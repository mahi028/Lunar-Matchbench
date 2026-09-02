"""
NASA LRO NAC Reference Data Ingestion Module (Step 1)
=====================================================
Discovers, downloads, and parses NASA Lunar Reconnaissance Orbiter (LRO)
Narrow Angle Camera (NAC) optical reference images from the PDS Archive.

Key Features:
  - Direct PDS archive resolution for Calibrated (CDR) and Raw (EDR) NAC images.
  - Resumable chunked streaming downloader with progress indicator.
  - PDS3 Attached Header parser (geometry, solar angles, GSD resolution).
  - True 1:1 physical aspect ratio science raster extractor and visualizer.

Outputs:
  - lroc_reference_output/data/       (.IMG science data files)
  - lroc_reference_output/discovery/  (lroc_manifest.csv, metadata JSON)
  - lroc_reference_output/visualise/  (1:1 aspect ratio preview PNGs)

Usage:
    python src/lroc_fetch.py --sample                  # Download & visualise curated reference sample
    python src/lroc_fetch.py --product M139237644LC    # Download specific LROC NAC product
    python src/lroc_fetch.py --list                     # List available curated reference products
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from io import BytesIO
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import requests

# =============================================================================
# DIRECTORIES & CONFIGURATION
# =============================================================================

OUTPUT_ROOT = Path("lroc_reference_output")
DATA_DIR = OUTPUT_ROOT / "data"
DISCOVERY_DIR = OUTPUT_ROOT / "discovery"
VIS_DIR = OUTPUT_ROOT / "visualise"
LOG_DIR = OUTPUT_ROOT / "logs"

DATA_DIR.mkdir(parents=True, exist_ok=True)
DISCOVERY_DIR.mkdir(parents=True, exist_ok=True)
VIS_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

CHUNK_SIZE_MB = 8
DOWNLOAD_MAX_RETRIES = 5
DOWNLOAD_RETRY_WAIT = 15

# ASU / NASA PDS Base URL
LROC_PDS_BASE = "https://lroc.sese.asu.edu/data"

# Curated reference products (verified on live PDS server)
CURATED_REFERENCE_PRODUCTS = [
    {
        "PRODUCT_ID": "M139237644LC",
        "CAMERA": "NACL (Narrow Angle Camera Left)",
        "LEVEL": "CDR (Calibrated Data Record)",
        "GSD_RES_M": 0.52,
        "ORBIT": 5653,
        "DATE": "2010-09-16",
        "RELATIVE_PATH": "LRO-L-LROC-3-CDR-V1.0/LROLRC_1005/DATA/MAP/2010259/NAC/M139237644LC.IMG",
        "DESC": "Calibrated optical reflectance frame with prominent lunar crater ray systems."
    },
    {
        "PRODUCT_ID": "M139237644RC",
        "CAMERA": "NACR (Narrow Angle Camera Right)",
        "LEVEL": "CDR (Calibrated Data Record)",
        "GSD_RES_M": 0.52,
        "ORBIT": 5653,
        "DATE": "2010-09-16",
        "RELATIVE_PATH": "LRO-L-LROC-3-CDR-V1.0/LROLRC_1005/DATA/MAP/2010259/NAC/M139237644RC.IMG",
        "DESC": "Companion right-camera frame for stereo / wide-swath coverage."
    },
    {
        "PRODUCT_ID": "M139237777LC",
        "CAMERA": "NACL (Narrow Angle Camera Left)",
        "LEVEL": "CDR (Calibrated Data Record)",
        "GSD_RES_M": 0.55,
        "ORBIT": 5653,
        "DATE": "2010-09-16",
        "RELATIVE_PATH": "LRO-L-LROC-3-CDR-V1.0/LROLRC_1005/DATA/MAP/2010259/NAC/M139237777LC.IMG",
        "DESC": "Overlapping orbital strip observation."
    }
]


# =============================================================================
# PDS3 HEADER PARSER
# =============================================================================

def parse_pds3_label(header_text: str) -> dict:
    """Parse key telemetry & image geometry fields from PDS3 attached label."""
    meta = {}
    patterns = {
        "PRODUCT_ID": r"PRODUCT_ID\s*=\s*\"?([A-Za-z0-9_]+)\"?",
        "DATA_SET_ID": r"DATA_SET_ID\s*=\s*\"?([^\r\n\"]+)\"?",
        "START_TIME": r"START_TIME\s*=\s*\"?([^\r\n\"]+)\"?",
        "STOP_TIME": r"STOP_TIME\s*=\s*\"?([^\r\n\"]+)\"?",
        "ORBIT_NUMBER": r"ORBIT_NUMBER\s*=\s*(\d+)",
        "LINES": r"LINES\s*=\s*(\d+)",
        "LINE_SAMPLES": r"LINE_SAMPLES\s*=\s*(\d+)",
        "SAMPLE_BITS": r"SAMPLE_BITS\s*=\s*(\d+)",
        "SAMPLE_TYPE": r"SAMPLE_TYPE\s*=\s*\"?([A-Za-z0-9_]+)\"?",
        "RECORD_BYTES": r"RECORD_BYTES\s*=\s*(\d+)",
        "LABEL_RECORDS": r"LABEL_RECORDS\s*=\s*(\d+)",
        "IMAGE_RECORD_OFFSET": r"\^IMAGE\s*=\s*(\d+)",
        "INCIDENCE_ANGLE": r"INCIDENCE_ANGLE\s*=\s*([\d\.\-]+)",
        "EMISSION_ANGLE": r"EMISSION_ANGLE\s*=\s*([\d\.\-]+)",
        "PHASE_ANGLE": r"PHASE_ANGLE\s*=\s*([\d\.\-]+)",
        "SUB_SOLAR_AZIMUTH": r"SUB_SOLAR_AZIMUTH\s*=\s*([\d\.\-]+)",
        "RESOLUTION": r"RESOLUTION\s*=\s*([\d\.\-]+)",
    }

    for key, pat in patterns.items():
        m = re.search(pat, header_text)
        if m:
            val = m.group(1).strip()
            if val.replace(".", "", 1).replace("-", "", 1).isdigit():
                meta[key] = float(val) if "." in val else int(val)
            else:
                meta[key] = val

    return meta


# =============================================================================
# DOWNLOADER CLASS
# =============================================================================

class LrocDownloader:
    """Handles authenticated/public HTTP downloads with chunked streaming,
    range resumption, and automatic retries."""

    def __init__(self, session: requests.Session = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "LunarMatchbench-LROC/1.0 (Research Pipeline)",
            "Accept": "*/*",
        })

    def download_file(self, url: str, target_path: Path) -> bool:
        """Download file to target_path with resume support."""
        partial_path = Path(str(target_path) + ".part")
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if target_path.exists() and target_path.stat().st_size > 0:
            print(f"  [cached] {target_path.name} already exists ({target_path.stat().st_size / (1024**2):.2f} MB). Skipping download.")
            return True

        for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
            try:
                resume_from = 0
                headers = {}
                if partial_path.exists():
                    resume_from = partial_path.stat().st_size
                    if resume_from > 0:
                        headers["Range"] = f"bytes={resume_from}-"

                print(f"  Connecting to {url}...")
                with self.session.get(url, headers=headers, stream=True, timeout=(20, 600), allow_redirects=True) as resp:
                    if resp.status_code not in (200, 206):
                        raise RuntimeError(f"HTTP error {resp.status_code} ({resp.reason})")

                    total_length = resp.headers.get("content-length")
                    total_mb_str = f" / {int(total_length) / (1024**2):.1f} MB" if total_length else ""

                    mode = "ab" if resume_from > 0 and resp.status_code == 206 else "wb"
                    downloaded = resume_from

                    print(f"  Streaming {target_path.name}...")
                    with open(partial_path, mode) as f:
                        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE_MB * 1024 * 1024):
                            if not chunk:
                                continue
                            f.write(chunk)
                            downloaded += len(chunk)
                            print(f"\r    Downloaded: {downloaded / (1024**2):.2f} MB{total_mb_str}", end="", flush=True)

                print("\n  -> Download complete.")
                if partial_path.exists():
                    if target_path.exists():
                        target_path.unlink()
                    partial_path.rename(target_path)
                return True

            except Exception as e:
                print(f"\n  [attempt {attempt}/{DOWNLOAD_MAX_RETRIES} failed]: {e}")
                if attempt < DOWNLOAD_MAX_RETRIES:
                    time.sleep(DOWNLOAD_RETRY_WAIT)

        return False


# =============================================================================
# SCIENCE RASTER EXTRACTOR & VISUALISER
# =============================================================================

def load_lroc_nac_slice(img_path: Path, center_line: int = None, slice_size: int = 2532) -> tuple[np.ndarray, dict]:
    """Reads the attached PDS3 label and streams a 1:1 square crop from the .IMG raster."""
    with open(img_path, "rb") as f:
        # Read the first 64 KB to extract PDS3 label
        raw_header = f.read(65536).decode("latin-1", errors="replace")
        meta = parse_pds3_label(raw_header)

        rec_bytes = int(meta.get("RECORD_BYTES", 2532))
        lbl_recs = int(meta.get("LABEL_RECORDS", 2))
        lines = int(meta.get("LINES", 48128))
        samples = int(meta.get("LINE_SAMPLES", 2532))

        # Check for 16-bit vs 8-bit
        is_16bit = "16" in raw_header or meta.get("SAMPLE_BITS") == 16 or "LSB_INTEGER" in raw_header
        dt = np.dtype("<i2" if is_16bit else np.uint8)
        bytes_per_sample = dt.itemsize

        header_offset = lbl_recs * rec_bytes
        bytes_per_line = samples * bytes_per_sample

        if center_line is None:
            # Quick scan to find the region with highest contrast / terrain detail
            stds = []
            scan_step = max(1000, lines // 30)
            for test_line in range(0, lines - slice_size, scan_step):
                f.seek(header_offset + (test_line * bytes_per_line))
                chunk = np.frombuffer(f.read(min(bytes_per_line * 100, 2532 * 200)), dtype=dt).astype(np.float32)
                if is_16bit:
                    valid = chunk >= -32752
                    std_val = chunk[valid].std() if valid.sum() > 50 else 0
                else:
                    std_val = chunk.std()
                stds.append((std_val, test_line))
            stds.sort(reverse=True)
            center_line = stds[0][1] + (slice_size // 2) if stds else lines // 2

        start_line = max(0, center_line - (slice_size // 2))
        actual_lines = min(slice_size, lines - start_line)

        # Seek to start of square slice
        skip_offset = header_offset + (start_line * bytes_per_line)
        f.seek(skip_offset)

        raw_bytes = f.read(actual_lines * bytes_per_line)
        arr = np.frombuffer(raw_bytes, dtype=dt).reshape(actual_lines, samples).astype(np.float32)

        if is_16bit:
            valid_mask = arr >= -32752
            scale = 3.05185094759972e-05
            m_scale = re.search(r"SCALING_FACTOR\s*=\s*([\d\.\-eE]+)", raw_header)
            if m_scale:
                scale = float(m_scale.group(1))
            arr = arr * scale
            arr[~valid_mask] = np.nan

        meta["SLICE_START_LINE"] = start_line
        meta["SLICE_LINES"] = actual_lines
        meta["ACTUAL_SAMPLES"] = samples
        return arr, meta


def visualise_lroc_nac(img_path: Path, out_path: Path) -> Path:
    """Extracts a square science slice and saves a publication-quality 1:1 aspect ratio PNG."""
    print(f"Visualising LROC NAC reference image: {img_path.name}...")
    arr, meta = load_lroc_nac_slice(img_path, slice_size=2532)

    # 2nd-98th percentile linear stretch ignoring NaNs
    valid_vals = arr[~np.isnan(arr)]
    if len(valid_vals) > 10:
        p2, p98 = np.percentile(valid_vals, (2, 98))
        norm = np.clip((arr - p2) / (p98 - p2 + 1e-5), 0.0, 1.0)
        norm[np.isnan(norm)] = 0.0
    else:
        norm = np.zeros_like(arr)

    fig, ax = plt.subplots(figsize=(8, 8), facecolor="#0b0b0b")
    ax.imshow(norm, cmap="gray", aspect="equal")

    prod_id = meta.get("PRODUCT_ID", img_path.stem)
    res_m = meta.get("RESOLUTION", 0.5)
    sun_az = meta.get("SUB_SOLAR_AZIMUTH", "N/A")
    inc_ang = meta.get("INCIDENCE_ANGLE", "N/A")

    title_text = (
        f"LROC NAC Reference (Lunar Reconnaissance Orbiter Camera)\n"
        f"Product: {prod_id} | 1:1 Aspect Ratio ({arr.shape[1]}x{arr.shape[0]} px)"
    )
    ax.set_title(title_text, color="white", fontsize=12, fontweight="bold", pad=12)

    caption = (
        f"Native GSD: ~{res_m} m/px | Incidence: {inc_ang}° | Sun Azimuth: {sun_az}°\n"
        f"File: {img_path.name} (PDS3 CDR Calibrated Radiance I/F)"
    )
    ax.set_xlabel(caption, color="#aaaaaa", fontsize=8.5, labelpad=8)
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for spine in ax.spines.values():
        spine.set_edgecolor("#333333")

    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor="#0b0b0b")
    plt.close()
    print(f"  -> Visualisation saved: {out_path}")
    return out_path


# =============================================================================
# MAIN CLI
# =============================================================================

def list_curated_products():
    print("\n" + "=" * 80)
    print("NASA LRO NAC REFERENCE ARCHIVE - CURATED PRODUCTS")
    print("=" * 80)
    for idx, p in enumerate(CURATED_REFERENCE_PRODUCTS, start=1):
        print(f"\n[{idx}] {p['PRODUCT_ID']} ({p['CAMERA']})")
        print(f"    Level: {p['LEVEL']} | GSD: ~{p['GSD_RES_M']} m/px | Date: {p['DATE']}")
        print(f"    Description: {p['DESC']}")
        print(f"    PDS URL: {LROC_PDS_BASE}/{p['RELATIVE_PATH']}")
    print("\n" + "=" * 80)


def fetch_product(product_entry: dict) -> tuple[Path, Path]:
    prod_id = product_entry["PRODUCT_ID"]
    rel_path = product_entry["RELATIVE_PATH"]
    full_url = f"{LROC_PDS_BASE}/{rel_path}"
    local_file = DATA_DIR / f"{prod_id}.IMG"
    vis_file = VIS_DIR / f"{prod_id}_1to1_detail.png"

    print(f"\n--- Ingesting LROC Reference Product: {prod_id} ---")
    print(f"Source URL: {full_url}")
    print(f"Target Path: {local_file}")

    downloader = LrocDownloader()
    success = downloader.download_file(full_url, local_file)
    if not success:
        raise RuntimeError(f"Failed to download LROC reference image from {full_url}")

    vis_path = visualise_lroc_nac(local_file, vis_file)

    # Save manifest
    manifest_csv = DISCOVERY_DIR / "lroc_manifest.csv"
    file_exists = manifest_csv.exists()
    with open(manifest_csv, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["PRODUCT_ID", "CAMERA", "LEVEL", "GSD_RES_M", "LOCAL_FILE", "VIS_FILE", "SOURCE_URL"])
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "PRODUCT_ID": prod_id,
            "CAMERA": product_entry.get("CAMERA", ""),
            "LEVEL": product_entry.get("LEVEL", ""),
            "GSD_RES_M": product_entry.get("GSD_RES_M", 0.5),
            "LOCAL_FILE": str(local_file.resolve()),
            "VIS_FILE": str(vis_path.resolve()),
            "SOURCE_URL": full_url,
        })
    print(f"Updated LROC reference manifest: {manifest_csv}")
    return local_file, vis_path


def main():
    parser = argparse.ArgumentParser(description="NASA LRO NAC Reference Data Ingestion Module (Step 1)")
    parser.add_argument("--sample", action="store_true", help="Download and visualize curated LROC NAC reference sample")
    parser.add_argument("--product", type=str, default=None, help="Product ID (e.g. M139237644LC)")
    parser.add_argument("--list", action="store_true", help="List curated reference products")
    args = parser.parse_args()

    if args.list:
        list_curated_products()
        return

    if args.product:
        entry = next((p for p in CURATED_REFERENCE_PRODUCTS if p["PRODUCT_ID"].upper() == args.product.upper()), None)
        if not entry:
            # Construct standard relative path if not in curated list
            pid = args.product.upper()
            entry = {
                "PRODUCT_ID": pid,
                "CAMERA": "NACL" if "L" in pid else "NACR",
                "LEVEL": "CDR",
                "GSD_RES_M": 0.5,
                "RELATIVE_PATH": f"LRO-L-LROC-3-CDR-V1.0/LROLRC_1005/DATA/MAP/2010259/NAC/{pid}.IMG",
            }
        fetch_product(entry)
        return

    # Default: fetch sample reference frame
    print("Defaulting to sample LROC NAC reference product (M139237644LC)...")
    fetch_product(CURATED_REFERENCE_PRODUCTS[0])


if __name__ == "__main__":
    main()
