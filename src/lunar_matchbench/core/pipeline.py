"""
Lunar-MatchBench: End-to-End Registration Pipeline
====================================================
Orchestrates discovery → download → extraction → registration → visualisation.
Stateless: all state is passed in / returned as dicts and ndarrays.

Every stage that produces a visual artefact writes it to disk immediately, so
a UI polling job status can render the pipeline as it actually happens --
extracted patches, detected keypoints, raw correspondences, MAGSAC++ inliers,
final result -- rather than a single poster that only appears at the end.
A failed run still gets a diagnostic image at the stage it broke down, since
"why did this fail" is exactly what a research prototype needs to show.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from lunar_matchbench.config import (
    PATCH_SIZE, POSTER_DIR, OVERLAP_DIR, INSTRUMENT_META, RUN_BYTE_BUDGET,
    ensure_dirs,
)
from lunar_matchbench.core.downloader import (
    find_ch2_geometry_match, extract_ch2_patch,
    discover_lroc_products, open_lroc_reader, extract_lroc_patch,
    find_ch2_geometry_match_streamed,
)
from lunar_matchbench.core.ch2_fetch import fetch_ch2_streamed, Ch2FetchError
from lunar_matchbench.core.streaming import TransferBudget, TransferBudgetExceeded
from lunar_matchbench.core.register import register
from lunar_matchbench.utils.geo import BBox, overlap_report
from lunar_matchbench.utils.image import make_checkerboard, make_difference_overlay

# ── Design tokens ───────────────────────────────────────────────────────────
# A restrained, print-report palette -- no gradients, no glow, no blue/black
# theme. Warm neutrals for structure, a rust accent for the ISRO/CH2 side, a
# slate accent for the NASA/LROC side, and unambiguous green/red for
# inlier/outlier so the science reads correctly at a glance.
BG          = "#fbfaf7"
PANEL_BG    = "#ffffff"
GRID_LINE   = "#dedad2"
INK         = "#232220"
INK_MUTED   = "#77726a"
CH2_ACCENT  = "#a8481f"
LROC_ACCENT = "#3d4a5c"
KP_COLOR    = "#c98a3c"
GOOD_COLOR  = "#2e7d4f"
BAD_COLOR   = "#a13228"
plt.rcParams["font.family"] = "DejaVu Sans"


def _style_axes(ax, title: str, title_color: str = INK, fontsize: int = 10.5):
    ax.axis("off")
    ax.set_facecolor(PANEL_BG)
    ax.set_title(title, color=title_color, fontsize=fontsize, pad=8, loc="left",
                 fontweight="medium")


def _suptitle(fig, text: str, sub: str | None = None, sub_color: str = INK_MUTED):
    fig.suptitle(text, color=INK, fontsize=15, fontweight="bold", x=0.03, y=0.99, ha="left")
    if sub:
        fig.text(0.03, 0.955, sub, color=sub_color, fontsize=10.5, ha="left", fontweight="bold")


def _footer(fig, text: str):
    fig.text(0.03, 0.012, text, color=INK_MUTED, fontsize=8, ha="left", family="monospace")


def _save_raw_patches(ch2, lroc, result: dict, label: str) -> dict:
    """Write bare patch PNGs -- no titles, axes or chrome -- for the browser.

    The matplotlib posters stay the scientific record. These are the pixels the
    interactive comparator and tie-point overlay composite directly, so they
    must carry no annotation of their own.
    """
    out_dir = POSTER_DIR / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)

    # The comparator overlays these two directly, so a size mismatch would show
    # up as a misalignment that the registration did not actually make. Both
    # already come out of the extractors at PATCH_SIZE; this makes that a
    # guarantee rather than a coincidence of two separate code paths.
    if ch2.shape[:2] != lroc.shape[:2]:
        h, w = lroc.shape[:2]
        ch2 = cv2.resize(ch2, (w, h), interpolation=cv2.INTER_AREA)

    paths = {"ch2": out_dir / f"{label}_ch2.png", "lroc": out_dir / f"{label}_lroc.png"}
    cv2.imwrite(str(paths["ch2"]), ch2)
    cv2.imwrite(str(paths["lroc"]), lroc)
    if result.get("status") == "SUCCESS" and result.get("homography") is not None:
        h, w = lroc.shape[:2]
        warped = cv2.warpPerspective(ch2, np.array(result["homography"]), (w, h))
        paths["warped"] = out_dir / f"{label}_warped.png"
        cv2.imwrite(str(paths["warped"]), warped)
    return {k: str(v) for k, v in paths.items()}


def _transfer_snapshot(reader) -> dict:
    """Byte accounting for the UI, so "fetched 38.7 MB of 529 MB" is a fact.

    A local cached product reports zeros with product_bytes 0, which is how the
    UI tells "served from disk" apart from "streamed over the network".
    """
    stats = getattr(reader, "stats", {}) or {}
    rf = getattr(reader, "rf", None)
    return {
        "fetched_bytes": stats.get("fetched_bytes", 0),
        "cached_bytes":  stats.get("cached_bytes", 0),
        "requests":      stats.get("requests", 0),
        "product_bytes": getattr(rf, "size", 0) if rf is not None else 0,
    }


def run_pipeline(
    lat: float,
    lon: float,
    instrument: str = "tmc",
    matcher: str = "xfeat",
    job_id: str | None = None,
    progress_cb=None,
) -> dict:
    """
    Full registration pipeline.

    Parameters
    ----------
    lat, lon     : Target coordinate (planetocentric, 0-360 E)
    instrument   : 'tmc' or 'ohrc'
    matcher      : 'xfeat' or 'sift'
    job_id       : Optional UUID for output file naming
    progress_cb  : Optional callable(step: int, total: int, message: str)

    Returns
    -------
    dict with keys: status, metrics, step_images, provenance
    """
    ensure_dirs()
    total_steps = 8
    label = job_id or f"lat{lat}_lon{lon}_{instrument}"
    step_images: dict[str, str] = {}

    transfer: dict = {"fetched_bytes": 0, "cached_bytes": 0,
                      "requests": 0, "product_bytes": 0}
    # One shared ceiling for the whole run, across every product it touches.
    budget = TransferBudget(RUN_BYTE_BUDGET)

    def _progress(step: int, msg: str):
        if progress_cb:
            # step_images is mutated in place as each artefact is written, so
            # passing it by reference here means every call carries whatever
            # is ready so far -- a status poll mid-run sees images appear
            # incrementally, not all at once at the very end.
            progress_cb(step, total_steps, msg, dict(step_images), dict(transfer))

    # ── 1. CH2 patch ──────────────────────────────────────────────────────────
    _progress(1, "Locating Chandrayaan-2 patch...")
    ch2_match = find_ch2_geometry_match(lat, lon, instrument)
    if ch2_match is None:
        # Nothing local covers this coordinate -- fetch it directly from
        # ISRO's ISSDC archive (real Keycloak login + WFS discovery + PRADAN
        # download), the same on-demand way LROC data is always fetched,
        # rather than requiring CH2 data to be pre-staged locally.
        def _fetch_cb(stage: str, detail):
            if stage == "query":
                _progress(1, "No local CH2 coverage -- querying ISSDC WFS catalog...")
            elif stage == "resolve":
                _progress(1, f"Resolving PRADAN download path ({detail} candidate product(s))...")
            elif stage == "download":
                _progress(1, f"Downloading {detail} from ISSDC/PRADAN...")
            elif stage == "stream":
                _progress(1, f"Streaming {detail} from ISSDC/PRADAN (byte-range)...")
            elif stage == "downloading":
                _progress(1, f"Downloading from ISSDC/PRADAN... {detail / 1e6:.1f} MB")

        try:
            _, zstream = fetch_ch2_streamed(lat, lon, instrument,
                                            progress_cb=_fetch_cb, budget=budget)
        except Ch2FetchError as exc:
            return {"status": "FAILED", "reason": f"Could not fetch Chandrayaan-2 data from ISSDC: {exc}"}
        except TransferBudgetExceeded as exc:
            return {"status": "FAILED", "reason": f"Transfer budget exhausted reading CH2 data: {exc}"}

        if zstream is not None:
            _progress(1, "Reading CH2 geometry grid (0.8 MB of 508 MB)...")
            ch2_match = find_ch2_geometry_match_streamed(zstream, lat, lon, instrument)

    if ch2_match is None:
        return {
            "status": "FAILED",
            "reason": (
                f"No Chandrayaan-2 {instrument.upper()} data covers ({lat:.3f}°N, {lon:.3f}°E) -- "
                f"checked local data and ISRO's ISSDC archive."
            ),
        }

    ch2_patch = extract_ch2_patch(ch2_match, instrument)
    if ch2_patch is None:
        return {"status": "FAILED", "reason": "Failed to extract CH2 patch."}

    # ── 2. LROC discovery ─────────────────────────────────────────────────────
    _progress(2, "Querying NASA ODE REST API for LROC NAC...")
    candidates = discover_lroc_products(lat, lon)
    if not candidates:
        return {"status": "FAILED", "reason": "No LROC NAC product found at this coordinate."}

    # ── 3-4. LROC download + patch extraction ─────────────────────────────────
    # A geometrically-good candidate can still turn out to be unusable (e.g.
    # a calibration/dark frame mis-tagged with a lunar-surface footprint --
    # extract_lroc_patch's has_real_content check catches those). Rather than
    # fail outright on the top-ranked candidate, fall through to the next
    # best-ranked one, capped so a run of bad candidates can't trigger
    # unbounded downloads.
    MAX_CANDIDATE_ATTEMPTS = 3
    best = lroc_path = lroc_patch = None
    ch2_gsd = ch2_match.get("gsd_m") or INSTRUMENT_META[instrument]["gsd_m"]
    lroc_gsd = scale = None
    loc_info = None
    skipped = []
    budget_error = None
    for candidate in candidates[:MAX_CANDIDATE_ATTEMPTS]:
        _progress(3, f"Opening LROC NAC {candidate['filename']} (byte-range stream)...")
        path = open_lroc_reader(candidate, budget=budget)

        _progress(4, "Extracting co-located LROC NAC patch...")
        # Prefer the actual per-product resolution (CH2's own PDS4 label,
        # LROC's ODE-reported Map_resolution) over the fixed config ratio --
        # LRO's orbit is a frozen ellipse, so NAC's real GSD varies per
        # acquisition and the config constant is only a rough average.
        candidate_gsd = candidate.get("gsd_m")
        candidate_scale = (ch2_gsd / candidate_gsd) if candidate_gsd else INSTRUMENT_META[instrument]["scale_factor"]
        try:
            patch, candidate_loc_info = extract_lroc_patch(
                path, candidate, lat, lon,
                ref_patch=ch2_patch, scale_factor=candidate_scale,
            )
        except TransferBudgetExceeded as exc:
            # Stop the candidate sweep rather than letting each further
            # candidate re-raise; the budget is shared and already spent.
            budget_error = str(exc)
            break
        if patch is not None:
            best, lroc_path, lroc_patch = candidate, path, patch
            lroc_gsd, scale, loc_info = candidate_gsd, candidate_scale, candidate_loc_info
            transfer.update(_transfer_snapshot(path))
            break
        skipped.append(candidate["filename"])

    if lroc_patch is None:
        reason = "Failed to extract a usable LROC patch."
        if budget_error:
            reason = f"Transfer budget exhausted while reading LROC data: {budget_error}"
        elif skipped:
            reason += f" Skipped {len(skipped)} unusable candidate(s) (no real surface content found): {', '.join(skipped)}."
        return {"status": "FAILED", "reason": reason}

    # ── Step image 1: extracted patches ───────────────────────────────────────
    step_images["extracted"] = str(_step_extracted(
        ch2_patch, lroc_patch, ch2_match, best, label, instrument, lat, lon, loc_info))
    overlap_path = _make_overlap_map(ch2_match, best, lat, lon, label)
    _progress(5, "Detecting keypoints...")

    prov = {
        "ch2_instrument": INSTRUMENT_META[instrument]["full_name"],
        "ch2_nearest_lat": ch2_match["lat"],
        "ch2_nearest_lon": ch2_match["lon"],
        "ch2_scan_line": ch2_match["scan"],
        "ch2_pixel_col": ch2_match["pixel"],
        "ch2_gsd_m": ch2_gsd,
        "lroc_product_id": best["pds_id"],
        "lroc_filename": best["filename"],
        "lroc_gsd_m": lroc_gsd,
        "scale_factor_used": round(scale, 3),
        "lroc_start_time": best["start_time"],
        "lroc_total_candidates": len(candidates),
        "lroc_candidates_tried": len(skipped) + 1,
        "ch2_patch_sha256": hashlib.sha256(ch2_patch.tobytes()).hexdigest()[:16] + "...",
        "lroc_patch_sha256": hashlib.sha256(lroc_patch.tobytes()).hexdigest()[:16] + "...",
        # Whether the LROC scan-line locking found a confident visual
        # correlation peak (best_n >= min_confident_matches) or fell back to
        # the raw pushbroom-geometry estimate. When False, a match failure
        # can't be read as "genuinely hard" -- the patch itself may not be
        # the true corresponding location.
        "lroc_localization": loc_info,
    }

    # ── Registration (keypoints -> raw matches -> MAGSAC++ all computed here) ──
    result = register(ch2_patch, lroc_patch, matcher=matcher)
    if result["status"] == "FAILED" and matcher == "xfeat":
        result = register(ch2_patch, lroc_patch, matcher="sift")

    # ── Step image 2: keypoints ────────────────────────────────────────────────
    step_images["keypoints"] = str(_step_keypoints(ch2_patch, lroc_patch, result, label))
    _progress(6, "Matching features...")

    # ── Step image 3: raw matches ──────────────────────────────────────────────
    step_images["matches"] = str(_step_matches(ch2_patch, lroc_patch, result, label))
    _progress(7, "Verifying with MAGSAC++...")

    # ── Step image 4: inliers (or failure diagnostic) ──────────────────────────
    step_images["inliers"] = str(_step_inliers(ch2_patch, lroc_patch, result, label))
    _progress(8, "Finalizing result...")
    if result["status"] != "SUCCESS":
        reason = result.get("reason", "Registration failed.")
        if loc_info and not loc_info["confident"]:
            pct = int(round(loc_info.get("strip_fraction_searched", 0) * 100))
            if loc_info.get("whole_strip_searched"):
                reason += (
                    f" (The ENTIRE LROC strip was searched -- {loc_info.get('lines_searched', 0)} "
                    f"of {loc_info.get('total_lines', 0)} lines, {pct}% -- and no location "
                    f"correlated above {loc_info['min_confident_matches']} matches "
                    f"(best was {loc_info['best_n']}). This is a genuine content or "
                    f"illumination mismatch, not a mislocalized patch.)"
                )
            else:
                reason += (
                    f" (Localization was NOT confident -- best coarse-scan peak was only "
                    f"{loc_info['best_n']} matches, below the {loc_info['min_confident_matches']} "
                    f"threshold, and only {pct}% of the strip was searched, so this patch is "
                    f"the raw geometry estimate rather than a visually verified location. "
                    f"This failure may reflect a mislocalized patch rather than a genuine "
                    f"content/illumination mismatch.)"
                )
        step_images["final"] = str(_step_failed(ch2_patch, lroc_patch, result, reason, label))
        return {
            "status":      "FAILED",
            "reason":      reason,
            "transfer":    dict(transfer),
            "register_result": result,
            "lroc_candidate": best,
            "raw_patches": _save_raw_patches(ch2_patch, lroc_patch, result, label),
            "step_images": step_images,
            "overlap_map_path": str(overlap_path),
            "provenance":  prov,
        }

    step_images["final"] = str(_step_final(ch2_patch, lroc_patch, result, label, instrument, lat, lon))

    return {
        "status":      "SUCCESS",
        "transfer":    dict(transfer),
        "register_result": result,
        "lroc_candidate": best,
        "raw_patches": _save_raw_patches(ch2_patch, lroc_patch, result, label),
        "metrics":     {
            "matcher":            result["matcher"],
            "n_inliers":          result["n_inliers"],
            "n_raw_matches":      result["n_raw_matches"],
            "inlier_ratio_pct":   result["inlier_ratio_pct"],
            "rmse_px":            result["reprojection_rmse_px"],
            "spatial_uniformity": result["spatial_uniformity"],
            "elapsed_sec":        result["elapsed_sec"],
        },
        "step_images": step_images,
        "overlap_map_path": str(overlap_path),
        "provenance":  prov,
    }


# ── Visualisation helpers ─────────────────────────────────────────────────────

def _step_extracted(
    ch2: np.ndarray, lroc: np.ndarray, ch2_match: dict, lroc_candidate: dict,
    label: str, instrument: str, lat: float, lon: float, loc_info: dict | None,
) -> Path:
    """Step 1: the two extracted patches, side by side, before anything else runs."""
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    out = POSTER_DIR / f"step1_extracted_{label}.png"
    meta = INSTRUMENT_META[instrument]

    fig, axes = plt.subplots(1, 2, figsize=(13, 6.4), facecolor=BG)
    axes[0].imshow(ch2, cmap="gray", vmin=0, vmax=255)
    _style_axes(axes[0],
        f"ISRO Chandrayaan-2 {meta['name']}  ·  moving source\n"
        f"{ch2_match['lat']:.4f}°N, {ch2_match['lon']:.4f}°E  ·  scan {ch2_match['scan']}, pixel {ch2_match['pixel']}",
        CH2_ACCENT, 10)
    axes[1].imshow(lroc, cmap="gray", vmin=0, vmax=255)
    _style_axes(axes[1],
        f"NASA LROC NAC  ·  fixed reference\n"
        f"{lroc_candidate['pds_id']}  ·  {lroc_candidate['filename']}",
        LROC_ACCENT, 10)
    for ax in axes:
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_color(GRID_LINE)

    _suptitle(fig, "Step 1 — Source Imagery Located",
              f"Target {lat:.4f}°N, {lon:.4f}°E")
    if loc_info is not None:
        if loc_info["confident"]:
            txt = f"Scan-line lock: CONFIDENT  ({loc_info['best_n']} correlated matches ≥ {loc_info['min_confident_matches']} threshold)"
            col = GOOD_COLOR
        else:
            txt = f"Scan-line lock: unverified  (best {loc_info['best_n']} matches, below {loc_info['min_confident_matches']} threshold — using geometry estimate)"
            col = "#b5751f"
        fig.text(0.97, 0.955, txt, color=col, fontsize=9, ha="right", fontweight="bold")
    _footer(fig, f"LUNAR-MATCHBENCH  ·  {instrument.upper()} → LROC NAC")
    fig.subplots_adjust(top=0.86, bottom=0.06, left=0.03, right=0.97, wspace=0.06)
    plt.savefig(out, dpi=140, facecolor=BG)
    plt.close(fig)
    return out


def _side_by_side(ch2: np.ndarray, lroc: np.ndarray) -> tuple[np.ndarray, int]:
    view = np.concatenate([ch2, lroc], axis=1)
    return cv2.cvtColor(view, cv2.COLOR_GRAY2RGB), ch2.shape[1]


def _step_keypoints(ch2: np.ndarray, lroc: np.ndarray, result: dict, label: str) -> Path:
    """Step 2: every keypoint the matcher detected on each image, independently."""
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    out = POSTER_DIR / f"step2_keypoints_{label}.png"
    view, offset = _side_by_side(ch2, lroc)
    kpts_m = np.array(result.get("kpts_moving", []))
    kpts_r = np.array(result.get("kpts_ref", []))

    fig, ax = plt.subplots(figsize=(13, 6.6), facecolor=BG)
    ax.imshow(view)
    for pts, dx in ((kpts_m, 0), (kpts_r, offset)):
        for x, y in pts:
            ax.plot(x + dx, y, marker="o", markersize=3, markeredgewidth=0.6,
                     markerfacecolor="none", markeredgecolor=KP_COLOR, alpha=0.85)
    ax.axvline(offset, color=GRID_LINE, linewidth=1)
    _style_axes(ax,
        f"Step 2 — Keypoints Detected   ·   {result.get('matcher', 'XFEAT')}   "
        f"·   {len(kpts_m)} on CH2, {len(kpts_r)} on LROC NAC")
    ax.set_title(ax.get_title(), color=INK, fontsize=12, fontweight="bold", loc="left", pad=10)
    _footer(fig, "LUNAR-MATCHBENCH  ·  independent per-image feature detection, before any matching")
    fig.subplots_adjust(top=0.90, bottom=0.05, left=0.02, right=0.98)
    plt.savefig(out, dpi=140, facecolor=BG)
    plt.close(fig)
    return out


def _step_matches(ch2: np.ndarray, lroc: np.ndarray, result: dict, label: str) -> Path:
    """Step 3: raw correspondences the matcher proposed, before geometric verification."""
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    out = POSTER_DIR / f"step3_matches_{label}.png"
    view, offset = _side_by_side(ch2, lroc)
    mkpts_m = np.array(result.get("mkpts_moving", []))
    mkpts_r = np.array(result.get("mkpts_ref", []))

    fig, ax = plt.subplots(figsize=(13, 6.6), facecolor=BG)
    ax.imshow(view)
    kp_amber = tuple(int(c * 255) for c in matplotlib.colors.to_rgb(KP_COLOR))
    view_bgr = cv2.cvtColor(view, cv2.COLOR_RGB2BGR)
    for p0, p1 in zip(mkpts_m, mkpts_r):
        pt0 = (int(p0[0]), int(p0[1]))
        pt1 = (int(p1[0]) + offset, int(p1[1]))
        cv2.line(view_bgr, pt0, pt1, kp_amber[::-1], 1, cv2.LINE_AA)
    ax.imshow(cv2.cvtColor(view_bgr, cv2.COLOR_BGR2RGB))
    ax.axvline(offset, color=GRID_LINE, linewidth=1)
    n = len(mkpts_m)
    _style_axes(ax, f"Step 3 — Candidate Correspondences   ·   {n} raw matches, not yet geometrically verified")
    ax.set_title(ax.get_title(), color=INK, fontsize=12, fontweight="bold", loc="left", pad=10)
    if n < 8:
        fig.text(0.97, 0.955, "Too few raw matches to attempt geometric verification",
                  color=BAD_COLOR, fontsize=9.5, ha="right", fontweight="bold")
    _footer(fig, "LUNAR-MATCHBENCH  ·  mutual-nearest-neighbour matches, pre-RANSAC")
    fig.subplots_adjust(top=0.90, bottom=0.05, left=0.02, right=0.98)
    plt.savefig(out, dpi=140, facecolor=BG)
    plt.close(fig)
    return out


def _step_inliers(ch2: np.ndarray, lroc: np.ndarray, result: dict, label: str) -> Path:
    """Step 4: MAGSAC++ verdict — green kept as geometrically consistent, red rejected."""
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    out = POSTER_DIR / f"step4_inliers_{label}.png"
    view, offset = _side_by_side(ch2, lroc)
    mkpts_m = np.array(result.get("mkpts_moving", []))
    mkpts_r = np.array(result.get("mkpts_ref", []))
    mask = result.get("inlier_mask")
    mask = np.array(mask, dtype=bool) if mask is not None else np.zeros(len(mkpts_m), dtype=bool)

    good_rgb = tuple(int(c * 255) for c in matplotlib.colors.to_rgb(GOOD_COLOR))
    bad_rgb = tuple(int(c * 255) for c in matplotlib.colors.to_rgb(BAD_COLOR))
    view_bgr = cv2.cvtColor(view, cv2.COLOR_RGB2BGR)
    for i, (p0, p1) in enumerate(zip(mkpts_m, mkpts_r)):
        pt0 = (int(p0[0]), int(p0[1]))
        pt1 = (int(p1[0]) + offset, int(p1[1]))
        col = good_rgb[::-1] if (i < len(mask) and mask[i]) else bad_rgb[::-1]
        cv2.line(view_bgr, pt0, pt1, col, 1, cv2.LINE_AA)

    fig, ax = plt.subplots(figsize=(13, 6.6), facecolor=BG)
    ax.imshow(cv2.cvtColor(view_bgr, cv2.COLOR_BGR2RGB))
    ax.axvline(offset, color=GRID_LINE, linewidth=1)
    n_in = int(mask.sum())
    n_raw = len(mkpts_m)
    title = f"Step 4 — MAGSAC++ Geometric Verification   ·   {n_in} inliers kept / {n_raw} candidates"
    if "reason" in result and result["status"] != "SUCCESS":
        title = "Step 4 — MAGSAC++ Geometric Verification   ·   did not converge"
    _style_axes(ax, title)
    ax.set_title(ax.get_title(), color=INK, fontsize=12, fontweight="bold", loc="left", pad=10)
    fig.text(0.03, 0.05, "● kept (inlier)", color=GOOD_COLOR, fontsize=9.5, fontweight="bold")
    fig.text(0.15, 0.05, "● rejected (outlier)", color=BAD_COLOR, fontsize=9.5, fontweight="bold")
    _footer(fig, "LUNAR-MATCHBENCH  ·  USAC_MAGSAC robust homography estimation")
    fig.subplots_adjust(top=0.90, bottom=0.08, left=0.02, right=0.98)
    plt.savefig(out, dpi=140, facecolor=BG)
    plt.close(fig)
    return out


def _step_final(
    ch2: np.ndarray, lroc: np.ndarray, result: dict,
    label: str, instrument: str, lat: float, lon: float,
) -> Path:
    """Step 5 (success): warped / checkerboard / difference triptych + metric ribbon."""
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    out = POSTER_DIR / f"step5_final_{label}.png"
    meta = INSTRUMENT_META[instrument]

    H_mat = np.array(result["homography"])
    h, w = lroc.shape[:2]
    ch2_reg = cv2.warpPerspective(ch2, H_mat, (w, h))
    checker = make_checkerboard(ch2_reg, lroc, cells=5)
    diff = make_difference_overlay(ch2_reg, lroc)

    fig = plt.figure(figsize=(13, 7.4), facecolor=BG)
    gs = fig.add_gridspec(2, 3, height_ratios=[0.22, 1], hspace=0.12, wspace=0.06)

    m = result
    metrics = [
        (f"{m['n_inliers']}", "inlier tie-points"),
        (f"{m['inlier_ratio_pct']}%", f"of {m['n_raw_matches']} raw"),
        (f"{m['reprojection_rmse_px']} px", "reprojection RMSE"),
        (f"{round(m['spatial_uniformity'] * 100, 1)}%", "spatial coverage"),
        (f"{m['elapsed_sec']} s", f"{m['matcher']} runtime"),
    ]
    ax_metrics = fig.add_subplot(gs[0, :])
    ax_metrics.axis("off")
    n = len(metrics)
    for i, (val, lab) in enumerate(metrics):
        x = (i + 0.5) / n
        ax_metrics.text(x, 0.62, val, ha="center", va="center", fontsize=17,
                         fontweight="bold", color=GOOD_COLOR, transform=ax_metrics.transAxes)
        ax_metrics.text(x, 0.12, lab, ha="center", va="center", fontsize=8.7,
                         color=INK_MUTED, transform=ax_metrics.transAxes)
        if i > 0:
            ax_metrics.axvline(i / n, ymin=0.05, ymax=0.95, color=GRID_LINE, linewidth=1)

    panels = [(ch2_reg, "gray", "Warped CH2 (registered onto LROC frame)"),
              (checker, "gray", "Checkerboard interleave"),
              (diff, None, "Difference overlay (cyan = LROC, magenta = CH2)")]
    for i, (img, cmap, title) in enumerate(panels):
        ax = fig.add_subplot(gs[1, i])
        ax.imshow(img, cmap=cmap, vmin=0 if cmap else None, vmax=255 if cmap else None)
        _style_axes(ax, title, INK, 9.5)
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_color(GRID_LINE)

    _suptitle(fig, "Step 5 — Registration Complete",
              f"{instrument.upper()} → LROC NAC  ·  {lat:.4f}°N, {lon:.4f}°E  ·  {m['matcher']}")
    _footer(fig, "LUNAR-MATCHBENCH  ·  SIH26166  ·  ISRO Chandrayaan-2 ↔ NASA LROC NAC")
    fig.subplots_adjust(top=0.87, bottom=0.04, left=0.03, right=0.97)
    plt.savefig(out, dpi=140, facecolor=BG)
    plt.close(fig)
    return out


def _step_failed(ch2: np.ndarray, lroc: np.ndarray, result: dict, reason: str, label: str) -> Path:
    """Step 5 (failure): an honest, still-professional summary of why registration stopped."""
    POSTER_DIR.mkdir(parents=True, exist_ok=True)
    out = POSTER_DIR / f"step5_final_{label}.png"

    fig = plt.figure(figsize=(13, 4.2), facecolor=BG)
    ax = fig.add_subplot(111)
    ax.axis("off")
    ax.add_patch(plt.Rectangle((0.02, 0.12), 0.96, 0.72, transform=ax.transAxes,
                                facecolor=PANEL_BG, edgecolor=GRID_LINE, linewidth=1))
    ax.text(0.06, 0.68, "Registration did not converge", transform=ax.transAxes,
            fontsize=16, fontweight="bold", color=BAD_COLOR)
    n_raw = len(result.get("mkpts_moving", []))
    n_in = int(np.array(result.get("inlier_mask", [])).sum()) if result.get("inlier_mask") else 0
    ax.text(0.06, 0.5, f"Raw candidate matches: {n_raw}      Verified inliers: {n_in}",
            transform=ax.transAxes, fontsize=10.5, color=INK)
    wrapped = "\n".join(_wrap(reason, 108))
    ax.text(0.06, 0.4, wrapped, transform=ax.transAxes, fontsize=9, color=INK_MUTED, va="top")

    _footer(fig, "LUNAR-MATCHBENCH  ·  a failed run is reported honestly, not hidden — see steps 2-4 for where it broke down")
    fig.subplots_adjust(top=0.98, bottom=0.06, left=0.02, right=0.98)
    plt.savefig(out, dpi=140, facecolor=BG)
    plt.close(fig)
    return out


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width=width) or [""]


def _make_overlap_map(
    ch2_match: dict, lroc_candidate: dict,
    lat: float, lon: float, label: str,
) -> Path:
    """Render a geographic footprint overlap map."""
    import matplotlib.patches as mpatches
    OVERLAP_DIR.mkdir(parents=True, exist_ok=True)
    out = OVERLAP_DIR / f"overlap_{label}.png"

    # Use small estimate for the CH2 patch bbox (~0.085° ≈ 5 km at equator)
    HALF = 0.085
    ch2_box  = BBox(lat - HALF, lat + HALF, lon - HALF, lon + HALF)
    lroc_box = BBox(
        lroc_candidate["lat_min"], lroc_candidate["lat_max"],
        lroc_candidate["lon_min"], lroc_candidate["lon_max"],
    )
    report = overlap_report(ch2_box, lroc_box)
    inter  = ch2_box.intersect(lroc_box)

    fig, ax = plt.subplots(figsize=(9, 7), facecolor=BG)
    ax.set_facecolor(PANEL_BG)

    def _rect(box: BBox, ec, fc, alpha, label_str):
        ax.add_patch(mpatches.FancyBboxPatch(
            (box.lon_min, box.lat_min),
            box.lon_max - box.lon_min, box.lat_max - box.lat_min,
            boxstyle="square,pad=0",
            linewidth=2, edgecolor=ec, facecolor=fc, alpha=alpha, label=label_str,
        ))

    _rect(lroc_box, LROC_ACCENT, LROC_ACCENT, 0.12,
          f"LROC NAC  {lroc_candidate['filename']}")
    _rect(ch2_box, CH2_ACCENT, CH2_ACCENT, 0.30,
          f"CH2 Patch (±{HALF:.3f}°)")
    if inter:
        _rect(inter, GOOD_COLOR, GOOD_COLOR, 0.45,
              f"Overlap  {report['overlap_area_km2']} km² ({report['ch2_overlap_pct']}%)")

    ax.plot(lon, lat, marker="*", markersize=16, color=BAD_COLOR, markeredgecolor=INK,
            markeredgewidth=0.8, label=f"Target ({lat:.3f}°N, {lon:.3f}°E)", zorder=10)

    pad = 0.4
    ax.set_xlim(min(lroc_box.lon_min, ch2_box.lon_min) - pad,
                max(lroc_box.lon_max, ch2_box.lon_max) + pad)
    ax.set_ylim(min(lroc_box.lat_min, ch2_box.lat_min) - pad,
                max(lroc_box.lat_max, ch2_box.lat_max) + pad)
    ax.set_xlabel("Longitude (°E)", color=INK)
    ax.set_ylabel("Latitude (°N)", color=INK)
    ax.tick_params(colors=INK)
    for sp in ax.spines.values():
        sp.set_color(GRID_LINE)
    ax.grid(True, linestyle="--", alpha=0.5, color=GRID_LINE)
    ax.set_title("Geographic Footprint Overlap Verification", color=INK,
                 fontsize=12, fontweight="bold", loc="left")
    ax.legend(loc="upper left", facecolor=PANEL_BG, edgecolor=GRID_LINE,
              labelcolor=INK, fontsize=9)
    plt.tight_layout()
    plt.savefig(out, dpi=130, facecolor=BG)
    plt.close(fig)
    return out
