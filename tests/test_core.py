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
