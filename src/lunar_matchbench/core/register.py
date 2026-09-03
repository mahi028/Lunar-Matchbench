"""
Lunar-MatchBench: Registration Engine
======================================
XFeat (CVPR 2024) primary matcher with SIFT fallback.
All state is stateless (pure functions) — safe for concurrent API use.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from lunar_matchbench.config import (
    RANSAC_THRESH, MIN_INLIERS, XFEAT_TOP_K,
    SIFT_N_FEATURES, CLAHE_CLIP, GRID_CELLS, PATCH_SIZE,
)
from lunar_matchbench.utils.image import apply_clahe, to_rgb


# ── XFeat singleton (load once) ───────────────────────────────────────────────

_xfeat = None

def _get_xfeat():
    global _xfeat
    if _xfeat is None:
        import torch
        # Explicit cap, not just relying on OMP_NUM_THREADS/env vars -- torch
        # doesn't always honour those depending on how its CPU backend was
        # built, and on a constrained (1-2 vCPU) host, capping threads also
        # limits how many glibc malloc arenas get created, which is the
        # actual lever against the RSS-climbs-with-every-request pattern.
        torch.set_num_threads(max(1, os.cpu_count() or 1))
        _xfeat = torch.hub.load(
            "verlab/accelerated_features", "XFeat",
            pretrained=True, top_k=XFEAT_TOP_K, trust_repo=True,
        )
        _xfeat.eval()
    return _xfeat


# ── Keypoint helpers ──────────────────────────────────────────────────────────

def _spatial_nms(pts: np.ndarray, scores: np.ndarray | None,
                 img_h: int, img_w: int,
                 cells: int = GRID_CELLS, max_per_cell: int = 20) -> np.ndarray:
    """Grid-based Non-Maximum Suppression for spatial uniformity."""
    cell_h = img_h / cells
    cell_w = img_w / cells
    selected = []
    for r in range(cells):
        for c in range(cells):
            mask = (
                (pts[:, 1] >= r * cell_h) & (pts[:, 1] < (r + 1) * cell_h) &
                (pts[:, 0] >= c * cell_w) & (pts[:, 0] < (c + 1) * cell_w)
            )
            cell_pts = np.where(mask)[0]
            if len(cell_pts) == 0:
                continue
            if scores is not None:
                order = np.argsort(-scores[cell_pts])
                cell_pts = cell_pts[order]
            selected.extend(cell_pts[:max_per_cell].tolist())
    return np.array(selected, dtype=np.int64)


def _spatial_uniformity(pts: np.ndarray, img_h: int, img_w: int,
                        cells: int = GRID_CELLS) -> float:
    """Fraction of grid cells that contain at least one inlier tie-point."""
    if len(pts) == 0:
        return 0.0
    cell_h = img_h / cells
    cell_w = img_w / cells
    occupied = set()
    for x, y in pts:
        r = int(y / cell_h)
        c = int(x / cell_w)
        occupied.add((min(r, cells - 1), min(c, cells - 1)))
    return len(occupied) / (cells * cells)


# ── Matchers ──────────────────────────────────────────────────────────────────

MatcherType = Literal["xfeat", "sift"]


def _match_xfeat(img_a: np.ndarray, img_b: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Return (kpts_a, kpts_b, mkpts0, mkpts1): every keypoint XFeat detected on
    each image individually, plus the mutual-nearest-neighbour matched pairs
    between them. Detection and matching are separate XFeat calls (rather
    than the bundled `match_xfeat` convenience method) specifically so the
    caller can visualise "keypoints found" as a distinct stage from
    "correspondences found" -- they're genuinely different amounts of data,
    not the same result relabelled.
    """
    xfeat = _get_xfeat()
    rgb_a = to_rgb(apply_clahe(img_a))
    rgb_b = to_rgb(apply_clahe(img_b))
    im1 = xfeat.parse_input(rgb_a)
    im2 = xfeat.parse_input(rgb_b)
    out1 = xfeat.detectAndCompute(im1, top_k=XFEAT_TOP_K)[0]
    out2 = xfeat.detectAndCompute(im2, top_k=XFEAT_TOP_K)[0]
    idx0, idx1 = xfeat.match(out1["descriptors"], out2["descriptors"])

    kpts_a = out1["keypoints"].cpu().numpy().astype(np.float32)
    kpts_b = out2["keypoints"].cpu().numpy().astype(np.float32)
    idx0 = idx0.cpu().numpy()
    idx1 = idx1.cpu().numpy()
    pts0 = kpts_a[idx0]
    pts1 = kpts_b[idx1]
    return kpts_a, kpts_b, pts0, pts1


def _match_sift(img_a: np.ndarray, img_b: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return (kpts_a, kpts_b, mkpts0, mkpts1) using SIFT + Lowe ratio test."""
    sift = cv2.SIFT_create(nfeatures=SIFT_N_FEATURES)
    kp1, des1 = sift.detectAndCompute(apply_clahe(img_a), None)
    kp2, des2 = sift.detectAndCompute(apply_clahe(img_b), None)
    kpts_a = np.array([k.pt for k in kp1], dtype=np.float32) if kp1 else np.empty((0, 2), dtype=np.float32)
    kpts_b = np.array([k.pt for k in kp2], dtype=np.float32) if kp2 else np.empty((0, 2), dtype=np.float32)
    if des1 is None or des2 is None or len(kp1) < 8 or len(kp2) < 8:
        return kpts_a, kpts_b, np.empty((0, 2), dtype=np.float32), np.empty((0, 2), dtype=np.float32)
    bf = cv2.BFMatcher(cv2.NORM_L2)
    raw = bf.knnMatch(des1, des2, k=2)
    good = [m for m, n in raw if m.distance < 0.75 * n.distance]
    if not good:
        return kpts_a, kpts_b, np.empty((0, 2), dtype=np.float32), np.empty((0, 2), dtype=np.float32)
    pts0 = np.array([kp1[m.queryIdx].pt for m in good], dtype=np.float32)
    pts1 = np.array([kp2[m.trainIdx].pt for m in good], dtype=np.float32)
    return kpts_a, kpts_b, pts0, pts1


# ── Public API ────────────────────────────────────────────────────────────────

def register(
    moving: np.ndarray,
    reference: np.ndarray,
    matcher: MatcherType = "xfeat",
) -> dict:
    """
    Register `moving` image onto `reference` image.

    Parameters
    ----------
    moving    : uint8 grayscale (H, W) — Chandrayaan-2 patch
    reference : uint8 grayscale (H, W) — LROC NAC patch
    matcher   : 'xfeat' (default) or 'sift'

    Returns
    -------
    dict with keys:
        status, matcher, n_raw_matches, n_inliers, inlier_ratio_pct,
        reprojection_rmse_px, spatial_uniformity, homography (3x3 list),
        elapsed_sec, mkpts_moving, mkpts_ref, inlier_mask, kpts_moving, kpts_ref

    kpts_moving/kpts_ref (every keypoint detected, before matching) and
    mkpts_moving/mkpts_ref (raw matched pairs, before RANSAC) are always
    included when available -- including on FAILED/ERROR -- so a failure can
    be visualised at the exact stage it broke down, not just reported as text.
    """
    t0 = time.perf_counter()
    h, w = reference.shape[:2]

    # ── Match ─────────────────────────────────────────────────────────────────
    try:
        if matcher == "xfeat":
            kpts_m, kpts_r, pts_m, pts_r = _match_xfeat(moving, reference)
        else:
            kpts_m, kpts_r, pts_m, pts_r = _match_sift(moving, reference)
    except Exception as exc:
        return {"status": "ERROR", "reason": str(exc)}

    stage_data = {
        "kpts_moving": kpts_m.tolist(),
        "kpts_ref":    kpts_r.tolist(),
        "mkpts_moving": pts_m.tolist(),
        "mkpts_ref":    pts_r.tolist(),
    }

    n_raw = len(pts_m)
    if n_raw < 8:
        return {"status": "FAILED", "reason": f"Too few raw matches: {n_raw}", **stage_data}

    # ── MAGSAC++ Homography ───────────────────────────────────────────────────
    H, mask = cv2.findHomography(
        pts_m, pts_r,
        method=cv2.USAC_MAGSAC,
        ransacReprojThreshold=RANSAC_THRESH,
        confidence=0.9995,
        maxIters=10000,
    )
    if H is None or mask is None:
        return {"status": "FAILED", "reason": "Homography estimation failed.", **stage_data}

    mask_flat = mask.ravel().astype(bool)
    n_inliers = int(mask_flat.sum())
    stage_data["inlier_mask"] = mask_flat.tolist()

    # Reprojection error for EVERY raw match, not just the inliers. The inlier
    # subset alone is a censored distribution -- presenting it as "the" error
    # histogram would flatter the result by hiding precisely the matches RANSAC
    # threw out. Computed here, before the inlier-count gate, so a run that
    # fails still carries the evidence of how it failed.
    all_proj = cv2.perspectiveTransform(pts_m.reshape(-1, 1, 2), H).reshape(-1, 2)
    all_resid = np.sqrt(np.sum((all_proj - pts_r) ** 2, axis=1))
    stage_data["residuals_px"] = [round(float(v), 4) for v in all_resid]

    if n_inliers < MIN_INLIERS:
        return {"status": "FAILED", "reason": f"Only {n_inliers} inliers (< {MIN_INLIERS}).", **stage_data}

    # ── Quality metrics ───────────────────────────────────────────────────────
    in_m = pts_m[mask_flat]
    in_r = pts_r[mask_flat]

    # Same residual array the UI plots, restricted to the kept points, so the
    # headline RMSE and the histogram can never tell different stories.
    rmse = float(np.sqrt(np.mean(all_resid[mask_flat] ** 2)))
    uniformity = _spatial_uniformity(in_r, h, w)

    elapsed = round(time.perf_counter() - t0, 3)
    return {
        "status":              "SUCCESS",
        "matcher":             matcher.upper(),
        "n_raw_matches":       n_raw,
        "n_inliers":           n_inliers,
        "inlier_ratio_pct":    round(n_inliers / n_raw * 100, 2),
        "reprojection_rmse_px": round(rmse, 4),
        "spatial_uniformity":  round(uniformity, 4),
        "homography":          H.tolist(),
        "elapsed_sec":         elapsed,
        **stage_data,
    }
