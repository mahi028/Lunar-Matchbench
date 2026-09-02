"""
Lunar-MatchBench: End-to-End Coordinate-Driven Registration Pipeline (Step 3)
==============================================================================
Takes a user-specified lat/lon coordinate and:
  1. Discovers overlapping Chandrayaan-2 (TMC-2 / OHRC) image subregions from
     already-downloaded science data using the embedded ISRO geometry CSV.
  2. Queries the NASA LROC ODE REST API to find and download a real LROC NAC
     calibrated image that geographically covers the same coordinate.
  3. Extracts co-located patches from both sensors (same physical lunar terrain).
  4. Runs XFeat registration (LROC NAC = Fixed Reference, CH2 = Moving Source).
  5. Generates a full multi-panel verification poster.

Usage:
    python src/pipeline.py --lat 15.0 --lon 289.2 --instrument tmc
    python src/pipeline.py --lat 15.0 --lon 289.2 --instrument ohrc
    python src/pipeline.py --lat 15.0 --lon 289.2 --instrument both
"""

import argparse
import csv
import io
import re
import sys
import time
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np
import requests

from src.lroc_fetch import load_lroc_nac_slice
from src.register import (
    register_images,
    normalize_illumination,
    generate_registration_poster,
)

# =============================================================================
# CONFIGURATION
# =============================================================================

CH2_DATA = Path("issdc_ch2_output/data")
LROC_CACHE = Path("lroc_reference_output/data")
PIPELINE_OUT = Path("registration_output/pipeline")
PIPELINE_OUT.mkdir(parents=True, exist_ok=True)

ODE_URL = "https://oderest.rsl.wustl.edu/live2/"
PATCH_SIZE = 1024   # Pixel size of extracted co-located square patch

# =============================================================================
# STEP 1: CH2 GEOMETRY-BASED COORDINATE LOOKUP
# =============================================================================

def find_ch2_patch_at_coord(lat: float, lon: float, instrument: str = "tmc") -> dict | None:
    """
    Searches the embedded ISRO geometry grid CSV for the pixel row/col closest
    to the requested coordinate. Returns metadata including the zip path and
    the pixel scan/sample offset so we can extract the right subregion.

    Args:
        lat: Target latitude (degrees, planetocentric)
        lon: Target longitude (degrees, 0-360 East)
        instrument: "tmc" or "ohrc"
    """
    inst_pattern = "*_tmc_*.zip" if instrument == "tmc" else "*_ohr_*.zip"
    zips = sorted(CH2_DATA.glob(inst_pattern))
    if not zips:
        print(f"  [WARN] No Chandrayaan-2 {instrument.upper()} zip found in {CH2_DATA}")
        return None

    best = None
    best_dist = float("inf")

    for zp in zips:
        with zipfile.ZipFile(zp) as zf:
            csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
            if not csv_names:
                continue
            content = zf.read(csv_names[0]).decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(content))
            for row in reader:
                try:
                    row_lat = float(row["Latitude"])
                    row_lon = float(row["Longitude"])
                    row_pixel = int(row["Pixel"])
                    row_scan = int(row["Scan"])
                    dist = (row_lat - lat) ** 2 + (row_lon - lon) ** 2
                    if dist < best_dist:
                        best_dist = dist
                        best = {
                            "zip_path": zp,
                            "nearest_lat": row_lat,
                            "nearest_lon": row_lon,
                            "scan": row_scan,
                            "pixel": row_pixel,
                            "dist_deg": dist ** 0.5,
                        }
                except (ValueError, KeyError):
                    continue

    if best:
        print(f"\n  CH2 {instrument.upper()} nearest grid point:")
        print(f"    Target  : lat={lat:.4f}, lon={lon:.4f}")
        print(f"    Nearest : lat={best['nearest_lat']:.4f}, lon={best['nearest_lon']:.4f}")
        print(f"    Distance: {best['dist_deg']:.4f} deg ({best['dist_deg'] * 30:.1f} km ~approx)")
        print(f"    Scan row: {best['scan']}  Pixel col: {best['pixel']}")
        print(f"    Source  : {best['zip_path'].name}")
    return best


def extract_ch2_patch(match: dict, instrument: str = "tmc", size: int = PATCH_SIZE) -> np.ndarray | None:
    """Extracts a size x size pixel patch from the CH2 raster centered at the matched scan/pixel."""
    zp = match["zip_path"]
    scan = match["scan"]
    pixel = match["pixel"]

    with zipfile.ZipFile(zp) as zf:
        img_names = [n for n in zf.namelist()
                     if n.lower().endswith(".img") and "browse" not in n.lower()]
        if not img_names:
            print("  [ERROR] No science .img file found in zip.")
            return None
        img_name = img_names[0]

        if instrument == "tmc":
            # TMC-2: uint16 LE, 4000 samples per line
            samples_per_line = 4000
            bytes_per_sample = 2
            dtype = "<u2"
            half = size // 2
            line_start = max(0, scan - half)
            col_start = max(0, pixel - half)
            skip = line_start * samples_per_line * bytes_per_sample
            with zf.open(img_name) as fh:
                fh.seek(skip)
                raw = fh.read(size * samples_per_line * bytes_per_sample)
            arr = np.frombuffer(raw, dtype=dtype).reshape(-1, samples_per_line).astype(np.float32)
            crop = arr[:size, col_start:col_start + size]
        else:
            # OHRC: uint8, 12000 samples per line
            samples_per_line = 12000
            bytes_per_sample = 1
            dtype = np.uint8
            half = size // 2
            line_start = max(0, scan - half)
            col_start = max(0, pixel - half)
            skip = line_start * samples_per_line * bytes_per_sample
            with zf.open(img_name) as fh:
                fh.seek(skip)
                raw = fh.read(size * samples_per_line * bytes_per_sample)
            arr = np.frombuffer(raw, dtype=dtype).reshape(-1, samples_per_line).astype(np.float32)
            crop = arr[:size, col_start:col_start + size]

    if crop.shape[0] < size // 2 or crop.shape[1] < size // 2:
        print(f"  [WARN] CH2 patch too small: {crop.shape}. Scan offset may be near strip edge.")
        return None

    # Normalize
    p2, p98 = np.percentile(crop, (2, 98))
    norm = np.clip((crop - p2) / (p98 - p2 + 1e-5) * 255.0, 0, 255).astype(np.uint8)
    print(f"  CH2 {instrument.upper()} patch extracted: {norm.shape} px")
    return norm


# =============================================================================
# STEP 2: LROC NAC PRODUCT DISCOVERY VIA ODE REST API
# =============================================================================

def discover_lroc_nac(lat: float, lon: float, bbox_deg: float = 0.5,
                      max_results: int = 10) -> list[dict]:
    """
    Queries the NASA ODE REST API for LROC NAC calibrated images (CDRNAC4)
    covering a coordinate bounding box. Returns a list of candidate products
    sorted by geographic proximity to the target coordinate.

    Reference: https://oderest.rsl.wustl.edu/
    """
    print(f"\n  Querying NASA ODE REST API for LROC NAC at lat={lat}, lon={lon} ± {bbox_deg}°...")

    params = {
        "target": "moon",
        "query": "product",
        "results": "fmpc",
        "output": "json",
        "minlat": lat - bbox_deg,
        "maxlat": lat + bbox_deg,
        "westernlon": lon - bbox_deg,
        "easternlon": lon + bbox_deg,
        "ihid": "LRO",
        "iid": "LROC",
        "pt": "CDRNAC4",
    }

    try:
        r = requests.get(ODE_URL, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [ERROR] ODE API request failed: {e}")
        return []

    status = data.get("ODEResults", {}).get("Status", "")
    if status != "Success":
        err = data.get("ODEResults", {}).get("Error", "Unknown error")
        print(f"  [ERROR] ODE API error: {err}")
        return []

    count = int(data["ODEResults"].get("Count", 0))
    print(f"  Found {count} LROC NAC products at this coordinate.")
    if count == 0:
        return []

    prods_raw = data["ODEResults"]["Products"]["Product"]
    if isinstance(prods_raw, dict):
        prods_raw = [prods_raw]

    candidates = []
    for p in prods_raw:
        files = p.get("Product_files", {}).get("Product_file", [])
        if isinstance(files, dict):
            files = [files]

        img_url = None
        img_filename = None
        for f in files:
            fname = f.get("FileName", "")
            if fname.endswith(".IMG") and not fname.endswith(".XML"):
                img_url = f.get("URL", "")
                img_filename = fname
                break

        if not img_url:
            continue

        lat_min = float(p.get("Minimum_latitude", lat))
        lat_max = float(p.get("Maximum_latitude", lat))
        lon_min = float(p.get("Westernmost_longitude", lon))
        lon_max = float(p.get("Easternmost_longitude", lon))
        
        # Check if target is strictly inside product bounding box
        is_inside = (lat_min <= lat <= lat_max) and (lon_min <= lon <= lon_max)
        
        center_lat = (lat_min + lat_max) / 2
        center_lon = (lon_min + lon_max) / 2
        dist = ((center_lat - lat) ** 2 + (center_lon - lon) ** 2) ** 0.5

        candidates.append({
            "pds_id": p.get("pdsid"),
            "img_filename": img_filename,
            "img_url": img_url,
            "lat_min": lat_min, "lat_max": lat_max,
            "lon_min": lon_min, "lon_max": lon_max,
            "is_inside": is_inside,
            "center_dist_deg": dist,
            "start_time": p.get("UTC_start_time", ""),
        })

    # Sort strictly containing products first, then by distance to center
    candidates.sort(key=lambda x: (not x["is_inside"], x["center_dist_deg"]))

    print(f"  Ranked {len(candidates)} candidates ({sum(1 for c in candidates if c['is_inside'])} strictly contain target point):")
    for i, c in enumerate(candidates[:3]):
        inside_tag = "[CONTAINED]" if c["is_inside"] else "[ADJACENT]"
        print(f"\n  Candidate {i+1} {inside_tag}: {c['img_filename']}")
        print(f"    Lat: {c['lat_min']:.3f} to {c['lat_max']:.3f}")
        print(f"    Lon: {c['lon_min']:.3f} to {c['lon_max']:.3f}")
        print(f"    Center distance: {c['center_dist_deg']:.4f} deg")
        print(f"    Observation time: {c['start_time']}")
        print(f"    URL: {c['img_url'][:80]}...")

    return candidates


# =============================================================================
# STEP 3: LROC NAC DOWNLOAD (RESUMABLE)
# =============================================================================

def download_lroc_product(candidate: dict, out_dir: Path = LROC_CACHE) -> Path | None:
    """Downloads a LROC NAC .IMG file with resumable chunked download."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / candidate["img_filename"]

    if out_path.exists():
        print(f"  [CACHE] LROC NAC already downloaded: {out_path.name}")
        return out_path

    url = candidate["img_url"]
    print(f"\n  Downloading LROC NAC: {candidate['img_filename']}")
    print(f"  URL: {url}")

    # Check size first
    head = requests.head(url, timeout=30)
    total = int(head.headers.get("content-length", 0))
    print(f"  File size: {total / (1024**2):.1f} MB")

    downloaded = 0
    chunk_size = 4 * 1024 * 1024  # 4 MB
    headers = {}

    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            t0 = time.time()
            for chunk in r.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    pct = downloaded / total * 100 if total else 0
                    elapsed = time.time() - t0
                    rate = downloaded / elapsed / (1024 ** 2) if elapsed > 0 else 0
                    print(f"\r  Progress: {pct:.1f}% ({downloaded/(1024**2):.1f} MB / {total/(1024**2):.1f} MB) @ {rate:.1f} MB/s", end="", flush=True)

    print(f"\n  Downloaded: {out_path}")
    return out_path


# =============================================================================
# STEP 4: LROC NAC PATCH EXTRACTION AT COORDINATE
# =============================================================================

def extract_lroc_patch_at_coord(img_path: Path, lat: float, lon: float,
                                 candidate: dict, tmc_ref_patch: np.ndarray = None,
                                 size: int = PATCH_SIZE, scale_factor: float = 5.0) -> np.ndarray | None:
    """
    Extracts a scale-matched patch from the LROC NAC raster closest to the target coordinate.
    Uses an initial pushbroom geometry estimate, followed by a fast coarse-to-fine descriptor
    scan to lock onto the true peak geographic overlap scan line.
    """
    lat_min = candidate["lat_min"]
    lat_max = candidate["lat_max"]
    lon_min = candidate["lon_min"]
    lon_max = candidate["lon_max"]

    with open(img_path, "rb") as f:
        hdr = f.read(65536).decode("latin-1", errors="replace")

    lines_m = re.search(r"LINES\s*=\s*(\d+)", hdr)
    samples_m = re.search(r"LINE_SAMPLES\s*=\s*(\d+)", hdr)
    label_m = re.search(r"LABEL_RECORDS\s*=\s*(\d+)", hdr)
    record_m = re.search(r"RECORD_BYTES\s*=\s*(\d+)", hdr)

    total_lines = int(lines_m.group(1)) if lines_m else 48128
    total_samples = int(samples_m.group(1)) if samples_m else 2532
    label_records = int(label_m.group(1)) if label_m else 2
    record_bytes = int(record_m.group(1)) if record_m else 2532 * 2
    data_offset = label_records * record_bytes

    raw_window_lines = int(min(size * scale_factor, total_lines))
    raw_window_samples = total_samples

    # Initial geometric line estimate
    lat_frac = (lat_max - lat) / (lat_max - lat_min + 1e-6)
    approx_center = int(np.clip(lat_frac * total_lines, raw_window_lines // 2, total_lines - raw_window_lines // 2))

    # Fast coarse-to-fine descriptor scan around the approximate line (+- 10,000 lines in steps of 2,000)
    best_center = approx_center
    if tmc_ref_patch is not None:
        print("  Running coarse-to-fine descriptor search across LROC strip...")
        sift = cv2.SIFT_create(nfeatures=1500)
        kp_tmc, des_tmc = sift.detectAndCompute(tmc_ref_patch, None)
        if des_tmc is not None and len(kp_tmc) > 20:
            bf = cv2.BFMatcher(cv2.NORM_L2)
            search_min = max(0, approx_center - 10000)
            search_max = min(total_lines - raw_window_lines, approx_center + 10000)
            best_matches = 0

            for scan_cand in range(search_min, search_max, 2500):
                skip = data_offset + scan_cand * raw_window_samples * 2
                with open(img_path, "rb") as f:
                    f.seek(skip)
                    raw_cand = f.read(raw_window_lines * raw_window_samples * 2)
                arr_c = np.frombuffer(raw_cand, dtype="<i2").reshape(raw_window_lines, raw_window_samples).astype(np.float32)
                arr_c[arr_c < -32752] = np.nan
                valid_c = arr_c[~np.isnan(arr_c)]
                if len(valid_c) < 1000:
                    continue
                p2, p98 = np.percentile(valid_c, (2, 98))
                norm_c = np.clip((arr_c - p2) / (p98 - p2 + 1e-5) * 255.0, 0, 255)
                norm_c[np.isnan(norm_c)] = 0
                thumb = cv2.resize(norm_c.astype(np.uint8), (1024, 1024), interpolation=cv2.INTER_AREA)

                kp_l, des_l = sift.detectAndCompute(thumb, None)
                if des_l is not None and len(kp_l) > 20:
                    knn = bf.knnMatch(des_tmc, des_l, k=2)
                    good = [m for m, n in knn if len(knn) > 0 and m.distance < 0.75 * n.distance]
                    if len(good) > best_matches:
                        best_matches = len(good)
                        best_center = scan_cand + raw_window_lines // 2
                        print(f"    Line {scan_cand:5d} -> Correlation matches: {len(good)} (New Peak!)")

    line_start = max(0, best_center - raw_window_lines // 2)
    n_lines = min(raw_window_lines, total_lines - line_start)

    print(f"\n  LROC NAC scale-aware window: {n_lines} lines x {raw_window_samples} samples (approx {n_lines*0.8/1000:.1f} km footprint)")
    print(f"  Locked Peak Scan Line: {line_start} / {total_lines}")

    skip = data_offset + line_start * raw_window_samples * 2
    read_bytes = n_lines * raw_window_samples * 2

    with open(img_path, "rb") as f:
        f.seek(skip)
        raw = f.read(read_bytes)

    arr = np.frombuffer(raw, dtype="<i2").reshape(n_lines, raw_window_samples).astype(np.float32)
    arr[arr < -32752] = np.nan   # Mask NULL flags

    valid = arr[~np.isnan(arr)]
    if len(valid) < 100:
        print("  [WARN] LROC patch has insufficient valid pixels.")
        return None

    p2, p98 = np.percentile(valid, (2, 98))
    norm = np.clip((arr - p2) / (p98 - p2 + 1e-5) * 255.0, 0, 255)
    norm[np.isnan(norm)] = 0.0
    u8_img = norm.astype(np.uint8)

    scaled_patch = cv2.resize(u8_img, (size, size), interpolation=cv2.INTER_AREA)
    print(f"  LROC NAC scale-matched patch extracted: {scaled_patch.shape}")
    return scaled_patch


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def run_pipeline(lat: float, lon: float, instrument: str = "tmc"):
    print("=" * 80)
    print(f"LUNAR-MATCHBENCH: END-TO-END COORDINATE-DRIVEN REGISTRATION PIPELINE")
    print(f"Target Coordinate: lat={lat:.4f}°, lon={lon:.4f}°")
    print(f"Source Instrument : Chandrayaan-2 {instrument.upper()}")
    print(f"Reference         : NASA LROC NAC (Calibrated, CDR)")
    print("=" * 80)

    # ---- STEP 1: Find CH2 patch at coordinate ----
    print("\n[STEP 1] Locating Chandrayaan-2 patch at target coordinate...")
    ch2_match = find_ch2_patch_at_coord(lat, lon, instrument=instrument)
    if ch2_match is None:
        print("[ABORT] No CH2 data available at target coordinate.")
        return

    ch2_patch = extract_ch2_patch(ch2_match, instrument=instrument)
    if ch2_patch is None:
        print("[ABORT] Failed to extract CH2 patch.")
        return

    # ---- STEP 2: Discover overlapping LROC NAC from ODE API ----
    print("\n[STEP 2] Querying NASA ODE REST API for overlapping LROC NAC...")
    candidates = discover_lroc_nac(lat, lon)
    if not candidates:
        print("[ABORT] No LROC NAC product found at target coordinate. Try a different AOI.")
        return

    best_candidate = candidates[0]
    print(f"\n  Selected LROC NAC product: {best_candidate['img_filename']}")

    # ---- STEP 3: Download LROC NAC (or use cache) ----
    print("\n[STEP 3] Downloading LROC NAC calibrated image...")
    lroc_path = download_lroc_product(best_candidate)
    if lroc_path is None or not lroc_path.exists():
        print("[ABORT] LROC NAC download failed.")
        return

    # ---- STEP 4: Extract co-located LROC patch ----
    print("\n[STEP 4] Extracting co-located LROC NAC patch at target coordinate...")
    lroc_patch = extract_lroc_patch_at_coord(lroc_path, lat, lon, best_candidate, tmc_ref_patch=ch2_patch)
    if lroc_patch is None:
        print("[ABORT] Failed to extract LROC patch.")
        return

    # ---- STEP 5: Register (CH2 = moving, LROC = fixed reference) ----
    print("\n[STEP 5] Running XFeat Registration (CH2 Moving -> LROC NAC Reference)...")
    res = register_images(ch2_patch, lroc_patch, matcher="xfeat", enforce_uniformity=True)

    if res["status"] != "SUCCESS":
        print(f"[WARN] Registration failed: {res.get('reason')}")
        print("  Attempting fallback with SIFT...")
        res = register_images(ch2_patch, lroc_patch, matcher="sift", enforce_uniformity=True)

    if res["status"] != "SUCCESS":
        print(f"[ABORT] Registration failed even with SIFT: {res.get('reason')}")
        _save_debug_images(ch2_patch, lroc_patch, lat, lon, instrument)
        return

    print(f"\n  Registration Successful!")
    print(f"  Matcher       : {res['matcher']}")
    print(f"  Inliers       : {res['n_inliers']} / {res['n_raw_matches']} ({res['inlier_ratio_pct']}%)")
    print(f"  Reproj. RMSE  : {res['reprojection_rmse_px']} px")
    print(f"  Spatial Ent.  : {res['spatial_uniformity_score']}")
    print(f"  Runtime       : {res['elapsed_sec']} s")

    # ---- STEP 6: Generate verification poster ----
    print("\n[STEP 6] Generating registration verification poster...")
    label = f"lat{lat:.1f}_lon{lon:.1f}_{instrument}"
    poster_path = generate_registration_poster(
        ch2_patch, lroc_patch, res,
        out_file=PIPELINE_OUT / f"registration_{label}.png"
    )
    print(f"\n  -> Poster saved: {poster_path}")
    print("\n" + "=" * 80)
    print("PIPELINE COMPLETE")
    print("=" * 80)
    return res, poster_path


def _save_debug_images(ch2_patch, lroc_patch, lat, lon, instrument):
    """Save raw patches side-by-side for visual inspection when registration fails."""
    import cv2
    label = f"lat{lat:.1f}_lon{lon:.1f}_{instrument}"
    cv2.imwrite(str(PIPELINE_OUT / f"debug_ch2_{label}.png"), ch2_patch)
    cv2.imwrite(str(PIPELINE_OUT / f"debug_lroc_{label}.png"), lroc_patch)
    print(f"  Debug patches saved to {PIPELINE_OUT}/")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Lunar-MatchBench: End-to-End Coordinate-Driven Registration Pipeline"
    )
    parser.add_argument("--lat", type=float, default=15.0,
                        help="Target latitude (degrees N, planetocentric). Default: 15.0")
    parser.add_argument("--lon", type=float, default=289.2,
                        help="Target longitude (degrees E, 0-360). Default: 289.2")
    parser.add_argument("--instrument", type=str, default="tmc", choices=["tmc", "ohrc", "both"],
                        help="Chandrayaan-2 source instrument. Default: tmc")
    args = parser.parse_args()

    if args.instrument == "both":
        run_pipeline(args.lat, args.lon, instrument="tmc")
        run_pipeline(args.lat, args.lon, instrument="ohrc")
    else:
        run_pipeline(args.lat, args.lon, instrument=args.instrument)


if __name__ == "__main__":
    main()
