"""
Lunar-MatchBench: Centralised configuration.
All paths, constants, and tuneable parameters live here.
"""
from pathlib import Path
import os

# ── Project roots ─────────────────────────────────────────────────────────────
# PROJECT_ROOT must NOT be derived from this file's own location (the old
# `parent.parent.parent`) -- that only resolves correctly when running from
# the source tree (uv run / editable install). Once the package is installed
# normally (as the Dockerfile's `pip install .` does), config.py lives under
# .../site-packages/lunar_matchbench/, and that computation silently pointed
# at somewhere inside the Python installation itself (e.g.
# /usr/local/lib/python3.14) instead of the app's working directory -- every
# downloaded file was written there instead of any deployment's actual (and
# possibly volume-mounted) data directory, and the next lookup never found
# it there either, forcing a re-download on every request.
#
# Default to the current working directory: the project root for `uv run` /
# pytest (both invoked from there), and /app inside the Docker image per its
# WORKDIR. LUNAR_MATCHBENCH_DATA_ROOT overrides this for deployments that
# need data on a specific (e.g. separately mounted) path.
PROJECT_ROOT = Path(os.environ.get("LUNAR_MATCHBENCH_DATA_ROOT", Path.cwd()))
DATA_ROOT    = PROJECT_ROOT / "data_store"            # downloaded science data
OUTPUT_ROOT  = PROJECT_ROOT / "outputs"               # registration results
STATIC_ROOT  = Path(__file__).parent / "api" / "static"

# Sub-folders inside data_store/
CH2_DATA_DIR  = DATA_ROOT / "ch2"
LROC_DATA_DIR = DATA_ROOT / "lroc"

# Multiple search directories (handles data_store and legacy output dirs)
CH2_SEARCH_DIRS = [
    CH2_DATA_DIR,
    PROJECT_ROOT / "issdc_ch2_output" / "data",
    PROJECT_ROOT / "issdc_ch2_output",
]
LROC_SEARCH_DIRS = [
    LROC_DATA_DIR,
    PROJECT_ROOT / "lroc_reference_output" / "data",
    PROJECT_ROOT / "lroc_reference_output",
]

# Sub-folders inside outputs/
POSTER_DIR   = OUTPUT_ROOT / "posters"
OVERLAP_DIR  = OUTPUT_ROOT / "overlap"
JOB_DIR      = OUTPUT_ROOT / "jobs"

def ensure_dirs() -> None:
    """Create all required directories if absent."""
    for d in [CH2_DATA_DIR, LROC_DATA_DIR, POSTER_DIR, OVERLAP_DIR, JOB_DIR]:
        d.mkdir(parents=True, exist_ok=True)

# ── Instrument constants ──────────────────────────────────────────────────────
INSTRUMENT_META = {
    "tmc": {
        "name": "TMC-2",
        "full_name": "Terrain Mapping Camera-2 (Fore View)",
        "gsd_m": 5.0,
        "samples_per_line": 4000,
        "dtype": "<u2",
        "zip_glob": "*_tmc_*.zip",
        "scale_factor": 6,      # LROC (0.8 m/px) vs TMC-2 (5 m/px)
    },
    "ohrc": {
        "name": "OHRC",
        "full_name": "Orbiter High Resolution Camera",
        "gsd_m": 0.25,
        "samples_per_line": 12000,
        "dtype": "uint8",
        "zip_glob": "*_ohr_*.zip",
        "scale_factor": 1,      # OHRC and LROC are similar resolution
    },
}

# ── Registration parameters ───────────────────────────────────────────────────
PATCH_SIZE      = 1024          # pixels (uniform square for matcher input)
RESAMPLE_INTERP = "area"        # cv2.INTER_AREA for downsampling
RANSAC_THRESH   = 3.0           # pixels — MAGSAC++ reprojection threshold
MIN_INLIERS     = 8             # abort if fewer verified tie-points
XFEAT_TOP_K     = 4096
SIFT_N_FEATURES = 2000
CLAHE_CLIP      = 3.0
GRID_CELLS      = 8             # for spatial uniformity NMS

# ── ODE / LROC discovery ─────────────────────────────────────────────────────
ODE_BASE_URL    = "https://oderest.rsl.wustl.edu/live2/"
ODE_IHID        = "LRO"
ODE_IID         = "LROC"
ODE_PT          = "CDRNAC4"
ODE_BBOX_DEG    = 0.5
LROC_SCAN_STEP  = 2500          # coarse-to-fine scan step (lines)
LROC_SCAN_RANGE = 10000         # ± lines around geometry estimate

# ── HTTP download ─────────────────────────────────────────────────────────────
DOWNLOAD_CHUNK  = 4 * 1024 * 1024   # 4 MB
HTTP_TIMEOUT    = 60                 # seconds
