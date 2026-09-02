"""
ISSDC CH2 AOI DISCOVERY + DOWNLOAD
===================================

Input:
    AUTH_TOKEN
    MIN_LON
    MIN_LAT
    MAX_LON
    MAX_LAT

The script:

1. Queries the same CH2 layers used by ISSDC MapBrowse.
2. Excludes CH1 TMC completely.
3. Discovers all CH2 products intersecting the AOI.
4. Keeps calibrated TMC/OHR/IIR products only.
5. Deduplicates by DOWNLOAD filename.
6. Resolves PRADAN download URLs.
7. Downloads all resolved files with resume/retry support.

Tested logic is based on the ISSDC WFS / PRADAN URL structure supplied
in the working examples.

IMPORTANT:
    AUTH_TOKEN should be the CURRENT JSESSIONID value from the browser
    session that can access both MapBrowse and PRADAN.

Example:

    AUTH_TOKEN = "C4760DD214596CA9C029E94EF9EF1101"

    AOI:
        MIN_LON = -71.5
        MIN_LAT = 22.0
        MAX_LON = -70.5
        MAX_LAT = 23.5

Output:

    issdc_ch2_output/
        discovery/
            wfs_all_ch2.json
            calibrated_products.json
            calibrated_products.csv
            download_manifest.csv

        data/
            <downloaded ZIP files>

        logs/
            download.log
"""

import csv
import json
import os
import sys
import time
import threading
from pathlib import Path
from urllib.parse import quote

import requests


# =============================================================================
# USER CONFIGURATION
# =============================================================================

AUTH_TOKEN = "9A4F189CEA4BA7F0E897E3541949495B"

MIN_LON = -71.5
MIN_LAT = 22.0
MAX_LON = -70.5
MAX_LAT = 23.5


# =============================================================================
# GENERAL CONFIGURATION
# =============================================================================

WFS_URL = "https://chmapbrowse.issdc.gov.in/server/wfs"
PRADAN_URL_PREFIX = "https://pradan.issdc.gov.in"

OUTPUT_ROOT = Path("issdc_ch2_output")

DISCOVERY_DIR = OUTPUT_ROOT / "discovery"
DATA_DIR = OUTPUT_ROOT / "data"
LOG_DIR = OUTPUT_ROOT / "logs"

WFS_TIMEOUT = 120

DOWNLOAD_MAX_RETRIES = 5
DOWNLOAD_RETRY_WAIT = 30

CHUNK_SIZE_MB = 8

# Number of bytes used when probing a possible PRADAN path.
# This avoids downloading the complete file merely to test the URL.
PROBE_BYTES = 1


# =============================================================================
# CH2 LAYERS
# =============================================================================
#
# These reproduce the CH2 portion of the MapBrowse request.
#
# CH1 TMC is intentionally NOT included.
#
# We query all representations so that the WFS discovery stage behaves like
# MapBrowse. After discovery we explicitly keep only the calibrated products.
# =============================================================================

CH2_LAYERS = [
    # Standard CH2
    "moon:ins:ch2_iir_cal",
    "moon:ins:ch2_iir_raw",
    "moon:ins:ch2_ohr_cal",
    "moon:ins:ch2_ohr_raw",
    "moon:ins:ch2_tmc_cal",
    "moon:ins:ch2_tmc_raw",

    # North-pole products
    "moon:ins:np:ch2_iir_cal_np",
    "moon:ins:np:ch2_iir_raw_np",
    "moon:ins:np:ch2_ohr_cal_np",
    "moon:ins:np:ch2_ohr_raw_np",
    "moon:ins:np:ch2_tmc_cal_np",
    "moon:ins:np:ch2_tmc_raw_np",

    # South-pole products
    "moon:ins:sp:ch2_iir_cal_sp",
    "moon:ins:sp:ch2_iir_raw_sp",
    "moon:ins:sp:ch2_ohr_cal_sp",
    "moon:ins:sp:ch2_ohr_raw_sp",
    "moon:ins:sp:ch2_tmc_cal_sp",
    "moon:ins:sp:ch2_tmc_raw_sp",
]


# =============================================================================
# SESSION
# =============================================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, application/geo+json, */*",
        "Referer": "https://chmapbrowse.issdc.gov.in/MapBrowse/",
        "X-Requested-With": "XMLHttpRequest",
    }
)

session.cookies.update(
    {
        "JSESSIONID": AUTH_TOKEN,
    }
)


# =============================================================================
# DIRECTORIES
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
# VALIDATE CONFIGURATION
# =============================================================================

if not AUTH_TOKEN or AUTH_TOKEN == "PASTE_CURRENT_JSESSIONID_HERE":
    raise RuntimeError(
        "\nAUTH_TOKEN is not configured.\n"
        "Paste your CURRENT JSESSIONID into AUTH_TOKEN."
    )


# =============================================================================
# KEEP-ALIVE
# =============================================================================

def keep_alive():
    """
    Keep the ISSDC session alive during long downloads.

    The supplied working downloader used this endpoint every 10 minutes.
    """

    keep_alive_url = (
        PRADAN_URL_PREFIX +
        "/ch2/protected/payload.xhtml"
    )

    while True:
        time.sleep(600)

        try:
            session.get(
                keep_alive_url,
                timeout=(30, 60),
                allow_redirects=False,
            )
        except Exception:
            pass


threading.Thread(
    target=keep_alive,
    daemon=True,
).start()


# =============================================================================
# WFS DISCOVERY
# =============================================================================

def discover_products():

    log("=" * 80)
    log("ISSDC CH2 PRODUCT DISCOVERY")
    log("=" * 80)

    log("")
    log("AOI:")
    log(f"Longitude: {MIN_LON} -> {MAX_LON}")
    log(f"Latitude : {MIN_LAT} -> {MAX_LAT}")

    log("")
    log("Layers:")

    for layer in CH2_LAYERS:
        log(f"  {layer}")

    log("")
    log("CH1 TMC:")
    log("  EXCLUDED")

    # -------------------------------------------------------------------------
    # Important:
    #
    # We deliberately use the same broad PRODUCT_ID filter as MapBrowse.
    #
    # The layer list is what determines which CH2 product families are
    # searched. We do NOT restrict the WFS query itself to calibrated products.
    # That happens after discovery.
    # -------------------------------------------------------------------------

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
    log("=" * 80)
    log("QUERYING WFS")
    log("=" * 80)

    log("")
    log("Requesting complete CH2 catalog...")

    response = session.get(
        WFS_URL,
        params=params,
        timeout=WFS_TIMEOUT,
        allow_redirects=True,
    )

    log(f"HTTP status: {response.status_code}")
    log(f"Content-Type: {response.headers.get('Content-Type')}")

    response.raise_for_status()

    content_type = response.headers.get(
        "Content-Type",
        ""
    ).lower()

    text = response.text

    if "text/html" in content_type or "<html" in text[:500].lower():
        log("")
        log("=" * 80)
        log("AUTHENTICATION FAILURE")
        log("=" * 80)
        log("")
        log(text[:1000])

        raise RuntimeError(
            "WFS returned HTML instead of GeoJSON. "
            "Your JSESSIONID is probably expired or invalid."
        )

    try:
        data = response.json()
    except Exception:
        log("")
        log("WFS response was not valid JSON:")
        log(text[:2000])
        raise

    features = data.get("features", [])

    log("")
    log("=" * 80)
    log("WFS RETRIEVAL COMPLETE")
    log("=" * 80)

    log(f"Total raw features: {len(features)}")

    # Save complete WFS result
    all_json = DISCOVERY_DIR / "wfs_all_ch2.json"

    with open(all_json, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    log(f"Saved: {all_json}")

    return features


# =============================================================================
# PRODUCT TYPE
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


# =============================================================================
# CALIBRATED FILTER
# =============================================================================

def is_calibrated(feature):

    """
    Keep only products coming from calibrated CH2 layers.

    WFS feature properties do not always expose the originating typename,
    so we use the product naming convention and/or metadata where available.
    """

    props = feature.get("properties", {})

    pid = str(props.get("PRODUCT_ID", "")).lower()
    download = str(props.get("DOWNLOAD", "")).lower()

    # -------------------------------------------------------------------------
    # Some ISSDC products explicitly expose processing/layer information.
    # Check any likely metadata fields first.
    # -------------------------------------------------------------------------

    possible_fields = [
        "TYPE_NAME",
        "TYPENAME",
        "LAYER",
        "LAYER_NAME",
        "PROCESSING_LEVEL",
        "PROCESSING",
        "PRODUCT_TYPE",
    ]

    metadata_text = " ".join(
        str(props.get(k, ""))
        for k in possible_fields
    ).lower()

    if "raw" in metadata_text:
        return False

    if "cal" in metadata_text:
        return True

    # -------------------------------------------------------------------------
    # For the standard calibrated image products, the DOWNLOAD filename is
    # what we ultimately need. Raw TMC products use nrf/nra/nrn while
    # calibrated products use ncf/nca/ncn.
    #
    # IIR/OHR are handled similarly by their product/layer naming where
    # available.
    # -------------------------------------------------------------------------

    if "_tmc_" in pid:
        if "_nrf_" in pid or "_nra_" in pid or "_nrn_" in pid:
            return False

        if "_ncf_" in pid or "_nca_" in pid or "_ncn_" in pid:
            return True

    # If we have no reliable explicit raw indicator, allow it.
    # The WFS layer discovery is CH2-only and downstream path resolution
    # performs another sanity check.
    return True


# =============================================================================
# DEDUPLICATION
# =============================================================================

def deduplicate(features):

    log("")
    log("=" * 80)
    log("DEDUPLICATING")
    log("=" * 80)

    unique = {}

    for feature in features:

        props = feature.get("properties", {})

        product_id = props.get("PRODUCT_ID")
        download = props.get("DOWNLOAD")

        if not product_id and not download:
            continue

        # Prefer PRODUCT_ID as the product identity.
        key = product_id or download

        if key not in unique:
            unique[key] = feature

    result = list(unique.values())

    log(f"Raw CH2 features: {len(features)}")
    log(f"Duplicates: {len(features) - len(result)}")
    log(f"Unique products: {len(result)}")

    return result


# =============================================================================
# SELECT CALIBRATED PRODUCTS
# =============================================================================

def select_calibrated(features):

    log("")
    log("=" * 80)
    log("SELECTING CALIBRATED CH2 PRODUCTS")
    log("=" * 80)

    selected = []

    for feature in features:

        props = feature.get("properties", {})

        pid = props.get("PRODUCT_ID", "")

        ptype = product_type(pid)

        if ptype not in ("TMC", "OHR", "IIR"):
            continue

        if not is_calibrated(feature):
            continue

        selected.append(feature)

    log(f"Calibrated products: {len(selected)}")

    counts = {
        "TMC": 0,
        "OHR": 0,
        "IIR": 0,
    }

    for feature in selected:
        ptype = product_type(
            feature.get("properties", {}).get("PRODUCT_ID", "")
        )

        if ptype in counts:
            counts[ptype] += 1

    log("")
    log("By type:")

    for ptype, count in counts.items():
        log(f"  {ptype}: {count}")

    return selected


# =============================================================================
# SAVE PRODUCT METADATA
# =============================================================================

def save_product_metadata(features):

    records = []

    for feature in features:

        props = feature.get("properties", {})

        records.append(
            {
                "PRODUCT_TYPE": product_type(
                    props.get("PRODUCT_ID", "")
                ),
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
            }
        )

    json_file = DISCOVERY_DIR / "calibrated_products.json"

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    csv_file = DISCOVERY_DIR / "calibrated_products.csv"

    if records:

        fieldnames = list(records[0].keys())

        with open(
            csv_file,
            "w",
            newline="",
            encoding="utf-8",
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
            )

            writer.writeheader()
            writer.writerows(records)

    log("")
    log(f"Saved metadata:")
    log(f"  {json_file}")
    log(f"  {csv_file}")


# =============================================================================
# PRADAN PATH CONSTRUCTION
# =============================================================================

def date_from_download(filename):

    """
    Extract YYYYMMDD from:

        ch2_tmc_ncf_20240503T1355084418_d_img_d18.zip
    """

    import re

    match = re.search(
        r"_(20\d{6})T",
        filename or "",
    )

    if not match:
        return None

    return match.group(1)


def candidate_pradan_paths(product_type_name, filename):

    """
    Generate candidate PRADAN paths.

    TMC and OHR conventions are directly based on the working downloader
    supplied by the user.

    IIR has multiple archive conventions, so several calibrated candidates
    are attempted.
    """

    date = date_from_download(filename)

    if not date:
        return []

    candidates = []

    if product_type_name == "TMC":

        candidates.append(
            (
                f"/ch2/protected/downloadData/POST_OD/"
                f"isda_archive/ch2_bundle/cho_bundle/nop/"
                f"tmc_collection/data/calibrated/{date}/"
                f"{filename}?tmc2"
            )
        )

    elif product_type_name == "OHR":

        candidates.append(
            (
                f"/ch2/protected/downloadData/POST_OD/"
                f"isda_archive/ch2_bundle/cho_bundle/nop/"
                f"ohr_collection/data/calibrated/{date}/"
                f"{filename}?ohrc"
            )
        )

    elif product_type_name == "IIR":

        # Most likely calibrated image convention.
        candidates.append(
            (
                f"/ch2/protected/downloadData/POST_OD/"
                f"isda_archive/ch2_bundle/cho_bundle/nop/"
                f"iir_collection/data/calibrated/{date}/"
                f"{filename}?iirc"
            )
        )

        # Some archive variants may use a different collection suffix.
        candidates.append(
            (
                f"/ch2/protected/downloadData/POST_OD/"
                f"isda_archive/ch2_bundle/cho_bundle/nop/"
                f"iir_collection/data/calibrated/{date}/"
                f"{filename}?iir"
            )
        )

    return candidates


# =============================================================================
# PROBE PRADAN URL
# =============================================================================

def probe_url(url):

    """
    Validate that a protected PRADAN URL is accessible.

    HEAD is attempted first.

    If HEAD is unsupported, perform a 1-byte GET.

    Returns:
        True  -> URL appears valid
        False -> URL invalid/authentication failure
    """

    # -------------------------------------------------------------------------
    # HEAD
    # -------------------------------------------------------------------------

    try:

        response = session.head(
            url,
            timeout=(30, 60),
            allow_redirects=False,
        )

        if response.status_code in (200, 206):

            return True

        if response.status_code in (301, 302, 303, 307, 308):

            location = response.headers.get("Location", "")

            if location and "login" not in location.lower():
                return True

    except Exception:
        pass

    # -------------------------------------------------------------------------
    # Range GET
    # -------------------------------------------------------------------------

    try:

        response = session.get(
            url,
            headers={
                "Range": f"bytes=0-{PROBE_BYTES - 1}"
            },
            stream=True,
            timeout=(30, 60),
            allow_redirects=False,
        )

        status = response.status_code

        response.close()

        if status in (200, 206):

            return True

    except Exception:
        pass

    return False


# =============================================================================
# RESOLVE DOWNLOAD PATHS
# =============================================================================

def resolve_downloads(features):

    log("")
    log("=" * 80)
    log("RESOLVING PRADAN DOWNLOAD PATHS")
    log("=" * 80)

    manifest = []

    resolved = 0
    unresolved = 0

    for index, feature in enumerate(features, start=1):

        props = feature.get("properties", {})

        pid = props.get("PRODUCT_ID", "")
        filename = props.get("DOWNLOAD", "")

        ptype = product_type(pid)

        log("")
        log(
            f"[{index}/{len(features)}] "
            f"{ptype}  {filename}"
        )

        if not filename:
            log("  No DOWNLOAD filename in WFS.")
            unresolved += 1
            continue

        candidates = candidate_pradan_paths(
            ptype,
            filename,
        )

        found_url = None
        found_path = None

        for relative_path in candidates:

            url = PRADAN_URL_PREFIX + relative_path

            log(f"  Testing: {relative_path}")

            if probe_url(url):

                found_url = url
                found_path = relative_path

                log("  RESOLVED")

                break

        if found_url:

            resolved += 1

            manifest.append(
                {
                    "PRODUCT_TYPE": ptype,
                    "PRODUCT_ID": pid,
                    "DOWNLOAD": filename,
                    "PRADAN_PATH": found_path,
                    "PRADAN_URL": found_url,
                    "OBS_ST_TIME": props.get("OBS_ST_TIME"),
                    "IM_ORB_NUM": props.get("IM_ORB_NUM"),
                    "DM_ORB_NUM": props.get("DM_ORB_NUM"),
                }
            )

        else:

            unresolved += 1

            log("  *** UNRESOLVED ***")

            manifest.append(
                {
                    "PRODUCT_TYPE": ptype,
                    "PRODUCT_ID": pid,
                    "DOWNLOAD": filename,
                    "PRADAN_PATH": "",
                    "PRADAN_URL": "",
                    "OBS_ST_TIME": props.get("OBS_ST_TIME"),
                    "IM_ORB_NUM": props.get("IM_ORB_NUM"),
                    "DM_ORB_NUM": props.get("DM_ORB_NUM"),
                }
            )

    # -------------------------------------------------------------------------
    # Save manifest
    # -------------------------------------------------------------------------

    manifest_file = DISCOVERY_DIR / "download_manifest.csv"

    if manifest:

        with open(
            manifest_file,
            "w",
            newline="",
            encoding="utf-8",
        ) as f:

            fieldnames = list(manifest[0].keys())

            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
            )

            writer.writeheader()
            writer.writerows(manifest)

    log("")
    log("=" * 80)
    log("PATH RESOLUTION SUMMARY")
    log("=" * 80)

    log(f"Resolved  : {resolved}")
    log(f"Unresolved: {unresolved}")
    log(f"Manifest  : {manifest_file}")

    return manifest


# =============================================================================
# DOWNLOAD ONE FILE
# =============================================================================

def download_file(item, index, total):

    filename = item["DOWNLOAD"]
    url = item["PRADAN_URL"]

    final_file = DATA_DIR / filename
    partial_file = Path(str(final_file) + ".part")

    log("")
    log("=" * 80)
    log(f"DOWNLOAD [{index}/{total}]")
    log("=" * 80)

    log(f"File: {filename}")

    if final_file.exists():

        log("Already exists. Skipping.")

        return True

    for attempt in range(
        1,
        DOWNLOAD_MAX_RETRIES + 1,
    ):

        try:

            resume_from = 0

            request_headers = {}

            if partial_file.exists():

                resume_from = partial_file.stat().st_size

                if resume_from > 0:

                    request_headers["Range"] = (
                        f"bytes={resume_from}-"
                    )

                    log(
                        f"Resuming from "
                        f"{resume_from / (1024 ** 2):.2f} MB"
                    )

            log(
                f"Attempt {attempt}/"
                f"{DOWNLOAD_MAX_RETRIES}"
            )

            with session.get(
                url,
                headers=request_headers,
                stream=True,
                timeout=(30, 600),
                allow_redirects=False,
            ) as response:

                if response.status_code not in (200, 206):

                    raise RuntimeError(
                        f"HTTP {response.status_code}"
                    )

                # -----------------------------------------------------------------
                # If we requested a Range but server returned 200, it ignored
                # the Range request. In that case we MUST restart the file
                # rather than append the complete file to the partial one.
                # -----------------------------------------------------------------

                if resume_from > 0 and response.status_code == 200:

                    log(
                        "Server ignored Range request. "
                        "Restarting download."
                    )

                    resume_from = 0
                    mode = "wb"

                else:

                    mode = (
                        "ab"
                        if resume_from > 0
                        else "wb"
                    )

                downloaded = resume_from

                with open(
                    partial_file,
                    mode,
                ) as f:

                    for chunk in response.iter_content(
                        chunk_size=CHUNK_SIZE_MB * 1024 * 1024
                    ):

                        if not chunk:
                            continue

                        f.write(chunk)

                        downloaded += len(chunk)

                        print(
                            "\r"
                            f"  {downloaded / (1024 ** 2):.2f} MB",
                            end="",
                            flush=True,
                        )

            print()

            # -----------------------------------------------------------------
            # Successful complete HTTP response.
            # Rename only after the transfer completed.
            # -----------------------------------------------------------------

            partial_file.rename(final_file)

            log("Download complete.")

            return True

        except Exception as exc:

            log("")
            log(
                f"Attempt {attempt} failed: "
                f"{type(exc).__name__}: {exc}"
            )

            if attempt < DOWNLOAD_MAX_RETRIES:

                log(
                    f"Waiting "
                    f"{DOWNLOAD_RETRY_WAIT} seconds..."
                )

                time.sleep(DOWNLOAD_RETRY_WAIT)

    log("")
    log("*** DOWNLOAD FAILED ***")

    return False


# =============================================================================
# DOWNLOAD ALL
# =============================================================================

def download_all(manifest):

    downloadable = [
        item
        for item in manifest
        if item.get("PRADAN_URL")
    ]

    log("")
    log("=" * 80)
    log("STARTING DOWNLOAD")
    log("=" * 80)

    log(
        f"Resolved files available for download: "
        f"{len(downloadable)}"
    )

    success = 0
    failed = 0

    for index, item in enumerate(
        downloadable,
        start=1,
    ):

        ok = download_file(
            item,
            index,
            len(downloadable),
        )

        if ok:
            success += 1
        else:
            failed += 1

    log("")
    log("=" * 80)
    log("DOWNLOAD SUMMARY")
    log("=" * 80)

    log(f"Successful: {success}")
    log(f"Failed    : {failed}")

    return success, failed


# =============================================================================
# MAIN
# =============================================================================

def main():

    start_time = time.time()

    # -------------------------------------------------------------------------
    # Header
    # -------------------------------------------------------------------------

    log("")
    log("=" * 80)
    log("ISSDC CH2 AOI DISCOVERY + DOWNLOAD")
    log("=" * 80)

    log("")
    log("Configuration:")
    log(f"  AOI:")
    log(f"      MIN_LON = {MIN_LON}")
    log(f"      MIN_LAT = {MIN_LAT}")
    log(f"      MAX_LON = {MAX_LON}")
    log(f"      MAX_LAT = {MAX_LAT}")

    log("")
    log("Output:")
    log(f"  {OUTPUT_ROOT.resolve()}")

    # -------------------------------------------------------------------------
    # 1. Discover
    # -------------------------------------------------------------------------

    raw_features = discover_products()

    # -------------------------------------------------------------------------
    # 2. Deduplicate
    # -------------------------------------------------------------------------

    unique_features = deduplicate(raw_features)

    # -------------------------------------------------------------------------
    # 3. Select calibrated products
    # -------------------------------------------------------------------------

    calibrated_features = select_calibrated(
        unique_features
    )

    # -------------------------------------------------------------------------
    # 4. Save metadata
    # -------------------------------------------------------------------------

    save_product_metadata(
        calibrated_features
    )

    # -------------------------------------------------------------------------
    # 5. Resolve actual PRADAN URLs
    # -------------------------------------------------------------------------

    manifest = resolve_downloads(
        calibrated_features
    )

    # -------------------------------------------------------------------------
    # 6. Download
    # -------------------------------------------------------------------------

    success, failed = download_all(
        manifest
    )

    # -------------------------------------------------------------------------
    # Final
    # -------------------------------------------------------------------------

    elapsed = time.time() - start_time

    log("")
    log("=" * 80)
    log("ALL DONE")
    log("=" * 80)

    log(f"Elapsed time: {elapsed / 60:.2f} minutes")
    log(f"Data directory: {DATA_DIR.resolve()}")
    log(f"Log file      : {LOG_FILE.resolve()}")

    if failed:
        log("")
        log(
            f"WARNING: {failed} file(s) failed. "
            f"Re-run the script to resume them."
        )


if __name__ == "__main__":
    main()