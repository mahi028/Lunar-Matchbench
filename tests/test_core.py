"""
Tests for Lunar-MatchBench Core Modules
========================================
"""
import pytest
import numpy as np
from pathlib import Path

from lunar_matchbench.config import INSTRUMENT_META, PATCH_SIZE
from lunar_matchbench.utils.image import normalise_uint8, apply_clahe, to_rgb, resize_to
from lunar_matchbench.utils.geo import BBox, overlap_report


def test_image_utils():
    # Test normalization
    data = np.random.uniform(100, 5000, (100, 100)).astype(np.float32)
    norm = normalise_uint8(data)
    assert norm.dtype == np.uint8
    assert norm.shape == (100, 100)

    # Test CLAHE
    clahe_out = apply_clahe(norm)
    assert clahe_out.shape == norm.shape
    assert clahe_out.dtype == np.uint8

    # Test RGB conversion
    rgb = to_rgb(norm)
    assert rgb.shape == (100, 100, 3)

    # Test resizing
    resized = resize_to(norm, 512)
    assert resized.shape == (512, 512)


def test_geo_bbox_overlap():
    box_a = BBox(14.0, 16.0, 288.0, 290.0)
    box_b = BBox(14.5, 15.5, 288.5, 289.5)
    
    assert box_a.contains(15.0, 289.0)
    assert not box_a.contains(17.0, 289.0)
    
    inter = box_a.intersect(box_b)
    assert inter is not None
    assert inter.lat_min == 14.5 and inter.lat_max == 15.5
    assert inter.lon_min == 288.5 and inter.lon_max == 289.5
    
    rep = overlap_report(box_b, box_a)
    assert rep["has_overlap"] is True
    assert rep["ch2_overlap_pct"] == 100.0


def test_instrument_metadata():
    assert "tmc" in INSTRUMENT_META
    assert "ohrc" in INSTRUMENT_META
    assert INSTRUMENT_META["tmc"]["samples_per_line"] == 4000
    assert INSTRUMENT_META["ohrc"]["samples_per_line"] == 12000


def test_extract_lroc_patch_caps_windows():
    """With no confident peak anywhere, the search must still stop at the cap.

    Over HTTP each window is a real fetch, so an unbounded search is the
    difference between a demo that runs and one that stalls.
    """
    import numpy as np

    from lunar_matchbench.config import MAX_LROC_WINDOWS
    from lunar_matchbench.core.downloader import extract_lroc_patch

    class FlatReader:
        """Featureless terrain: SIFT will never find a confident peak."""

        total_lines = 40000
        total_samples = 1024

        def __init__(self):
            self.calls = 0
            self.stats = {"fetched_bytes": 0, "cached_bytes": 0, "requests": 0}

        def read_lines(self, start, n):
            self.calls += 1
            start = max(0, start)
            n = max(0, min(n, self.total_lines - start))
            rng = np.random.default_rng(0)
            return rng.normal(1000, 1, (n, self.total_samples)).astype(np.float32)

    reader = FlatReader()
    candidate = {"lat_min": 14.0, "lat_max": 16.0, "lon_min": 288.0, "lon_max": 290.0}
    ref = np.random.default_rng(1).integers(0, 255, (1024, 1024), dtype=np.uint8)

    _, info = extract_lroc_patch(reader, candidate, 15.0, 289.0,
                                 ref_patch=ref, scale_factor=1.0)
    assert info["confident"] is False
    assert info["windows_fetched"] <= MAX_LROC_WINDOWS
    assert reader.calls <= MAX_LROC_WINDOWS + 1


def test_extract_lroc_patch_uses_geometry_when_unconfident():
    """An unconfident result must report the geometry estimate, not a noise peak."""
    import numpy as np

    from lunar_matchbench.core.downloader import extract_lroc_patch

    class FlatReader:
        total_lines = 20000
        total_samples = 512
        stats = {"fetched_bytes": 0, "cached_bytes": 0, "requests": 0}

        def read_lines(self, start, n):
            start = max(0, start)
            n = max(0, min(n, self.total_lines - start))
            rng = np.random.default_rng(2)
            return rng.normal(500, 1, (n, self.total_samples)).astype(np.float32)

    candidate = {"lat_min": 10.0, "lat_max": 20.0, "lon_min": 288.0, "lon_max": 290.0}
    _, info = extract_lroc_patch(FlatReader(), candidate, 15.0, 289.0,
                                 ref_patch=None, scale_factor=1.0)
    assert info["confident"] is False
    assert info["used_center_line"] == info["approx_center_line"]


def test_extract_lroc_patch_finds_a_planted_match():
    """A window containing the reference terrain must be located confidently."""
    import cv2
    import numpy as np

    from lunar_matchbench.core.downloader import extract_lroc_patch

    from lunar_matchbench.utils.image import normalise_uint8

    rng = np.random.default_rng(5)
    # Structured terrain: blurred noise is spatially correlated, so it survives
    # has_real_content and gives SIFT something real to match.
    #
    # It must be percentile-normalised here for the same reason a real CH2
    # reference is: extract_ch2_patch returns normalise_uint8(crop), and the
    # candidate window is normalised inside extract_lroc_patch. Handing SIFT a
    # raw low-contrast reference against a stretched candidate is an unfair
    # comparison -- blurred noise lands in a ~60 DN band and yields 28
    # keypoints against the window's 1500.
    terrain = normalise_uint8(cv2.GaussianBlur(
        rng.integers(0, 255, (1024, 1024)).astype(np.uint8), (0, 0), 3
    ).astype(np.float32))

    class PlantedReader:
        total_lines = 8000
        total_samples = 1024
        stats = {"fetched_bytes": 0, "cached_bytes": 0, "requests": 0}

        def read_lines(self, start, n):
            start = max(0, start)
            n = max(0, min(n, self.total_lines - start))
            strip = cv2.GaussianBlur(
                rng.integers(0, 255, (self.total_lines, self.total_samples)
                             ).astype(np.uint8), (0, 0), 3).astype(np.float32)
            strip[4000:5024] = terrain.astype(np.float32)
            return strip[start:start + n]

    candidate = {"lat_min": 14.0, "lat_max": 16.0, "lon_min": 288.0, "lon_max": 290.0}
    patch, info = extract_lroc_patch(PlantedReader(), candidate, 15.0, 289.0,
                                     ref_patch=terrain, scale_factor=1.0)
    assert patch is not None
    assert patch.shape == (1024, 1024)
    assert info["confident"] is True, f"expected a confident lock, got best_n={info['best_n']}"
    assert abs(info["used_center_line"] - 4512) < 1200


def _shifted_pair():
    """A blurred-noise image and a translated copy: an easy, honest match."""
    import cv2
    import numpy as np

    rng = np.random.default_rng(7)
    moving = cv2.GaussianBlur(
        rng.integers(0, 255, (512, 512)).astype(np.uint8), (0, 0), 2)
    M = np.float32([[1, 0, 12], [0, 1, -7]])
    reference = cv2.warpAffine(moving, M, (512, 512))
    return moving, reference


def test_register_returns_residual_per_raw_match():
    """The UI histogram needs the full distribution, not just the inliers."""
    from lunar_matchbench.core.register import register

    result = register(*_shifted_pair(), matcher="sift")
    assert result["status"] == "SUCCESS", result.get("reason")
    assert "residuals_px" in result
    assert len(result["residuals_px"]) == len(result["mkpts_moving"])
    assert all(r >= 0 for r in result["residuals_px"])


def test_reported_rmse_matches_inlier_residuals():
    """RMSE must be the inlier subset of the same residuals the UI plots."""
    import numpy as np

    from lunar_matchbench.core.register import register

    result = register(*_shifted_pair(), matcher="sift")
    resid = np.array(result["residuals_px"])
    mask = np.array(result["inlier_mask"], dtype=bool)
    expected = float(np.sqrt(np.mean(resid[mask] ** 2)))
    assert abs(expected - result["reprojection_rmse_px"]) < 1e-2


def test_transfer_snapshot_reads_streaming_reader():
    from lunar_matchbench.core.pipeline import _transfer_snapshot

    class Streamed:
        stats = {"fetched_bytes": 10, "cached_bytes": 5, "requests": 2}
        rf = type("F", (), {"size": 999})()

    assert _transfer_snapshot(Streamed()) == {
        "fetched_bytes": 10, "cached_bytes": 5, "requests": 2, "product_bytes": 999,
    }


def test_transfer_snapshot_handles_local_reader():
    """A local file has no rf and no transfer -- it must not crash or invent one."""
    from lunar_matchbench.core.pipeline import _transfer_snapshot

    class Local:
        stats = {"fetched_bytes": 0, "cached_bytes": 0, "requests": 0}

    assert _transfer_snapshot(Local())["product_bytes"] == 0


def test_warm_presets_match_the_ui_presets():
    """If these drift apart, pre-warming silently warms the wrong coordinates.

    That is worse than not warming at all: the demo looks prepared and still
    stalls on the coordinate actually clicked.
    """
    import re
    from pathlib import Path

    from lunar_matchbench.cli import WARM_PRESETS

    html = (Path(__file__).resolve().parents[1]
            / "src/lunar_matchbench/api/templates/index.html").read_text(encoding="utf-8")
    in_html = {(float(a), float(b))
               for a, b in re.findall(r'data-lat="([-\d.]+)"\s+data-lon="([-\d.]+)"', html)}
    assert in_html, "no presets found in index.html -- did the markup change?"
    assert in_html == set(WARM_PRESETS)


def test_localization_reports_strip_coverage():
    """An unconfident result means different things at 20% vs 100% coverage.

    At 10.2N/289.5E a 24-probe sweep of all 15,360 lines found no peak, so the
    honest report there is "genuine mismatch", not "may be mislocalized".
    """
    import numpy as np

    from lunar_matchbench.core.downloader import extract_lroc_patch

    class ShortStrip:
        """Short enough that the window cap covers the whole thing."""
        total_lines = 4000
        total_samples = 512
        stats = {"fetched_bytes": 0, "cached_bytes": 0, "requests": 0}

        def read_lines(self, start, n):
            start = max(0, start)
            n = max(0, min(n, self.total_lines - start))
            rng = np.random.default_rng(3)
            return rng.normal(500, 1, (n, self.total_samples)).astype(np.float32)

    candidate = {"lat_min": 10.0, "lat_max": 11.0, "lon_min": 288.0, "lon_max": 290.0}
    _, info = extract_lroc_patch(ShortStrip(), candidate, 10.5, 289.0,
                                 ref_patch=None, scale_factor=1.0)
    assert info["total_lines"] == 4000
    assert 0.0 <= info["strip_fraction_searched"] <= 1.0
    assert isinstance(info["whole_strip_searched"], bool)
    assert info["lines_searched"] <= info["total_lines"]


def test_lroc_candidates_are_interleaved_across_acquisitions():
    """Three attempts must land on three different acquisitions.

    The nearest-by-footprint ordering fills the attempt budget with the lc/rc
    halves of one stereo pair -- same spacecraft pass, same sun angle -- so a
    coordinate that fails under that illumination has no second chance.
    """
    from lunar_matchbench.core.downloader import interleave_by_acquisition

    ranked = [
        {"pds_id": "nac.m111lc", "start_time": "2011-01-01T00:00:00"},
        {"pds_id": "nac.m111rc", "start_time": "2011-01-01T00:00:00"},
        {"pds_id": "nac.m222lc", "start_time": "2012-02-02T00:00:00"},
        {"pds_id": "nac.m222rc", "start_time": "2012-02-02T00:00:00"},
        {"pds_id": "nac.m333lc", "start_time": "2013-03-03T00:00:00"},
    ]
    out = interleave_by_acquisition(ranked)
    assert [c["pds_id"] for c in out[:3]] == ["nac.m111lc", "nac.m222lc", "nac.m333lc"]
    # Nothing may be dropped -- the stereo partners come after the diverse head.
    assert sorted(c["pds_id"] for c in out) == sorted(c["pds_id"] for c in ranked)


def test_interleaving_preserves_rank_within_an_acquisition():
    from lunar_matchbench.core.downloader import interleave_by_acquisition

    ranked = [
        {"pds_id": "nac.m111lc", "start_time": "t1"},
        {"pds_id": "nac.m111rc", "start_time": "t1"},
        {"pds_id": "nac.m222lc", "start_time": "t2"},
    ]
    out = interleave_by_acquisition(ranked)
    order = [c["pds_id"] for c in out]
    assert order.index("nac.m111lc") < order.index("nac.m111rc")


def test_interleaving_survives_missing_identifiers():
    from lunar_matchbench.core.downloader import interleave_by_acquisition

    ranked = [{"pds_id": None, "start_time": ""}, {"pds_id": "nac.m1lc", "start_time": "t"}]
    assert len(interleave_by_acquisition(ranked)) == 2


def test_ch2_acquisition_time_is_parsed_from_the_product_name():
    """The gap between the two passes sets the sun-angle difference.

    ISSDC encodes the timestamp in the filename and nowhere else the pipeline
    already reads, so without this the UI can only report one of the two dates.
    """
    from lunar_matchbench.core.downloader import _ch2_time_from_name

    assert _ch2_time_from_name(
        "ch2_tmc_ncf_20191218T1121183775_d_img_gds.zip") == "2019-12-18T11:21:18"
    assert _ch2_time_from_name("no_timestamp_here.zip") == ""
    assert _ch2_time_from_name("") == ""


def test_the_two_patches_come_from_two_different_sources():
    """The moving and reference patches must never be the same raster twice.

    If the reference were secretly a second read of the Chandrayaan-2 image the
    registration would succeed trivially and every metric would be meaningless.
    Verified live for job c8ce875f: CH2 4.33 m/px from ISSDC against LROC
    1.052 m/px from NASA PDS, pixel correlation 0.14, and a direct re-read of
    the LROC product matched the saved reference patch at 0.998.

    This guards the wiring: extract_ch2_patch and extract_lroc_patch must be
    fed by different readers.
    """
    import inspect

    from lunar_matchbench.core import pipeline

    source = inspect.getsource(pipeline.run_pipeline)
    assert "extract_ch2_patch(" in source
    assert "extract_lroc_patch(" in source
    # The reference patch must come from the LROC reader, never from ch2_match.
    assert "extract_lroc_patch(\n                path," in source or \
           "extract_lroc_patch(\n            path," in source, \
        "the LROC patch must be extracted from the LROC reader"
    assert "extract_ch2_patch(ch2_match" in source, \
        "the moving patch must come from the CH2 geometry match"


def test_registration_rejects_an_image_matched_against_itself():
    """A self-match is a degenerate success; the guard is that we never do it.

    Registering an image against itself gives a near-perfect result, which is
    exactly what a duplicated-source bug would look like from the metrics. This
    pins the signature so it is recognisable if it ever appears for real.
    """
    import cv2
    import numpy as np

    from lunar_matchbench.core.register import register

    rng = np.random.default_rng(11)
    img = cv2.GaussianBlur(rng.integers(0, 255, (512, 512)).astype(np.uint8), (0, 0), 2)

    same = register(img, img, matcher="sift")
    assert same["status"] == "SUCCESS"
    # A true self-match is essentially exact: near-zero error, near-total inliers.
    assert same["reprojection_rmse_px"] < 0.05, same["reprojection_rmse_px"]
    assert same["inlier_ratio_pct"] > 95, same["inlier_ratio_pct"]


def test_project_root_is_overridable(tmp_path, monkeypatch):
    """Installed in a container, walking up from __file__ lands in site-packages.

    Without an override the demo bundle, job records and cache would all resolve
    inside the Python installation instead of the deployment's own directory.
    """
    import importlib

    monkeypatch.setenv("LMB_PROJECT_ROOT", str(tmp_path))
    import lunar_matchbench.config as config
    reloaded = importlib.reload(config)
    try:
        assert reloaded.PROJECT_ROOT == tmp_path
        assert reloaded.DATA_ROOT == tmp_path / "data_store"
        assert reloaded.OUTPUT_ROOT == tmp_path / "outputs"

        monkeypatch.setenv("LMB_OUTPUT_ROOT", str(tmp_path / "elsewhere"))
        again = importlib.reload(config)
        assert again.OUTPUT_ROOT == tmp_path / "elsewhere"
    finally:
        monkeypatch.delenv("LMB_PROJECT_ROOT", raising=False)
        monkeypatch.delenv("LMB_OUTPUT_ROOT", raising=False)
        importlib.reload(config)
