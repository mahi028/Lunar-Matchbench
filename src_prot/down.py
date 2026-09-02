"""
ISSDC CH2 AOI DISCOVERY + DOWNLOAD
===================================

Auth is now fully automated: credentials come from .env (PRADAN_USERNAME /
PRADAN_PASSWORD) and login happens against Keycloak SSO programmatically.
Since chmapbrowse.issdc.gov.in and pradan.issdc.gov.in are separate app
sessions (see prior note), each gets its OWN IssdcSession that logs in
independently and RE-LOGS-IN AUTOMATICALLY whenever a request comes back
looking unauthenticated -- important since these tokens are short-lived
and a long download run will outlive at least one of them.

AOI:
    MIN_LON, MIN_LAT, MAX_LON, MAX_LAT below.

Output:
    issdc_ch2_output/
        discovery/  (wfs_all_ch2.json, calibrated_products.{json,csv}, download_manifest.csv)
        data/       (downloaded ZIPs)
        logs/       (download.log)
"""

import csv
import html
import json
import os
import re
import sys
import time
import threading
from pathlib import Path

import requests


# =============================================================================
# USER CONFIGURATION
# =============================================================================

MIN_LON = -71.25
MIN_LAT = 22.375
MAX_LON = -70.75
MAX_LAT = 23.125


# =============================================================================
# .ENV LOADING
# =============================================================================

def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env loader (no external dependency). Skips keys already set
    in the real environment, same convention as python-dotenv."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv()

USERNAME = os.environ.get("PRADAN_USERNAME")
PASSWORD = os.environ.get("PRADAN_PASSWORD")

if not USERNAME or not PASSWORD:
    raise SystemExit(
        "PRADAN_USERNAME / PRADAN_PASSWORD not found. "
        "Add them to a .env file in the working directory."
    )


# =============================================================================
# GENERAL CONFIGURATION
# =============================================================================

WFS_URL = "https://chmapbrowse.issdc.gov.in/server/wfs"
PRADAN_URL_PREFIX = "https://pradan.issdc.gov.in"

# "Trigger" URLs -- protected pages that force a login redirect if the
# session is missing/expired. Used both for the initial login and for
# detecting/recovering from mid-run session expiry.
PRADAN_TRIGGER_URL = PRADAN_URL_PREFIX + "/ch2/protected/payload.xhtml"
WFS_TRIGGER_URL = "https://chmapbrowse.issdc.gov.in/MapBrowse/"

OUTPUT_ROOT = Path("issdc_ch2_output")
DISCOVERY_DIR = OUTPUT_ROOT / "discovery"
DATA_DIR = OUTPUT_ROOT / "data"
LOG_DIR = OUTPUT_ROOT / "logs"

WFS_TIMEOUT = 120
DOWNLOAD_MAX_RETRIES = 5
DOWNLOAD_RETRY_WAIT = 30
CHUNK_SIZE_MB = 8
PROBE_BYTES = 1
# Seconds to sleep between each probe request -- prevents PRADAN's rate-limiter
# (observed to kick in after ~500 rapid-fire requests) from returning 403.
PROBE_INTER_DELAY = 0.5
# Extra sleep (seconds) when a probe returns 403 -- signals server-side throttle,
# back off before trying the next candidate/item.
PROBE_THROTTLE_BACKOFF = 60
# Seconds to wait after the full probe pass before starting the download phase --
# lets the server's rate-limit window reset after hundreds of probes.
POST_PROBE_COOLDOWN = 30

CH2_LAYERS = [
    "moon:ins:ch2_iir_cal", "moon:ins:ch2_iir_raw",
    "moon:ins:ch2_ohr_cal", "moon:ins:ch2_ohr_raw",
    "moon:ins:ch2_tmc_cal", "moon:ins:ch2_tmc_raw",
    "moon:ins:np:ch2_iir_cal_np", "moon:ins:np:ch2_iir_raw_np",
    "moon:ins:np:ch2_ohr_cal_np", "moon:ins:np:ch2_ohr_raw_np",
    "moon:ins:np:ch2_tmc_cal_np", "moon:ins:np:ch2_tmc_raw_np",
    "moon:ins:sp:ch2_iir_cal_sp", "moon:ins:sp:ch2_iir_raw_sp",
    "moon:ins:sp:ch2_ohr_cal_sp", "moon:ins:sp:ch2_ohr_raw_sp",
    "moon:ins:sp:ch2_tmc_cal_sp", "moon:ins:sp:ch2_tmc_raw_sp",
]


# =============================================================================
# KEYCLOAK LOGIN AUTOMATION
# =============================================================================

_LOGIN_FORM_RE = re.compile(
    r'<form[^>]*id="kc-form-login".*?</form>', re.IGNORECASE | re.DOTALL
)
_FALLBACK_FORM_RE = re.compile(
    r'<form[^>]*action="([^"]*login-actions/authenticate[^"]*)"[^>]*>.*?</form>',
    re.IGNORECASE | re.DOTALL,
)
_ACTION_RE = re.compile(r'action="([^"]+)"', re.IGNORECASE)
_INPUT_RE = re.compile(r'<input[^>]+name="([^"]+)"[^>]*value="([^"]*)"', re.IGNORECASE)


def extract_login_form(page_html: str):
    """Parse Keycloak's login form: action URL + any hidden fields it needs
    (CSRF-style tokens, credentialId, etc.) besides username/password."""
    match = _LOGIN_FORM_RE.search(page_html)
    if not match:
        match = _FALLBACK_FORM_RE.search(page_html)
    if not match:
        return None, {}

    form_html = match.group(0)
    action_match = _ACTION_RE.search(form_html)
    action = html.unescape(action_match.group(1)) if action_match else None

    fields = {}
    for name, value in _INPUT_RE.findall(form_html):
        fields[name] = html.unescape(value)

    return action, fields


def looks_unauthenticated(response: requests.Response) -> bool:
    if "idp.issdc.gov.in" in response.url or "auth/realms" in response.url:
        return True
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" in content_type:
        snippet = response.text[:5000]
        if "kc-form-login" in snippet or "login-actions/authenticate" in snippet:
            return True
    return False


class IssdcSession:
    """Wraps a requests.Session for one ISSDC app, with automatic Keycloak
    login and automatic re-login if a request mid-run looks unauthenticated
    (handles short-lived tokens transparently)."""

    def __init__(self, trigger_url: str, referer: str, name: str):
        self.trigger_url = trigger_url
        self.name = name
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, application/geo+json, */*",
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        })
        self._login()

    def _login(self) -> None:
        r = self.session.get(self.trigger_url, allow_redirects=True, timeout=(30, 60))
        if not looks_unauthenticated(r):
            log(f"[{self.name}] already authenticated (or no login required).")
            return

        action, fields = extract_login_form(r.text)
        if not action:
            raise RuntimeError(
                f"[{self.name}] Could not find Keycloak login form on {r.url}. "
                "The IdP theme may differ from the default -- inspect the page HTML."
            )

        fields["username"] = USERNAME
        fields["password"] = PASSWORD

        r2 = self.session.post(action, data=fields, allow_redirects=True, timeout=(30, 60))
        if looks_unauthenticated(r2):
            raise RuntimeError(
                f"[{self.name}] Login failed -- still on the IdP login page after "
                "submitting credentials. Check PRADAN_USERNAME/PASSWORD, or the "
                "account may require a CAPTCHA/2FA that blocks automated login."
            )
        log(f"[{self.name}] login successful.")

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        timeout = kwargs.pop("timeout", (30, 60))
        response = self.session.request(method, url, timeout=timeout, **kwargs)
        if looks_unauthenticated(response):
            log(f"[{self.name}] session expired mid-run -- re-authenticating.")
            self._login()
            response = self.session.request(method, url, timeout=timeout, **kwargs)
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def head(self, url, **kwargs):
        return self.request("HEAD", url, **kwargs)


# =============================================================================
# DIRECTORIES / LOGGING
# =============================================================================

DISCOVERY_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "download.log"


def log(message=""):
    print(message)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(message + "\n")


# =============================================================================
# SESSIONS (created after log() exists, since login logs its outcome)
# =============================================================================

wfs_session = IssdcSession(WFS_TRIGGER_URL, WFS_TRIGGER_URL, name="chmapbrowse")
pradan_session = IssdcSession(PRADAN_TRIGGER_URL, PRADAN_TRIGGER_URL, name="pradan")


# =============================================================================
# KEEP-ALIVE (PRADAN session only -- that's the one used for long downloads).
# Also doubles as an early re-auth: if the token expired during the wait,
# .get() on IssdcSession will detect and refresh it before it's needed.
# =============================================================================

def keep_alive():
    while True:
        time.sleep(600)
        try:
            # allow_redirects=True is required so IssdcSession.request() follows
            # the login redirect and looks_unauthenticated() can detect expiry
            # and re-authenticate automatically.
            pradan_session.get(PRADAN_TRIGGER_URL, allow_redirects=True)
        except Exception:
            pass


threading.Thread(target=keep_alive, daemon=True).start()


# =============================================================================
# WFS DISCOVERY (uses wfs_session)
# =============================================================================

def discover_products():
    log("=" * 80)
    log("ISSDC CH2 PRODUCT DISCOVERY")
    log("=" * 80)
    log("")
    log("AOI:")
    log(f"Longitude: {MIN_LON} -> {MAX_LON}")
    log(f"Latitude : {MIN_LAT} -> {MAX_LAT}")

    cql_filter = (
        "(PRODUCT_ID LIKE '%tmc%' "
        "OR PRODUCT_ID LIKE '%ohr%' "
        "OR PRODUCT_ID LIKE '%iir%') "
        "AND "
        f"(BBOX(the_geom,{MIN_LON},{MIN_LAT},{MAX_LON},{MAX_LAT}))"
    )

    params = {
        "service": "wfs",
        "version": "2.0.0",
        "request": "GetFeature",
        "outputFormat": "application/json",
        "cql_filter": cql_filter,
        "typeName": ",".join(CH2_LAYERS),
    }

    log("")
    log("Requesting complete CH2 catalog...")

    response = wfs_session.get(WFS_URL, params=params, timeout=WFS_TIMEOUT, allow_redirects=True)

    log(f"HTTP status: {response.status_code}")
    log(f"Content-Type: {response.headers.get('Content-Type')}")
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    text = response.text

    if "text/html" in content_type or "<html" in text[:500].lower():
        log("")
        log("AUTHENTICATION FAILURE (chmapbrowse session)")
        log(text[:1000])
        raise RuntimeError(
            "WFS returned HTML instead of GeoJSON even after re-login. "
            "Check PRADAN_USERNAME/PASSWORD, or the account may need manual "
            "verification (CAPTCHA/2FA) that blocks automated login."
        )

    data = response.json()
    features = data.get("features", [])

    log(f"Total raw features: {len(features)}")

    all_json = DISCOVERY_DIR / "wfs_all_ch2.json"
    with open(all_json, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log(f"Saved: {all_json}")

    return features


# =============================================================================
# PRODUCT TYPE / CALIBRATED FILTER / DEDUPE  (unchanged from your version)
# =============================================================================

def product_type(product_id):
    pid = (product_id or "").lower()
    if "_tmc_" in pid:
        return "TMC"
    if "_ohr_" in pid:
        return "OHR"
    if "_iir_" in pid:
        return "IIR"
    return "UNKNOWN"


def is_calibrated(feature):
    props = feature.get("properties", {})
    pid = str(props.get("PRODUCT_ID", "")).lower()

    possible_fields = ["TYPE_NAME", "TYPENAME", "LAYER", "LAYER_NAME",
                        "PROCESSING_LEVEL", "PROCESSING", "PRODUCT_TYPE"]
    metadata_text = " ".join(str(props.get(k, "")) for k in possible_fields).lower()

    if "raw" in metadata_text:
        return False
    if "cal" in metadata_text:
        return True

    if "_tmc_" in pid:
        if "_nrf_" in pid or "_nra_" in pid or "_nrn_" in pid:
            return False
        if "_ncf_" in pid or "_nca_" in pid or "_ncn_" in pid:
            return True

    return True


def deduplicate(features):
    log("")
    log("DEDUPLICATING")
    unique = {}
    for feature in features:
        props = feature.get("properties", {})
        key = props.get("PRODUCT_ID") or props.get("DOWNLOAD")
        if not key:
            continue
        unique.setdefault(key, feature)
    result = list(unique.values())
    log(f"Raw CH2 features: {len(features)}  Unique: {len(result)}")
    return result


def select_calibrated(features):
    log("")
    log("SELECTING CALIBRATED CH2 PRODUCTS")
    selected = []
    for feature in features:
        pid = feature.get("properties", {}).get("PRODUCT_ID", "")
        ptype = product_type(pid)
        if ptype not in ("TMC", "OHR", "IIR"):
            continue
        if not is_calibrated(feature):
            continue
        selected.append(feature)

    counts = {"TMC": 0, "OHR": 0, "IIR": 0}
    for feature in selected:
        ptype = product_type(feature.get("properties", {}).get("PRODUCT_ID", ""))
        counts[ptype] = counts.get(ptype, 0) + 1
    log(f"Calibrated products: {len(selected)}  {counts}")
    return selected


def save_product_metadata(features):
    records = []
    for feature in features:
        props = feature.get("properties", {})
        records.append({
            "PRODUCT_TYPE": product_type(props.get("PRODUCT_ID", "")),
            "PRODUCT_ID": props.get("PRODUCT_ID"),
            "DOWNLOAD": props.get("DOWNLOAD"),
            "BROWSE": props.get("BROWSE"),
            "OBS_ST_TIME": props.get("OBS_ST_TIME"),
            "OBS_ED_TIME": props.get("OBS_ED_TIME"),
            "IM_ORB_NUM": props.get("IM_ORB_NUM"),
            "DM_ORB_NUM": props.get("DM_ORB_NUM"),
            "L0_ID": props.get("L0_ID"),
            "GAIN": props.get("GAIN"),
            "EXPOSURE": props.get("EXPOSURE"),
        })

    json_file = DISCOVERY_DIR / "calibrated_products.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    csv_file = DISCOVERY_DIR / "calibrated_products.csv"
    if records:
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)

    log(f"Saved metadata: {json_file}, {csv_file}")


# =============================================================================
# PRADAN PATH CONSTRUCTION
# =============================================================================

def date_from_download(filename):
    match = re.search(r"_(20\d{6})T", filename or "")
    return match.group(1) if match else None


def candidate_pradan_paths(product_type_name, filename):
    """
    NOTE: for IIR specifically, the known-working example paths you found
    manually were under data/derived/ with '_rfl_' filenames and a plain
    '?iirs' suffix -- not data/calibrated/ with '?iirc'/'?iir' as originally
    guessed here. The WFS DOWNLOAD field may not exactly match PRADAN's
    served filename for IIR derived products, so IIR resolution may still
    need manual verification even after the session fix. TMC/OHR paths
    match your working examples directly.
    """
    date = date_from_download(filename)
    if not date:
        return []

    candidates = []

    if product_type_name == "TMC":
        candidates.append(
            f"/ch2/protected/downloadData/POST_OD/isda_archive/ch2_bundle/"
            f"cho_bundle/nop/tmc_collection/data/calibrated/{date}/{filename}?tmc2"
        )

    elif product_type_name == "OHR":
        candidates.append(
            f"/ch2/protected/downloadData/POST_OD/isda_archive/ch2_bundle/"
            f"cho_bundle/nop/ohr_collection/data/calibrated/{date}/{filename}?ohrc"
        )

    elif product_type_name == "IIR":
        fn_lower = filename.lower()
        if "_nri_" in fn_lower:
            levels = ["raw", "calibrated", "derived"]
        elif "_ndi_" in fn_lower:
            levels = ["derived", "calibrated", "raw"]
        else:
            levels = ["calibrated", "raw", "derived"]

        for level in levels:
            candidates.append(
                f"/ch2/protected/downloadData/POST_OD/isda_archive/ch2_bundle/"
                f"cho_bundle/nop/iir_collection/data/{level}/{date}/{filename}?iirs"
            )

    return candidates


# =============================================================================
# PROBE / DOWNLOAD (use pradan_session)
# =============================================================================

def probe_url(url):
    """Returns (resolved: bool, last_status: str).
    last_status starts with 'THROTTLED' when the server returned 403,
    which the caller uses to trigger a longer backoff before continuing."""
    last_status = "no response"

    try:
        response = pradan_session.head(url, timeout=(10, 15), allow_redirects=False)
        last_status = f"HEAD {response.status_code}"
        if response.status_code in (200, 206):
            return True, last_status
        if response.status_code == 403:
            return False, f"THROTTLED HEAD 403"
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location", "")
            if location and "login" not in location.lower() and "idp.issdc" not in location.lower():
                return True, last_status
    except Exception as e:
        last_status = f"HEAD error: {type(e).__name__}"

    try:
        response = pradan_session.get(
            url, headers={"Range": f"bytes=0-{PROBE_BYTES - 1}"},
            stream=True, timeout=(10, 15), allow_redirects=False,
        )
        status = response.status_code
        response.close()
        last_status = f"GET {status}"
        if status in (200, 206):
            return True, last_status
        if status == 403:
            return False, f"THROTTLED GET 403"
    except Exception as e:
        last_status = f"GET error: {type(e).__name__}"

    return False, last_status


# Temporary sanity-check limit: only download this many resolved files per
# payload type (TMC/OHR/IIR), instead of everything. Set to None to download
# everything resolved.
SANITY_CHECK_LIMIT_PER_TYPE = 1

# Payload types to skip probing (leave empty to probe all discovered types)
SKIP_TYPES = set()


def resolve_downloads(features):
    log("")
    log("RESOLVING PRADAN DOWNLOAD PATHS")
    manifest = []
    resolved = 0
    unresolved = 0
    resolved_per_type = {}
    wanted_types = {
        product_type(f.get("properties", {}).get("PRODUCT_ID", ""))
        for f in features
    } & {"TMC", "OHR", "IIR"}

    manifest_file = DISCOVERY_DIR / "download_manifest.csv"
    fieldnames = ["PRODUCT_TYPE", "PRODUCT_ID", "DOWNLOAD", "PRADAN_PATH",
                  "PRADAN_URL", "OBS_ST_TIME", "IM_ORB_NUM", "DM_ORB_NUM"]
    # Open once and write incrementally -- if the run is interrupted, whatever
    # resolved so far is already on disk instead of lost.
    manifest_fh = open(manifest_file, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(manifest_fh, fieldnames=fieldnames)
    writer.writeheader()

    for index, feature in enumerate(features, start=1):
        props = feature.get("properties", {})
        pid = props.get("PRODUCT_ID", "")
        filename = props.get("DOWNLOAD", "")
        ptype = product_type(pid)

        # Skip probing once this type already has enough resolved matches.
        # IIR strips in particular are wide enough that hundreds of
        # overlapping candidates exist even in a small AOI, and download_all()
        # only ever keeps SANITY_CHECK_LIMIT_PER_TYPE per type anyway -- so
        # probing the rest just burns PRADAN's rate-limit budget for nothing.
        if (SANITY_CHECK_LIMIT_PER_TYPE is not None
                and resolved_per_type.get(ptype, 0) >= SANITY_CHECK_LIMIT_PER_TYPE):
            continue

        log(f"[{index}/{len(features)}] {ptype}  {filename}")

        if not filename:
            unresolved += 1
            continue

        found_url, found_path = None, None
        last_status = "skipped"

        if ptype in SKIP_TYPES:
            log(f"  SKIPPED ({ptype} path pattern not yet confirmed)")
        else:
            for relative_path in candidate_pradan_paths(ptype, filename):
                url = PRADAN_URL_PREFIX + relative_path
                ok, last_status = probe_url(url)

                if last_status.startswith("THROTTLED"):
                    log(f"  [rate-limit] sleeping {PROBE_THROTTLE_BACKOFF}s then retrying...")
                    time.sleep(PROBE_THROTTLE_BACKOFF)
                    ok, last_status = probe_url(url)

                if ok:
                    found_url, found_path = url, relative_path
                    log("  RESOLVED")
                    break

                # Polite inter-probe delay to avoid triggering the rate-limiter
                time.sleep(PROBE_INTER_DELAY)

        entry = {
            "PRODUCT_TYPE": ptype, "PRODUCT_ID": pid, "DOWNLOAD": filename,
            "PRADAN_PATH": found_path or "", "PRADAN_URL": found_url or "",
            "OBS_ST_TIME": props.get("OBS_ST_TIME"),
            "IM_ORB_NUM": props.get("IM_ORB_NUM"), "DM_ORB_NUM": props.get("DM_ORB_NUM"),
        }
        manifest.append(entry)
        writer.writerow(entry)
        manifest_fh.flush()

        if found_url:
            resolved += 1
            resolved_per_type[ptype] = resolved_per_type.get(ptype, 0) + 1
        elif ptype not in SKIP_TYPES:
            unresolved += 1
            log(f"  *** UNRESOLVED *** (last: {last_status})")
        else:
            unresolved += 1

        if (SANITY_CHECK_LIMIT_PER_TYPE is not None
                and wanted_types
                and all(resolved_per_type.get(t, 0) >= SANITY_CHECK_LIMIT_PER_TYPE
                        for t in wanted_types)):
            log("All instrument types resolved to the sanity-check limit -- stopping early.")
            break

        # Inter-item delay (on top of per-candidate delay above)
        time.sleep(PROBE_INTER_DELAY)

    manifest_fh.close()
    log(f"Resolved: {resolved}  Unresolved/skipped: {unresolved}  Manifest: {manifest_file}")
    return manifest


def download_file(item, index, total):
    filename = item["DOWNLOAD"]
    url = item["PRADAN_URL"]
    final_file = DATA_DIR / filename
    partial_file = Path(str(final_file) + ".part")

    log(f"DOWNLOAD [{index}/{total}] {filename}")

    if final_file.exists():
        log("Already exists. Skipping.")
        return True

    for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
        try:
            # Proactively verify the session is still alive before each attempt.
            # After a long probing phase, the token may have expired even if the
            # keep-alive thread fired -- this guarantees a fresh session cookie.
            pradan_session.get(PRADAN_TRIGGER_URL, allow_redirects=True, timeout=(15, 30))

            resume_from = 0
            request_headers = {}
            if partial_file.exists():
                resume_from = partial_file.stat().st_size
                if resume_from > 0:
                    request_headers["Range"] = f"bytes={resume_from}-"

            # allow_redirects=True lets requests follow PRADAN's redirect chain
            # to the actual storage/CDN endpoint that serves the file bytes.
            with pradan_session.request(
                "GET", url, headers=request_headers, stream=True,
                timeout=(30, 600), allow_redirects=True,
            ) as response:
                if response.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {response.status_code}")

                if resume_from > 0 and response.status_code == 200:
                    log("Server ignored Range request. Restarting download.")
                    resume_from = 0
                    mode = "wb"
                else:
                    mode = "ab" if resume_from > 0 else "wb"

                downloaded = resume_from
                with open(partial_file, mode) as f:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE_MB * 1024 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        print(f"\r  {downloaded / (1024 ** 2):.2f} MB", end="", flush=True)

            print()
            partial_file.rename(final_file)
            log("Download complete.")
            return True

        except Exception as exc:
            log(f"Attempt {attempt} failed: {type(exc).__name__}: {exc}")
            if attempt < DOWNLOAD_MAX_RETRIES:
                time.sleep(DOWNLOAD_RETRY_WAIT)

    log("*** DOWNLOAD FAILED ***")
    return False


def download_all(manifest):
    downloadable = [item for item in manifest if item.get("PRADAN_URL")]

    if SANITY_CHECK_LIMIT_PER_TYPE is not None:
        limited = []
        seen_per_type = {}
        for item in downloadable:
            ptype = item.get("PRODUCT_TYPE", "UNKNOWN")
            count = seen_per_type.get(ptype, 0)
            if count < SANITY_CHECK_LIMIT_PER_TYPE:
                limited.append(item)
                seen_per_type[ptype] = count + 1
        log(f"Sanity-check mode: limiting to {SANITY_CHECK_LIMIT_PER_TYPE} file(s) per type "
            f"({len(downloadable)} resolved -> {len(limited)} selected).")
        downloadable = limited

    log(f"Resolved files available for download: {len(downloadable)}")

    success = failed = 0
    for index, item in enumerate(downloadable, start=1):
        if download_file(item, index, len(downloadable)):
            success += 1
        else:
            failed += 1

    log(f"Successful: {success}  Failed: {failed}")
    return success, failed


# =============================================================================
# MAIN
# =============================================================================

def main():
    start_time = time.time()
    download_only = "--download-only" in sys.argv

    log("")
    log("ISSDC CH2 AOI DISCOVERY + DOWNLOAD")

    if download_only:
        manifest_file = DISCOVERY_DIR / "download_manifest.csv"
        if not manifest_file.exists():
            raise SystemExit(f"--download-only given but {manifest_file} doesn't exist yet.")
        with open(manifest_file, newline="", encoding="utf-8") as f:
            manifest = list(csv.DictReader(f))
        log(f"Loaded {len(manifest)} manifest rows from {manifest_file} (skipping discovery/resolve).")
    else:
        log(f"AOI: lon {MIN_LON}->{MAX_LON}, lat {MIN_LAT}->{MAX_LAT}")
        log(f"Output: {OUTPUT_ROOT.resolve()}")
        raw_features = discover_products()
        unique_features = deduplicate(raw_features)
        calibrated_features = select_calibrated(unique_features)
        save_product_metadata(calibrated_features)
        manifest = resolve_downloads(calibrated_features)
        if POST_PROBE_COOLDOWN > 0:
            log(f"Probe phase complete. Cooling down {POST_PROBE_COOLDOWN}s before downloads...")
            time.sleep(POST_PROBE_COOLDOWN)

    success, failed = download_all(manifest)

    elapsed = time.time() - start_time
    log("")
    log(f"ALL DONE. Elapsed: {elapsed / 60:.2f} min")
    log(f"Data directory: {DATA_DIR.resolve()}")
    if failed:
        log(f"WARNING: {failed} file(s) failed. Re-run to resume them.")


if __name__ == "__main__":
    main()