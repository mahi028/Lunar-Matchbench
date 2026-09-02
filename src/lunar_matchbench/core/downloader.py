"""
Lunar-MatchBench: Science Data Downloader
==========================================
Handles downloading from two independent sources:
  1. ISRO ISSDC (Chandrayaan-2 TMC-2, OHRC)
  2. NASA LROC PDS via ODE REST API

All downloads are resumable (Range header) and cached by filename.
"""
from __future__ import annotations

import csv
import io
import re
import time
import zipfile
from pathlib import Path
from typing import Iterator

import numpy as np
import requests

from lunar_matchbench.config import (
    CH2_DATA_DIR, LROC_DATA_DIR, CH2_SEARCH_DIRS, LROC_SEARCH_DIRS,
    ODE_BASE_URL, ODE_IHID, ODE_IID, ODE_PT, ODE_BBOX_DEG,
    DOWNLOAD_CHUNK, HTTP_TIMEOUT, INSTRUMENT_META,
    LROC_SCAN_STEP, LROC_SCAN_RANGE, PATCH_SIZE,
)
from lunar_matchbench.utils.image import normalise_uint8, resize_to, has_real_content
from lunar_matchbench.utils.geo import DEG_TO_KM_LAT


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _http_download(url: str, dest: Path, verbose: bool = True) -> Path:
    """
    Download url → dest with resume support. Returns dest path.

    Downloads to a `.part` sibling and only renames it to `dest` once the
    transfer completes -- so `dest` existing is proof the download actually
    finished. Writing straight to `dest` (the old behaviour) meant a process
    killed mid-download left a truncated file under the *final* filename,
    which every later call then trusted as a complete, valid cache hit --
    the truncated bytes only surfaced much later as an obscure reshape
    crash deep in patch extraction, far from the actual cause.
    """
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    partial = dest.with_name(dest.name + ".part")
    headers = {}
    existing = 0
    if partial.exists():
        existing = partial.stat().st_size
        headers["Range"] = f"bytes={existing}-"

    with requests.get(url, headers=headers, stream=True, timeout=HTTP_TIMEOUT) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0)) + existing
        mode = "ab" if existing else "wb"
        downloaded = existing
        t0 = time.time()
        with open(partial, mode) as f:
            for chunk in r.iter_content(chunk_size=DOWNLOAD_CHUNK):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if verbose:
                        rate = downloaded / (time.time() - t0 + 1e-6) / 1e6
                        if total > 0:
                            pct = downloaded / total * 100
                            print(f"\r  {pct:5.1f}%  {downloaded/1e6:.1f}/{total/1e6:.1f} MB  @ {rate:.1f} MB/s", end="", flush=True)
                        else:
                            print(f"\r  Downloaded {downloaded/1e6:.1f} MB  @ {rate:.1f} MB/s", end="", flush=True)
    if verbose:
        print()
    partial.rename(dest)
    return dest


# ─── ISRO Chandrayaan-2 ───────────────────────────────────────────────────────

def find_ch2_geometry_match(lat: float, lon: float, instrument: str) -> dict | None:
    """
    Search the ISRO geometry-grid CSV embedded in the CH2 ZIP file to locate
    the scan row / pixel column that is geographically nearest to (lat, lon).
    """
    meta = INSTRUMENT_META[instrument]
    zips = []
    for d in CH2_SEARCH_DIRS:
        if d.exists():
            zips.extend(d.glob(meta["zip_glob"]))
    zips = sorted(list(set(zips)))
    if not zips:
        return None

    best: dict | None = None
    best_dist = float("inf")

    # Longitude degrees shrink to ~0 physical distance near the poles (OHRC's
    # coverage is the lunar south pole), so weight the longitude term by
    # cos(lat) — otherwise a 1 deg lon offset (a few hundred m near the pole)
    # is scored the same as a 1 deg lat offset (~30 km), and the "nearest"
    # grid point found can be many km away from the true closest one.
    lon_weight = np.cos(np.radians(lat))

    for zp in zips:
        with zipfile.ZipFile(zp) as zf:
            csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
            if not csv_names:
                continue
            text = zf.read(csv_names[0]).decode("utf-8", errors="replace")
            for row in csv.DictReader(io.StringIO(text)):
                try:
                    rlat = float(row["Latitude"])
                    rlon = float(row["Longitude"])
                    scan = int(row["Scan"])
                    pixel = int(row["Pixel"])
                    dist = (rlat - lat) ** 2 + ((rlon - lon) * lon_weight) ** 2
                    if dist < best_dist:
                        best_dist = dist
                        best = {
                            "zip_path": zp,
                            "lat": rlat, "lon": rlon,
                            "scan": scan, "pixel": pixel,
                            "dist_deg": dist ** 0.5,
                        }
                except (ValueError, KeyError):
                    continue

    # Strict spatial bounding check: a flat 0.5 deg (~15 km) tolerance is far
    # looser than any instrument's actual patch footprint, so it was silently
    # accepting "nearest" grid points that sit at the edge of the strip (e.g.
    # pixel 0 or samples-1) when the target coordinate is actually just
    # outside the imaged swath. Require the match to fall within roughly
    # half the instrument's own patch footprint instead.
    patch_half_km = PATCH_SIZE * meta["gsd_m"] / 1000 / 2
    max_dist_deg = patch_half_km / DEG_TO_KM_LAT
    if best is None or best["dist_deg"] > max_dist_deg:
        return None

    # The nominal per-instrument GSD in config is a spec value; the actual
    # resolution of a specific product (embedded in its own PDS4 label) can
    # differ by 10-20% depending on the spacecraft's true altitude at
    # acquisition. Use the real value when available so patch footprint math
    # downstream reflects the product actually being read, not the spec sheet.
    best["gsd_m"] = _read_ch2_pixel_resolution(best["zip_path"]) or meta["gsd_m"]
    return best


def _read_ch2_pixel_resolution(zip_path: Path) -> float | None:
    """Read the actual per-product pixel resolution (m/px) from the CH2 PDS4 XML label."""
    with zipfile.ZipFile(zip_path) as zf:
        xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not xml_names:
            return None
        text = zf.read(xml_names[0]).decode("utf-8", errors="replace")
    m = re.search(r"pixel_resolution[^>]*>([\d.]+)<", text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def extract_ch2_patch(match: dict, instrument: str, size: int = PATCH_SIZE) -> np.ndarray | None:
    """
    Read a `size × size` pixel patch centred on the matched scan/pixel from the
    CH2 binary raster inside the ZIP.
    """
    meta = INSTRUMENT_META[instrument]
    zp = match["zip_path"]
    scan, pixel = match["scan"], match["pixel"]
    samples = meta["samples_per_line"]
    bps = 2 if meta["dtype"] != "uint8" else 1
    dtype = meta["dtype"]
    half = size // 2
    # Clamp col_start (and line_start, once the raster's line count is known
    # below) so the requested window never runs past the edge of the strip —
    # otherwise a match near the edge silently returns a non-square, truncated
    # patch instead of the full size x size window centred nearby.
    col_start = max(0, min(pixel - half, samples - size))

    with zipfile.ZipFile(zp) as zf:
        img_names = [n for n in zf.namelist()
                     if n.lower().endswith(".img") and "browse" not in n.lower()]
        if not img_names:
            return None
        total_lines = zf.getinfo(img_names[0]).file_size // (samples * bps)
        line_start = max(0, min(scan - half, max(0, total_lines - size)))
        with zf.open(img_names[0]) as fh:
            fh.seek(line_start * samples * bps)
            raw = fh.read(size * samples * bps)

    arr = np.frombuffer(raw, dtype=dtype).reshape(-1, samples).astype(np.float32)
    crop = arr[:size, col_start:col_start + size]
    if crop.shape[0] < size // 4 or crop.shape[1] < size // 4:
        return None
    return normalise_uint8(crop)


# ─── NASA LROC (ODE API + PDS) ───────────────────────────────────────────────

def discover_lroc_products(lat: float, lon: float,
                            bbox: float = ODE_BBOX_DEG) -> list[dict]:
    """
    Query the NASA ODE REST API for LROC NAC calibrated products (CDRNAC4)
    that intersect the given coordinate box. Returns candidates sorted so
    'strictly containing' products come first, then by centre distance.
    """
    params = {
        "target": "moon", "query": "product", "results": "fmpc",
        "output": "json",
        "minlat": max(-90, lat - bbox), "maxlat": min(90, lat + bbox),
        "westernlon": lon - bbox, "easternlon": lon + bbox,
        "ihid": ODE_IHID, "iid": ODE_IID, "pt": ODE_PT,
    }
    r = requests.get(ODE_BASE_URL, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json().get("ODEResults", {})
    if data.get("Status") != "Success":
        return []

    prods_raw = data["Products"]["Product"]
    if isinstance(prods_raw, dict):
        prods_raw = [prods_raw]

    candidates = []
    for p in prods_raw:
        files = p.get("Product_files", {}).get("Product_file", [])
        if isinstance(files, dict):
            files = [files]
        img_url = next(
            (f["URL"] for f in files
             if f.get("FileName", "").endswith(".IMG")
             and not f.get("FileName", "").endswith(".XML")),
            None
        )
        img_name = next(
            (f["FileName"] for f in files
             if f.get("FileName", "").endswith(".IMG")
             and not f.get("FileName", "").endswith(".XML")),
            None
        )
        if not img_url:
            continue
        lat_min = float(p.get("Minimum_latitude", lat))
        lat_max = float(p.get("Maximum_latitude", lat))
        lon_min = float(p.get("Westernmost_longitude", lon))
        lon_max = float(p.get("Easternmost_longitude", lon))
        inside = (lat_min <= lat <= lat_max) and (lon_min <= lon <= lon_max)
        centre_dist = (((lat_min + lat_max) / 2 - lat) ** 2 +
                       ((lon_min + lon_max) / 2 - lon) ** 2) ** 0.5
        candidates.append({
            "pds_id":      p.get("pdsid"),
            "filename":    img_name,
            "url":         img_url,
            "lat_min":     lat_min, "lat_max": lat_max,
            "lon_min":     lon_min, "lon_max": lon_max,
            "is_inside":   inside,
            "centre_dist": centre_dist,
            "start_time":  p.get("UTC_start_time", ""),
            # Actual per-image ground sample distance (m/px), reported by
            # ODE itself. LRO's orbit is a frozen ellipse (low over the south
            # pole, much higher near the north pole), so NAC resolution
            # genuinely varies frame to frame -- this is not a fixed constant.
            "gsd_m":       float(p["Map_resolution"]) if p.get("Map_resolution") else None,
        })

    candidates.sort(key=lambda x: (not x["is_inside"], x["centre_dist"]))
    return candidates


def download_lroc(candidate: dict, verbose: bool = True) -> Path:
    """Download the LROC NAC .IMG file to the cache directory, checking existing cache first."""
    fname = candidate["filename"]
    for d in LROC_SEARCH_DIRS:
        local_path = d / fname
        if local_path.exists() and local_path.stat().st_size > 0:
            if verbose:
                print(f"  Found cached LROC product: {local_path}")
            return local_path

    LROC_DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = LROC_DATA_DIR / fname
    if verbose:
        print(f"  Downloading {fname} ...")
    return _http_download(candidate["url"], dest, verbose=verbose)


def _parse_pds3_header(path: Path) -> dict:
    """Extract key PDS3 label fields from an attached binary label."""
    with open(path, "rb") as f:
        raw = f.read(65536)
    hdr = raw.decode("latin-1", errors="replace")
    def _get(key: str, default=None):
        m = re.search(rf"{key}\s*=\s*\"?([^\r\n\"]+)\"?", hdr)
        return m.group(1).strip() if m else default
    return {
        "total_lines":    int(_get("LINES", 48128)),
        "total_samples":  int(_get("LINE_SAMPLES", 5064)),
        "label_records":  int(_get("LABEL_RECORDS", 2)),
        "record_bytes":   int(_get("RECORD_BYTES", 5064 * 2)),
        "product_id":     _get("PRODUCT_ID", ""),
        "start_time":     _get("START_TIME", ""),
        "dataset_id":     _get("DATA_SET_ID", ""),
    }


def extract_lroc_patch(
    img_path: Path,
    candidate: dict,
    lat: float,
    lon: float,
    ref_patch: np.ndarray | None = None,
    scale_factor: float = 6,
    size: int = PATCH_SIZE,
) -> tuple[np.ndarray | None, dict]:
    """
    Extract a scale-matched patch from the LROC NAC strip.

    Strategy:
    1. Estimate center scan line from pushbroom geometry.
    2. If `ref_patch` is provided, run a fast SIFT-based coarse-to-fine scan
       (±LROC_SCAN_RANGE lines in LROC_SCAN_STEP increments) to lock onto
       the scan line with peak visual correlation with the CH2 source patch.
    3. Extract raw_window = size * scale_factor lines, downsample to (size, size).

    Returns (patch_or_None, localization_info). localization_info always
    has 'best_n' (peak coarse-scan good-match count) and 'confident'
    (whether that peak cleared MIN_CONFIDENT_MATCHES) -- a False here means
    the patch was extracted at the raw geometry estimate, not a verified
    visual correlation, so a downstream match failure can't be told apart
    from "genuinely hard" without checking this.
    """
    import cv2
    hdr = _parse_pds3_header(img_path)
    total_lines   = hdr["total_lines"]
    total_samples = hdr["total_samples"]
    data_offset   = hdr["label_records"] * hdr["record_bytes"]
    raw_win       = min(int(round(size * scale_factor)), total_lines)
    # Crop the cross-track (sample) axis to the same physical footprint as the
    # along-track (line) axis. Without this, the full LROC line width (e.g.
    # ~5064 samples, ~2.5 km) gets squashed into `size` regardless of
    # scale_factor, which is a mild aspect distortion for TMC-2 (scale_factor
    # 6 means the window already wants close to the full width) but severely
    # anisotropic for OHRC (scale_factor 1 wants a ~1024-sample-wide window,
    # ~5x narrower than the full strip) — the squashed image ends up at a
    # completely different effective GSD per axis, which breaks feature
    # matching against the square, single-scale CH2 patch.
    raw_win_samples = min(int(round(size * scale_factor)), total_samples)

    # ── Geometry estimate ──────────────────────────────────────────────────────
    lat_min, lat_max = candidate["lat_min"], candidate["lat_max"]
    lat_frac = (lat_max - lat) / (lat_max - lat_min + 1e-9)
    approx_center = int(np.clip(lat_frac * total_lines,
                                raw_win // 2, total_lines - raw_win // 2))

    def _read_window(center: int) -> np.ndarray | None:
        ls = max(0, center - raw_win // 2)
        nl = min(raw_win, total_lines - ls)
        skip = data_offset + ls * total_samples * 2
        nbytes = nl * total_samples * 2
        with open(img_path, "rb") as f:
            f.seek(skip)
            raw = f.read(nbytes)
        if len(raw) < nbytes:
            # The file on disk is shorter than its own PDS3 header's LINES
            # field promises (a truncated/corrupted download, most likely --
            # see _http_download's .part-then-rename fix). Treat this window
            # as unavailable rather than letting reshape() raise and crash
            # the whole job; the caller already skips a None window / falls
            # through to the next candidate product.
            return None
        arr = np.frombuffer(raw, dtype="<i2").reshape(nl, total_samples).astype(np.float32)
        if raw_win_samples < total_samples:
            cs = (total_samples - raw_win_samples) // 2
            arr = arr[:, cs:cs + raw_win_samples]
        arr[arr < -32752] = np.nan
        valid = arr[~np.isnan(arr)]
        if len(valid) < 500:
            return None
        # Some CDR products (e.g. calibration/dark frames mis-tagged with a
        # lunar-surface footprint) are just sensor noise around a near-zero
        # baseline. normalise_uint8's percentile stretch would blow that up
        # to look like a fully-textured image, so filter it out here first.
        if not has_real_content(arr):
            return None
        return normalise_uint8(arr)

    # ── Coarse-to-fine descriptor scan ────────────────────────────────────────
    best_center = approx_center
    best_n = 0
    # A genuine correlation peak (verified against a known-good match) scores
    # in the hundreds (220-281 good matches); repetitive lunar crater texture
    # produces spurious "peaks" of ~20-60 good matches essentially anywhere
    # in the strip. Below this floor, a coarse-scan "winner" is noise, not a
    # real match -- trusting it anyway is what let a spurious peak 18,000
    # lines away from the true location win over the (correctly-located but
    # weakly-scored) true region. Below the floor, keep the geometry estimate
    # instead of overriding it with noise.
    MIN_CONFIDENT_MATCHES = 100
    if ref_patch is not None:
        sift = cv2.SIFT_create(nfeatures=1500)
        kp_ref, des_ref = sift.detectAndCompute(ref_patch, None)
        if des_ref is not None and len(kp_ref) > 10:
            bf = cv2.BFMatcher(cv2.NORM_L2)

            # Pass 1: search near the geometry estimate first (most likely to
            # be correct); only look further afield if nothing confident
            # turns up nearby.
            min_c = raw_win // 2
            max_c = total_lines - raw_win // 2
            near_centers = list(range(max(min_c, approx_center - 12500),
                                       min(max_c, approx_center + 12500), LROC_SCAN_STEP))
            far_centers = [c for c in range(min_c, max_c, LROC_SCAN_STEP) if c not in near_centers]

            def _scan(centers):
                nonlocal best_n, best_center
                for cand_center in centers:
                    thumb_raw = _read_window(cand_center)
                    if thumb_raw is None:
                        continue
                    thumb = resize_to(thumb_raw, size)
                    kp_l, des_l = sift.detectAndCompute(thumb, None)
                    if des_l is not None and len(kp_l) > 10:
                        knn = bf.knnMatch(des_ref, des_l, k=2)
                        good = [m for m, n in knn if len((m, n)) == 2 and m.distance < 0.78 * n.distance]
                        if len(good) > best_n:
                            best_n = len(good)
                            best_center = cand_center

            _scan(near_centers)
            if best_n < MIN_CONFIDENT_MATCHES:
                _scan(far_centers)
            if best_n < MIN_CONFIDENT_MATCHES:
                # Nothing confident anywhere in the strip -- trust the
                # geometry estimate rather than a noise-level "winner".
                best_center, best_n = approx_center, 0

            # Pass 2: Fine refinement (step = 500 lines) around coarse peak,
            # only meaningful once a confident coarse peak was actually found.
            if best_n >= MIN_CONFIDENT_MATCHES:
                fine_lo = max(min_c, best_center - 2500)
                fine_hi = min(max_c, best_center + 2500)
                for fine_center in range(fine_lo, fine_hi, 500):
                    thumb_raw = _read_window(fine_center)
                    if thumb_raw is None:
                        continue
                    thumb = resize_to(thumb_raw, size)
                    kp_l, des_l = sift.detectAndCompute(thumb, None)
                    if des_l is not None and len(kp_l) > 10:
                        knn = bf.knnMatch(des_ref, des_l, k=2)
                        good = [m for m, n in knn if len((m, n)) == 2 and m.distance < 0.78 * n.distance]
                        if len(good) > best_n:
                            best_n = len(good)
                            best_center = fine_center

    localization_info = {
        "best_n": best_n,
        "min_confident_matches": MIN_CONFIDENT_MATCHES,
        "confident": best_n >= MIN_CONFIDENT_MATCHES,
        "approx_center_line": approx_center,
        "used_center_line": best_center,
    }

    raw_img = _read_window(best_center)
    if raw_img is None:
        return None, localization_info
    return resize_to(raw_img, size), localization_info
