"""
Lunar-MatchBench: On-demand ISRO ISSDC CH2 data fetch
=======================================================
When no local CH2 (TMC-2 / OHRC) product covers a requested coordinate,
discover and download one directly from ISRO's ISSDC archive -- the same
automated Keycloak login + WFS discovery + PRADAN download flow as
src_prot/down.py's whole-catalog batch script, adapted into a single
coordinate-scoped, callable function so the main pipeline can fetch exactly
the one product it needs instead of requiring pre-staged local data.

Requires PRADAN_USERNAME/PRADAN_PASSWORD (or PRADAN_USER/PRADAN_PASS) in the
environment or a .env file -- the user's own ISSDC/PRADAN account.
"""
from __future__ import annotations

import html
import os
import re
import time
from pathlib import Path
from typing import Callable

import requests

from lunar_matchbench.config import CH2_DATA_DIR

WFS_URL = "https://chmapbrowse.issdc.gov.in/server/wfs"
PRADAN_URL_PREFIX = "https://pradan.issdc.gov.in"
PRADAN_TRIGGER_URL = PRADAN_URL_PREFIX + "/ch2/protected/payload.xhtml"
WFS_TRIGGER_URL = "https://chmapbrowse.issdc.gov.in/MapBrowse/"

# General + polar-projected layer variants -- queried together and filtered
# by product type/calibration afterward, same as the original batch script.
CH2_LAYERS = {
    "tmc":  ["moon:ins:ch2_tmc_cal", "moon:ins:np:ch2_tmc_cal_np", "moon:ins:sp:ch2_tmc_cal_sp"],
    "ohrc": ["moon:ins:ch2_ohr_cal", "moon:ins:np:ch2_ohr_cal_np", "moon:ins:sp:ch2_ohr_cal_sp"],
}
PRODUCT_CODE = {"tmc": "TMC", "ohrc": "OHR"}

PROBE_INTER_DELAY = 0.5
PROBE_THROTTLE_BACKOFF = 60
DOWNLOAD_MAX_RETRIES = 5
DOWNLOAD_RETRY_WAIT = 30
CHUNK_SIZE = 8 * 1024 * 1024

ProgressCB = Callable[[str, object], None]


class Ch2FetchError(RuntimeError):
    """Raised for credential/login/query failures (vs. a plain 'nothing found')."""


def _get_credentials() -> tuple[str, str]:
    def _read():
        user = os.environ.get("PRADAN_USERNAME") or os.environ.get("PRADAN_USER")
        password = os.environ.get("PRADAN_PASSWORD") or os.environ.get("PRADAN_PASS")
        return user, password

    user, password = _read()
    if not user or not password:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        user, password = _read()
    if not user or not password:
        raise Ch2FetchError(
            "No ISSDC/PRADAN credentials found. Set PRADAN_USERNAME and "
            "PRADAN_PASSWORD (or PRADAN_USER / PRADAN_PASS) in a .env file "
            "to enable automatic CH2 data fetching."
        )
    return user, password


_LOGIN_FORM_RE = re.compile(r'<form[^>]*id="kc-form-login".*?</form>', re.IGNORECASE | re.DOTALL)
_FALLBACK_FORM_RE = re.compile(
    r'<form[^>]*action="([^"]*login-actions/authenticate[^"]*)"[^>]*>.*?</form>',
    re.IGNORECASE | re.DOTALL,
)
_ACTION_RE = re.compile(r'action="([^"]+)"', re.IGNORECASE)
_INPUT_RE = re.compile(r'<input[^>]+name="([^"]+)"[^>]*value="([^"]*)"', re.IGNORECASE)


def _extract_login_form(page_html: str):
    match = _LOGIN_FORM_RE.search(page_html) or _FALLBACK_FORM_RE.search(page_html)
    if not match:
        return None, {}
    form_html = match.group(0)
    action_match = _ACTION_RE.search(form_html)
    action = html.unescape(action_match.group(1)) if action_match else None
    fields = {name: html.unescape(value) for name, value in _INPUT_RE.findall(form_html)}
    return action, fields


def _looks_unauthenticated(response: requests.Response) -> bool:
    if "idp.issdc.gov.in" in response.url or "auth/realms" in response.url:
        return True
    if "text/html" in response.headers.get("Content-Type", "").lower():
        snippet = response.text[:5000]
        if "kc-form-login" in snippet or "login-actions/authenticate" in snippet:
            return True
    return False


class _IssdcSession:
    """A requests.Session with automatic Keycloak login + re-login on expiry."""

    def __init__(self, trigger_url: str, referer: str, name: str, username: str, password: str):
        self.trigger_url = trigger_url
        self.name = name
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, application/geo+json, */*",
            "Referer": referer,
        })
        self._login()

    def _login(self) -> None:
        r = self.session.get(self.trigger_url, allow_redirects=True, timeout=(30, 60))
        if not _looks_unauthenticated(r):
            return
        action, fields = _extract_login_form(r.text)
        if not action:
            raise Ch2FetchError(
                f"[{self.name}] Could not find the ISSDC Keycloak login form -- "
                "the login page layout may have changed."
            )
        fields["username"] = self.username
        fields["password"] = self.password
        r2 = self.session.post(action, data=fields, allow_redirects=True, timeout=(30, 60))
        if _looks_unauthenticated(r2):
            raise Ch2FetchError(
                f"[{self.name}] ISSDC login failed -- check PRADAN_USERNAME/PASSWORD, "
                "or the account may need manual CAPTCHA/2FA verification."
            )

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        timeout = kwargs.pop("timeout", (30, 60))
        response = self.session.request(method, url, timeout=timeout, **kwargs)
        if _looks_unauthenticated(response):
            self._login()
            response = self.session.request(method, url, timeout=timeout, **kwargs)
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def head(self, url, **kwargs):
        return self.request("HEAD", url, **kwargs)


def _product_type(product_id: str) -> str:
    pid = (product_id or "").lower()
    if "_tmc_" in pid:
        return "TMC"
    if "_ohr_" in pid:
        return "OHR"
    if "_iir_" in pid:
        return "IIR"
    return "UNKNOWN"


def _is_calibrated(feature: dict) -> bool:
    props = feature.get("properties", {})
    pid = str(props.get("PRODUCT_ID", "")).lower()
    fields = ["TYPE_NAME", "TYPENAME", "LAYER", "LAYER_NAME", "PROCESSING_LEVEL", "PROCESSING", "PRODUCT_TYPE"]
    metadata_text = " ".join(str(props.get(k, "")) for k in fields).lower()
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


def _date_from_download(filename: str) -> str | None:
    m = re.search(r"_(20\d{6})T", filename or "")
    return m.group(1) if m else None


def _candidate_pradan_paths(product_type_name: str, filename: str) -> list[str]:
    date = _date_from_download(filename)
    if not date:
        return []
    if product_type_name == "TMC":
        return [f"/ch2/protected/downloadData/POST_OD/isda_archive/ch2_bundle/"
                f"cho_bundle/nop/tmc_collection/data/calibrated/{date}/{filename}?tmc2"]
    if product_type_name == "OHR":
        return [f"/ch2/protected/downloadData/POST_OD/isda_archive/ch2_bundle/"
                f"cho_bundle/nop/ohr_collection/data/calibrated/{date}/{filename}?ohrc"]
    return []


def _probe_url(session: _IssdcSession, url: str) -> tuple[bool, bool]:
    """Returns (resolved, throttled)."""
    try:
        r = session.head(url, timeout=(10, 15), allow_redirects=False)
        if r.status_code in (200, 206):
            return True, False
        if r.status_code == 403:
            return False, True
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("Location", "")
            if loc and "login" not in loc.lower() and "idp.issdc" not in loc.lower():
                return True, False
    except Exception:
        pass
    try:
        r = session.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=(10, 15), allow_redirects=False)
        status = r.status_code
        r.close()
        return status in (200, 206), status == 403
    except Exception:
        return False, False


def _download_file(session: _IssdcSession, url: str, dest: Path, progress_cb: ProgressCB | None) -> bool:
    partial = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return True
    for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
        try:
            resume_from = partial.stat().st_size if partial.exists() else 0
            headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
            with session.request("GET", url, headers=headers, stream=True,
                                  timeout=(30, 600), allow_redirects=True) as r:
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code}")
                mode = "ab" if resume_from and r.status_code == 206 else "wb"
                downloaded = resume_from if mode == "ab" else 0
                with open(partial, mode) as f:
                    for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            progress_cb("downloading", downloaded)
            partial.rename(dest)
            return True
        except Exception:
            if attempt < DOWNLOAD_MAX_RETRIES:
                time.sleep(DOWNLOAD_RETRY_WAIT)
    return False


def fetch_ch2_product(
    lat: float, lon: float, instrument: str,
    bbox: float = 0.2, progress_cb: ProgressCB | None = None,
) -> Path | None:
    """
    Discover and download a calibrated CH2 product (TMC-2 or OHRC) covering
    (lat, lon) directly from ISRO's ISSDC archive. Returns the downloaded
    zip's path, or None if no matching/resolvable product was found (a
    normal "no coverage here" outcome, not an error). Raises Ch2FetchError
    for credential, login, or query failures.
    """
    username, password = _get_credentials()
    # ISSDC's WFS uses -180..180 (west negative); the rest of the pipeline
    # uses planetocentric 0-360 East.
    wfs_lon = lon - 360 if lon > 180 else lon

    wfs_session = _IssdcSession(WFS_TRIGGER_URL, WFS_TRIGGER_URL, "chmapbrowse", username, password)
    pradan_session = _IssdcSession(PRADAN_TRIGGER_URL, PRADAN_TRIGGER_URL, "pradan", username, password)

    layers = CH2_LAYERS.get(instrument)
    if not layers:
        return None
    code = PRODUCT_CODE[instrument]

    min_lon, max_lon = wfs_lon - bbox, wfs_lon + bbox
    min_lat, max_lat = lat - bbox, lat + bbox
    cql_filter = f"(BBOX(the_geom,{min_lon},{min_lat},{max_lon},{max_lat}))"
    params = {
        "service": "wfs", "version": "2.0.0", "request": "GetFeature",
        "outputFormat": "application/json", "cql_filter": cql_filter,
        "typeName": ",".join(layers),
    }
    if progress_cb:
        progress_cb("query", None)
    r = wfs_session.get(WFS_URL, params=params, timeout=120, allow_redirects=True)
    r.raise_for_status()
    if "text/html" in r.headers.get("Content-Type", "").lower():
        raise Ch2FetchError("ISSDC WFS returned HTML instead of GeoJSON -- login likely failed.")
    features = r.json().get("features", [])

    seen = set()
    candidates = []
    for feature in features:
        props = feature.get("properties", {})
        pid = props.get("PRODUCT_ID")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        if _product_type(pid) != code or not _is_calibrated(feature):
            continue
        candidates.append(feature)

    if progress_cb:
        progress_cb("resolve", len(candidates))
    if not candidates:
        return None

    for feature in candidates:
        props = feature.get("properties", {})
        filename = props.get("DOWNLOAD", "")
        if not filename:
            continue
        for relative_path in _candidate_pradan_paths(code, filename):
            url = PRADAN_URL_PREFIX + relative_path
            resolved, throttled = _probe_url(pradan_session, url)
            if throttled:
                time.sleep(PROBE_THROTTLE_BACKOFF)
                resolved, _ = _probe_url(pradan_session, url)
            if not resolved:
                time.sleep(PROBE_INTER_DELAY)
                continue
            dest = CH2_DATA_DIR / filename
            if progress_cb:
                progress_cb("download", filename)
            if _download_file(pradan_session, url, dest, progress_cb):
                return dest
        time.sleep(PROBE_INTER_DELAY)

    return None
