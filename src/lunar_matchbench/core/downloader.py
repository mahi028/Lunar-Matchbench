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
    MAX_LROC_WINDOWS, LROC_SEARCH_MARGIN,
    LROC_PROBE_COUNT, LROC_PROBE_STEP_MIN, LROC_FINE_STEP_MIN,
)
from lunar_matchbench.utils.image import normalise_uint8, resize_to, has_real_content
from lunar_matchbench.utils.geo import DEG_TO_KM_LAT
from lunar_matchbench.core.streaming import LocalLrocReader, LrocStream


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _http_download(url: str, dest: Path, verbose: bool = True) -> Path:
    """Download url → dest with resume support. Returns dest path."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    headers = {}
    existing = 0
    if dest.exists():
        existing = dest.stat().st_size
        headers["Range"] = f"bytes={existing}-"

    with requests.get(url, headers=headers, stream=True, timeout=HTTP_TIMEOUT) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0)) + existing
        mode = "ab" if existing else "wb"
        downloaded = existing
        t0 = time.time()
        with open(dest, mode) as f:
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


def find_ch2_geometry_match_streamed(zstream, lat: float, lon: float,
                                     instrument: str) -> dict | None:
    """Nearest-grid-point search against a remote ZIP's geometry CSV.

    The CSV is ~0.8 MB compressed, so resolving a coordinate to a scan line
    costs two small ranged reads rather than the whole archive.
    """
    meta = INSTRUMENT_META[instrument]
    csv_names = [n for n in zstream.namelist() if n.endswith(".csv")]
    if not csv_names:
        return None
    text = zstream.member_bytes(csv_names[0]).decode("utf-8", errors="replace")

    best: dict | None = None
    best_dist = float("inf")
    # Longitude degrees shrink toward the poles, so weight by cos(lat) or a
    # 1 deg lon offset scores the same as a 1 deg lat offset (~30 km).
    lon_weight = np.cos(np.radians(lat))
    for row in csv.DictReader(io.StringIO(text)):
        try:
            rlat = float(row["Latitude"])
            rlon = float(row["Longitude"])
            dist = (rlat - lat) ** 2 + ((rlon - lon) * lon_weight) ** 2
            if dist < best_dist:
                best_dist = dist
                best = {
                    "zstream": zstream,
                    "lat": rlat, "lon": rlon,
                    "scan": int(row["Scan"]), "pixel": int(row["Pixel"]),
                    "dist_deg": dist ** 0.5,
                }
        except (ValueError, KeyError):
            continue

    patch_half_km = PATCH_SIZE * meta["gsd_m"] / 1000 / 2
    if best is None or best["dist_deg"] > patch_half_km / DEG_TO_KM_LAT:
        return None

    # Prefer the product's own recorded resolution over the spec constant.
    best["gsd_m"] = meta["gsd_m"]
    xml_names = [n for n in zstream.namelist() if n.lower().endswith(".xml")]
    if xml_names:
        xml = zstream.member_bytes(xml_names[0]).decode("utf-8", errors="replace")
        m = re.search(r"pixel_resolution[^>]*>([\d.]+)<", xml, re.IGNORECASE)
        if m:
            best["gsd_m"] = float(m.group(1))
    return best


def extract_ch2_patch(match: dict, instrument: str, size: int = PATCH_SIZE) -> np.ndarray | None:
    """
    Read a `size × size` pixel patch centred on the matched scan/pixel from the
    CH2 binary raster inside the ZIP.
    """
    meta = INSTRUMENT_META[instrument]
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

    # Remote-zip path: read only the lines needed, inflating from the member
    # start and stopping there, rather than transferring the whole archive.
    zstream = match.get("zstream")
    if zstream is not None:
        img_names = [n for n in zstream.namelist()
                     if n.lower().endswith(".img") and "browse" not in n.lower()]
        if not img_names:
            return None
        total_lines = zstream.member_info(img_names[0])["file_size"] // (samples * bps)
        line_start = max(0, min(scan - half, max(0, total_lines - size)))
        arr = zstream.img_lines(img_names[0], samples, dtype, line_start, size)
        crop = arr[:size, col_start:col_start + size]
        if crop.shape[0] < size // 4 or crop.shape[1] < size // 4:
            return None
        return normalise_uint8(crop)

    zp = match["zip_path"]
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


def open_lroc_reader(candidate: dict, prefer_stream: bool = True):
    """Return a line-window reader for an LROC product.

    A cached local copy always wins -- it is faster and costs no bandwidth.
    Otherwise the product is read over HTTP byte ranges rather than downloaded,
    which for a TMC-scale window is ~39 MB instead of ~529 MB.
    """
    fname = candidate["filename"]
    for d in LROC_SEARCH_DIRS:
        local = d / fname
        if local.exists() and local.stat().st_size > 0:
            return LocalLrocReader(local)
    if not prefer_stream:
        return LocalLrocReader(download_lroc(candidate, verbose=False))
    return LrocStream.open(candidate["url"])


def extract_lroc_patch(
    reader,
    candidate: dict,
    lat: float,
    lon: float,
    ref_patch: np.ndarray | None = None,
    scale_factor: float = 6,
    size: int = PATCH_SIZE,
) -> tuple[np.ndarray | None, dict]:
    """
    Extract a scale-matched patch from an LROC NAC strip.

    Strategy (geometry-first):
      1. Estimate the centre scan line from the product's own footprint.
      2. Fetch ONE oversized window around that estimate -- a single read.
      3. Run the coarse-to-fine descriptor search entirely INSIDE that buffer.
         Probing in memory is what makes this affordable over HTTP: the old
         approach re-read the source for every probe, which is free on a local
         file and ruinous across ~30 network fetches.
      4. Only if no confident peak turns up, slide to an adjacent window --
         capped at MAX_LROC_WINDOWS so a hopeless coordinate cannot run away.

    Returns (patch_or_None, localization_info). `confident` False means the
    patch came from the raw geometry estimate rather than a verified visual
    correlation, so a downstream match failure cannot be read as "genuinely
    hard" without checking this first.
    """
    import cv2

    total_lines = reader.total_lines
    total_samples = reader.total_samples
    raw_win = min(int(round(size * scale_factor)), total_lines)
    raw_win_samples = min(int(round(size * scale_factor)), total_samples)
    margin = int(raw_win * LROC_SEARCH_MARGIN)
    buffer_lines = min(raw_win + 2 * margin, total_lines)

    # -- Geometry estimate ----------------------------------------------------
    lat_min, lat_max = candidate["lat_min"], candidate["lat_max"]
    lat_frac = (lat_max - lat) / (lat_max - lat_min + 1e-9)
    approx_center = int(np.clip(lat_frac * total_lines,
                                raw_win // 2,
                                max(raw_win // 2, total_lines - raw_win // 2)))

    def _crop(arr: np.ndarray) -> np.ndarray | None:
        """Centre-crop the sample axis, then validate and normalise."""
        if arr.shape[0] < raw_win // 2:
            return None
        if raw_win_samples < total_samples:
            cs = (total_samples - raw_win_samples) // 2
            arr = arr[:, cs:cs + raw_win_samples]
        valid = arr[~np.isnan(arr)]
        if len(valid) < 500:
            return None
        # A percentile stretch would happily turn a dark calibration frame into
        # something that looks fully textured, so screen the raw values for real
        # spatial structure before normalising.
        if not has_real_content(arr):
            return None
        return normalise_uint8(arr)

    MIN_CONFIDENT_MATCHES = 100
    best_center = approx_center
    best_n = 0
    windows_fetched = 0
    chosen: np.ndarray | None = None

    sift = bf = kp_ref = des_ref = None
    if ref_patch is not None:
        sift = cv2.SIFT_create(nfeatures=1500)
        kp_ref, des_ref = sift.detectAndCompute(ref_patch, None)
        if des_ref is None or len(kp_ref) <= 10:
            sift = None
        else:
            bf = cv2.BFMatcher(cv2.NORM_L2)

    def _score(thumb_src: np.ndarray) -> int:
        thumb = resize_to(thumb_src, size)
        kp_l, des_l = sift.detectAndCompute(thumb, None)
        if des_l is None or len(kp_l) <= 10:
            return 0
        knn = bf.knnMatch(des_ref, des_l, k=2)
        return len([m for m, n in knn if m.distance < 0.78 * n.distance])

    # Windows are tried outward from the geometry estimate.
    offsets = [0]
    for k in range(1, MAX_LROC_WINDOWS):
        offsets.append(buffer_lines * (k if k % 2 else -k))

    for off in offsets[:MAX_LROC_WINDOWS]:
        buf_start = int(np.clip(approx_center + off - buffer_lines // 2,
                                0, max(0, total_lines - buffer_lines)))
        buf = reader.read_lines(buf_start, buffer_lines)
        windows_fetched += 1
        if buf.shape[0] < raw_win // 2:
            continue

        if sift is None:
            chosen = _crop(buf[:raw_win])
            best_center = buf_start + raw_win // 2
            break

        # Probe centres inside the already-loaded buffer -- no further reads.
        # LROC_SCAN_STEP (2500 lines) was sized for the old design where every
        # probe cost a fresh read of the source. Probing an in-memory buffer
        # costs only a SIFT pass, so the step is derived from the span instead:
        # a fixed 2500 would fit just one probe into a small buffer and miss the
        # match entirely.
        lo = raw_win // 2
        hi = max(lo + 1, buf.shape[0] - raw_win // 2)
        step = max(LROC_PROBE_STEP_MIN, (hi - lo) // LROC_PROBE_COUNT)
        for local_center in range(lo, hi, step):
            cand = _crop(buf[local_center - raw_win // 2:local_center + raw_win // 2])
            if cand is None:
                continue
            n_good = _score(cand)
            if n_good > best_n:
                best_n, best_center, chosen = n_good, buf_start + local_center, cand

        if best_n >= MIN_CONFIDENT_MATCHES:
            # Fine pass, still inside this buffer.
            centre_local = best_center - buf_start
            fine_step = max(LROC_FINE_STEP_MIN, step // 5)
            for local_center in range(max(lo, centre_local - step),
                                      min(hi, centre_local + step), fine_step):
                cand = _crop(buf[local_center - raw_win // 2:local_center + raw_win // 2])
                if cand is None:
                    continue
                n_good = _score(cand)
                if n_good > best_n:
                    best_n, best_center, chosen = n_good, buf_start + local_center, cand
            break

    confident = best_n >= MIN_CONFIDENT_MATCHES
    if not confident:
        # Nothing convincing anywhere we looked. Repetitive crater texture
        # produces spurious peaks of ~20-60 matches essentially anywhere in a
        # strip, so trusting a "winner" below the floor is how a patch 18,000
        # lines from the truth once won. Fall back to the geometry estimate.
        best_center = approx_center
        best_n = 0
        buf_start = int(np.clip(approx_center - raw_win // 2, 0,
                                max(0, total_lines - raw_win)))
        chosen = _crop(reader.read_lines(buf_start, raw_win))

    localization_info = {
        "best_n": best_n,
        "min_confident_matches": MIN_CONFIDENT_MATCHES,
        "confident": confident,
        "approx_center_line": approx_center,
        "used_center_line": best_center,
        "windows_fetched": windows_fetched,
        "window_lines": buffer_lines,
    }
    if chosen is None:
        return None, localization_info
    return resize_to(chosen, size), localization_info
