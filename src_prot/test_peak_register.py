import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2
import numpy as np
import zipfile
from src.register import register_images, generate_registration_poster, OUT_DIR

# 1. Load TMC-2 patch at scan 91700
tmc_zip = next(Path("issdc_ch2_output/data").glob("*_tmc_*.zip"))
with zipfile.ZipFile(tmc_zip) as zf:
    img_name = next(n for n in zf.namelist() if n.lower().endswith(".img") and "browse" not in n.lower())
    with zf.open(img_name) as fh:
        fh.seek(91700 * 4000 * 2)
        raw = fh.read(1024 * 4000 * 2)
    tmc_arr = np.frombuffer(raw, dtype="<u2").reshape(1024, 4000)[:, 1400:2424].astype(np.float32)

p2, p98 = np.percentile(tmc_arr, (2, 98))
tmc_u8 = np.clip((tmc_arr - p2) / (p98 - p2 + 1e-5) * 255.0, 0, 255).astype(np.uint8)

# 2. Extract LROC NAC patch at TRUE PEAK LINE 27000
lroc_file = Path("lroc_reference_output/data/M1359306139LC.IMG")
with open(lroc_file, "rb") as f:
    f.seek(5064 + 27000 * 5064 * 2)
    raw = f.read(5120 * 5064 * 2)

lroc_arr = np.frombuffer(raw, dtype="<i2").reshape(5120, 5064).astype(np.float32)
lroc_arr[lroc_arr < -32752] = np.nan
valid = lroc_arr[~np.isnan(lroc_arr)]
p2, p98 = np.percentile(valid, (2, 98))
lroc_norm = np.clip((lroc_arr - p2) / (p98 - p2 + 1e-5) * 255.0, 0, 255)
lroc_norm[np.isnan(lroc_norm)] = 0
lroc_u8 = cv2.resize(lroc_norm.astype(np.uint8), (1024, 1024), interpolation=cv2.INTER_AREA)

# 3. Register with XFeat
res = register_images(tmc_u8, lroc_u8, matcher="xfeat", enforce_uniformity=True)
print("=" * 80)
print("TRUE PEAK OVERLAP REGISTRATION RESULT (CH2 TMC-2 <-> NASA LROC NAC)")
print("=" * 80)
print("Status            :", res["status"])
print("Inlier Tie-Points :", res["n_inliers"], "/", res["n_raw_matches"], f"({res['inlier_ratio_pct']}%)")
print("Reprojection RMSE :", res["reprojection_rmse_px"], "px")
print("Spatial Uniformity:", res["spatial_uniformity_score"])
print("Runtime           :", res["elapsed_sec"], "s")

# 4. Generate Poster
poster_path = generate_registration_poster(tmc_u8, lroc_u8, res, out_file=OUT_DIR / "true_peak_overlap_registration.png")
print("Poster saved to ->", poster_path)
