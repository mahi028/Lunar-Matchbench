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
