"""UI checks driven by Playwright against a stubbed job.

The job is injected straight into the app's in-memory store, so these run
offline and never touch ISSDC or NASA. That reaches past the public API on
purpose -- it is what keeps these tests deterministic -- at the cost of
coupling them to the job-store shape.
"""
from __future__ import annotations

import copy
import threading
import time

import pytest
import uvicorn

pytestmark = pytest.mark.ui

DONE_JOB = {
    "status": "done",
    "step_image_urls": {},
    "result": {
        "metrics": {
            "matcher": "XFEAT", "n_inliers": 523, "n_raw_matches": 1280,
            "inlier_ratio_pct": 40.86, "rmse_px": 1.5322,
            "spatial_uniformity": 0.625, "elapsed_sec": 6.14,
        },
        "register_result": {
            "mkpts_moving": [[100.0, 120.0], [300.0, 400.0], [700.0, 220.0]],
            "mkpts_ref": [[104.0, 118.0], [297.0, 403.0], [900.0, 100.0]],
            "inlier_mask": [True, True, False],
            "residuals_px": [0.8, 1.4, 47.2],
            "homography": [[1, 0, 4], [0, 1, -2], [0, 0, 1]],
        },
        "transfer": {"fetched_bytes": 82_000_000, "cached_bytes": 0,
                     "requests": 3, "product_bytes": 528_929_736},
        "provenance": {
            "ch2_instrument": "Terrain Mapping Camera-2 (Fore View)",
            "lroc_product_id": "nac.m1359306139lc",
            "lroc_localization": {
                "best_n": 185, "min_confident_matches": 100, "confident": True,
                "approx_center_line": 24146, "used_center_line": 26251,
                "windows_fetched": 1, "window_lines": 8429,
                "total_lines": 52224, "lines_searched": 8429,
                "strip_fraction_searched": 0.161, "whole_strip_searched": False,
            },
        },
    },
}


@pytest.fixture(scope="module")
def live_server():
    from lunar_matchbench.api.app import app

    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    yield "http://127.0.0.1:8765"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def page(live_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 950})
        yield pg
        browser.close()


def _seed(job_id: str, payload: dict) -> None:
    from lunar_matchbench.api import app as app_mod
    app_mod._store(job_id, payload)


def _failed_job(**loc_overrides) -> dict:
    job = copy.deepcopy(DONE_JOB)
    job["status"] = "failed"
    job["error"] = "Too few raw matches: 5"
    job["result"]["metrics"] = None
    job["result"]["provenance"]["lroc_localization"].update(**loc_overrides)
    return job


def test_locator_marks_geometry_estimate_and_lock(page, live_server):
    _seed("uitest01", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest01", wait_until="networkidle")
    page.wait_for_selector("#locator svg", timeout=15000)

    text = page.inner_text("#locator")
    assert "52224" in text.replace(",", ""), "strip length must be shown"
    assert page.locator("#locator [data-mark='estimate']").count() == 1
    assert page.locator("#locator [data-mark='lock']").count() == 1
    assert page.locator("#locator [data-mark='searched']").count() >= 1


def test_locator_flags_a_fully_searched_strip(page, live_server):
    _seed("uitest02", _failed_job(
        confident=False, best_n=0, total_lines=15360, lines_searched=15360,
        strip_fraction_searched=1.0, whole_strip_searched=True,
        windows_fetched=3, window_lines=9217,
    ))
    page.goto(f"{live_server}/?job=uitest02", wait_until="networkidle")
    page.wait_for_selector("#locator svg", timeout=15000)
    assert page.locator("#locator").get_attribute("data-coverage") == "full"


def test_comparator_exposes_both_frames_and_a_draggable_split(page, live_server):
    _seed("uitest03", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest03", wait_until="networkidle")
    page.wait_for_selector(".cmp", timeout=15000)

    assert page.locator(".cmp-img[data-layer='reference']").count() == 1
    assert page.locator(".cmp-img[data-layer='moving']").count() == 1
    assert page.locator(".cmp-handle").count() == 1

    box = page.locator(".cmp").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.25, box["y"] + box["height"] / 2)
    page.mouse.up()
    split = page.evaluate(
        "getComputedStyle(document.querySelector('.cmp')).getPropertyValue('--split')")
    assert float(split.strip()) < 0.45, f"split did not follow the drag: {split!r}"


def test_comparator_split_is_keyboard_operable(page, live_server):
    _seed("uitest03b", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest03b", wait_until="networkidle")
    page.wait_for_selector(".cmp-handle", timeout=15000)
    page.focus(".cmp-handle")
    for _ in range(5):
        page.keyboard.press("ArrowLeft")
    split = float(page.evaluate(
        "getComputedStyle(document.querySelector('.cmp')).getPropertyValue('--split')").strip())
    assert split < 0.5, "arrow keys must move the split"


def test_comparator_switches_to_fade(page, live_server):
    _seed("uitest04", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest04", wait_until="networkidle")
    page.wait_for_selector(".cmp", timeout=15000)
    page.click("[data-mode='fade']")
    assert page.locator(".cmp").get_attribute("data-mode") == "fade"


def test_tiepoint_overlay_reports_real_counts(page, live_server):
    _seed("uitest05", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest05", wait_until="networkidle")
    page.wait_for_selector("canvas.tp-canvas", timeout=15000)

    legend = page.inner_text(".tp-legend")
    assert "2" in legend and "3" in legend, f"expected 2 kept of 3 points: {legend!r}"

    page.click("[data-filter='inliers']")
    assert page.locator(".tp").get_attribute("data-filter") == "inliers"


def test_tiepoint_hover_reads_out_a_residual(page, live_server):
    _seed("uitest06", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest06", wait_until="networkidle")
    page.wait_for_selector("canvas.tp-canvas", timeout=15000)
    box = page.locator("canvas.tp-canvas").bounding_box()
    # Point 0 sits at (100,120) in a 1024 patch; hover its drawn position.
    page.mouse.move(box["x"] + box["width"] * (100 / 1024),
                    box["y"] + box["height"] * (120 / 1024))
    page.wait_for_timeout(300)
    assert "px" in page.inner_text(".tp-readout")


def test_charts_render_from_real_metrics(page, live_server):
    _seed("uitest07", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest07", wait_until="networkidle")
    page.wait_for_selector(".chart-histogram", timeout=15000)

    # Bars are paths, not rects: the far end is rounded and the baseline end
    # square, which a rect cannot express.
    assert page.locator(".chart-histogram .bar").count() >= 1
    # 8x8 uniformity grid, matching GRID_CELLS in config.py
    assert page.locator(".chart-grid .grid-cell").count() == 64

    prop = page.inner_text(".prop-labels")
    assert "1,280" in prop and "523" in prop, prop


def test_charts_encode_the_verdict_by_shape_not_only_colour(page, live_server):
    """Green vs red sits at deutan dE 8.5, so shape has to carry it too."""
    _seed("uitest07b", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest07b", wait_until="networkidle")
    page.wait_for_selector(".chart-legend", timeout=15000)
    assert page.locator(".lg-mark--disc").count() >= 1
    assert page.locator(".lg-mark--ring").count() >= 1


def test_charts_are_absent_without_tiepoints(page, live_server):
    """No data must render as an empty state, never as an invented chart."""
    bare = copy.deepcopy(DONE_JOB)
    bare["result"]["register_result"] = {}
    _seed("uitest08", bare)
    page.goto(f"{live_server}/?job=uitest08", wait_until="networkidle")
    page.wait_for_selector("#charts", timeout=15000)
    assert page.locator(".chart-histogram").count() == 0
    assert "No correspondence data" in page.inner_text("#charts")
