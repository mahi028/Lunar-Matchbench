"""
PRADAN (ISSDC) ROI-based bulk downloader for Chandrayaan-2 OHRC / TMC-2 / IIRS.

There is no documented public REST API for PRADAN, so this drives a real
browser session with Playwright (handles login, JSF/PrimeFaces AJAX,
session cookies, and file downloads without you having to fight ViewState
tokens by hand).

HOW TO GET THE SELECTORS BELOW
-------------------------------
The browse pages are behind login, so the exact element IDs/selectors
depend on the live authenticated DOM, which I can't inspect for you.
Fastest way to fill in the TODOs:

    pip install playwright --break-system-packages
    playwright install chromium
    playwright codegen https://pradan.issdc.gov.in/ch2/protected/browse.xhtml?id=ohrc

Log in, do ONE manual ROI search + bulk download in the recorder window.
It generates a script with the real selectors -- copy those into the
TODO spots below (or just use the recorded script directly and wrap the
per-payload logic in a loop like this one).

USAGE
-----
    python pradan_roi_download.py --min-lat -30 --max-lat -20 \\
        --min-lon 10 --max-lon 20 --out ./downloads \\
        --payloads ohrc tmc2 iirs
"""

import argparse
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, Page
from dotenv import load_dotenv
load_dotenv()

BASE_URL = "https://pradan.issdc.gov.in/ch2/protected/browse.xhtml"
# PRADAN uses Keycloak SSO (idp.issdc.gov.in) for login. Don't hardcode the
# IdP URL directly -- it embeds a one-time `state` param tied to a single
# auth attempt. Instead, navigate to a protected PRADAN page and let it
# redirect to the IdP naturally, which issues a fresh state each time.

PAYLOAD_IDS = {
    "ohrc": "ohrc",
    "tmc2": "tmc2",
    "iirs": "iirs",
}

# Default ROI (lunar coordinates). Override at the CLI with --min-lat etc.
MIN_LON = -71.5
MIN_LAT = 22.0
MAX_LON = -70.5
MAX_LAT = 23.5


def login(page: Page, username: str, password: str) -> None:
    # Hitting a protected PRADAN page (not logged in yet) triggers PRADAN's
    # own redirect to the Keycloak IdP with a fresh `state` token.
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")

    # Keycloak's default login theme uses these field IDs consistently --
    # should work as-is, but confirm via codegen if issdc has a custom theme.
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#kc-login")
    page.wait_for_load_state("networkidle")  # Keycloak redirects back to PRADAN with the auth code


def search_roi(page: Page, payload_key: str, min_lat: float, max_lat: float,
                min_lon: float, max_lon: float) -> None:
    url = f"{BASE_URL}?id={PAYLOAD_IDS[payload_key]}"
    page.goto(url)
    page.wait_for_load_state("networkidle")

    # TODO: replace with the real spatial-filter field selectors.
    # PRADAN's browse pages have a footprint/spatial search panel with
    # min/max lat & lon fields -- open it (may be a collapsible panel)
    # before filling.
    page.click("text=Spatial Search")  # placeholder, confirm via codegen
    page.fill("#minLat", str(min_lat))
    page.fill("#maxLat", str(max_lat))
    page.fill("#minLon", str(min_lon))
    page.fill("#maxLon", str(max_lon))
    page.click("#searchButton")  # placeholder
    page.wait_for_load_state("networkidle")
    time.sleep(1)  # let the PrimeFaces datatable AJAX settle


def select_all_and_bulk_download(page: Page, download_dir: Path, payload_key: str) -> None:
    # TODO: replace with real "select all" checkbox + bulk download button
    page.click("#selectAllCheckbox")
    time.sleep(0.5)

    with page.expect_download(timeout=0) as download_info:
        page.click("#bulkDownloadButton")
    download = download_info.value

    out_path = download_dir / payload_key / download.suggested_filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(str(out_path))
    print(f"[{payload_key}] saved -> {out_path}")


def run(args: argparse.Namespace) -> None:
    username = os.environ.get("PRADAN_USER")
    password = os.environ.get("PRADAN_PASS")
    if not username or not password:
        raise SystemExit(
            "Set PRADAN_USER and PRADAN_PASS environment variables "
            "(don't hardcode credentials in the script)."
        )

    download_dir = Path(args.out)
    download_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        login(page, username, password)

        for payload_key in args.payloads:
            print(f"--- {payload_key} ---")
            search_roi(page, payload_key, args.min_lat, args.max_lat,
                       args.min_lon, args.max_lon)
            select_all_and_bulk_download(page, download_dir, payload_key)
            # be polite to a government server -- don't hammer it
            time.sleep(3)

        browser.close()


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-lat", type=float, default=MIN_LAT)
    ap.add_argument("--max-lat", type=float, default=MAX_LAT)
    ap.add_argument("--min-lon", type=float, default=MIN_LON)
    ap.add_argument("--max-lon", type=float, default=MAX_LON)
    ap.add_argument("--out", default="./downloads")
    ap.add_argument("--payloads", nargs="+", default=["ohrc", "tmc2", "iirs"],
                     choices=list(PAYLOAD_IDS.keys()))
    ap.add_argument("--headless", action="store_true")
    return ap.parse_args()


if __name__ == "__main__":
    run(parse_args())