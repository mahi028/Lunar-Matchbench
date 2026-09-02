"""
Image normalisation and conversion utilities.
"""
from __future__ import annotations
import numpy as np
import cv2


def has_real_content(arr: np.ndarray, min_ratio: float = 0.35) -> bool:
    """
    Detect whether a raw (pre-normalisation) science array holds real spatial
    structure, versus a dark/calibration frame that's just uncorrelated
    per-pixel sensor noise around a near-zero baseline.

    Percentile-stretch normalisation (`normalise_uint8`) will happily blow a
    tiny noise band up to full 0-255 contrast, making a blank frame look like
    a textured image -- so this check has to run on the raw values first.

    Real terrain, even low-contrast, is spatially correlated (craters/ridges
    span many pixels), so a small blur barely reduces its variance. Pure
    per-pixel noise has adjacent pixels uncorrelated, so blurring cancels
    most of it. The ratio is scale/calibration independent, so it works
    regardless of the raw DN convention a given product uses.
    """
    valid = arr[np.isfinite(arr)]
    if valid.size < 500:
        return False
    raw_var = float(np.nanvar(arr))
    if raw_var <= 0:
        return False
    filled = np.nan_to_num(arr, nan=float(np.nanmedian(valid)))
    blurred = cv2.blur(filled, (5, 5))
    return (float(np.nanvar(blurred)) / raw_var) > min_ratio


def normalise_uint8(arr: np.ndarray, clip_pct: tuple[float, float] = (2, 98)) -> np.ndarray:
    """Percentile-clip + scale a float/uint16 array to uint8."""
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    lo, hi = np.percentile(finite, clip_pct)
    normed = np.clip((arr - lo) / (hi - lo + 1e-8) * 255.0, 0, 255)
    normed[~np.isfinite(normed)] = 0
    return normed.astype(np.uint8)


def apply_clahe(img: np.ndarray, clip_limit: float = 3.0, tile: int = 8) -> np.ndarray:
    """Apply CLAHE illumination normalisation to a grayscale uint8 image."""
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(tile, tile))
    return clahe.apply(img)


def to_rgb(img: np.ndarray) -> np.ndarray:
    """Convert a grayscale uint8 image to RGB (required by XFeat)."""
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
    return img


def resize_to(img: np.ndarray, size: int) -> np.ndarray:
    """Downsample or upsample an image to a square of `size` pixels."""
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)


def make_checkerboard(img_a: np.ndarray, img_b: np.ndarray,
                      cells: int = 4) -> np.ndarray:
    """Interleave two same-size images in a checkerboard pattern."""
    h, w = img_a.shape[:2]
    out = np.empty_like(img_a)
    cell_h = h // cells
    cell_w = w // cells
    for i in range(cells):
        for j in range(cells):
            src = img_a if (i + j) % 2 == 0 else img_b
            out[i*cell_h:(i+1)*cell_h, j*cell_w:(j+1)*cell_w] = \
                src[i*cell_h:(i+1)*cell_h, j*cell_w:(j+1)*cell_w]
    return out


def make_difference_overlay(img_a: np.ndarray, img_b: np.ndarray) -> np.ndarray:
    """Cyan-magenta anaglyph-style difference overlay."""
    rgb = np.stack([
        img_b.astype(np.float32),
        img_a.astype(np.float32),
        img_a.astype(np.float32),
    ], axis=-1)
    return np.clip(rgb, 0, 255).astype(np.uint8)
