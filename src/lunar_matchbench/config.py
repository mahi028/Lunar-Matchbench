"""
Lunar-MatchBench: Centralised configuration.
All paths, constants, and tuneable parameters live here.
"""
from pathlib import Path
import os

# ── Project roots ─────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
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

# HTTP byte-range cache. Keyed by (url, offset, length), so a re-run of the
# same coordinate serves from disk instead of re-fetching.
CACHE_DIR    = DATA_ROOT / "cache"

def ensure_dirs() -> None:
    """Create all required directories if absent."""
    for d in [CH2_DATA_DIR, LROC_DATA_DIR, POSTER_DIR, OVERLAP_DIR, JOB_DIR, CACHE_DIR]:
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

# ── Range streaming ──────────────────────────────────────────────────────────
# Measured against pds.lroc.im-ldi.com on 2026-09-02: the host honours single
# byte-ranges (206, accurate Content-Range) but silently IGNORES comma-separated
# multi-ranges -- a 3 KB multi-range request came back 200 with all 529 MB.
# Never batch ranges; issue one contiguous interval per request.
RANGE_CHUNK_MAX     = 64 * 1024 * 1024   # refuse absurd single reads
# Archive hosts drop long-lived connections; a run should survive one blip.
RANGE_MAX_RETRIES   = 3                  # attempts per ranged read
RANGE_RETRY_WAIT    = 1.5                # seconds, multiplied by attempt
LROC_SEARCH_MARGIN  = 0.5                # extra window height, as a fraction of raw_win
MAX_LROC_WINDOWS    = 3                  # hard cap on windows fetched per product
# Each LROC window costs buffer_lines * samples * 2 bytes -- 77 MB at TMC scale,
# so three windows across three candidates can reach ~1.1 GB, which defeats the
# point of streaming. This is an absolute ceiling on network bytes for one
# registration: reaching it fails the run with a clear reason rather than
# quietly transferring a gigabyte.
RUN_BYTE_BUDGET     = 700 * 1024 * 1024
# Probing an in-memory buffer costs a SIFT pass, not a read, so the search is
# denser than the old per-read LROC_SCAN_STEP allowed. The step is derived from
# the buffer span so a small window still gets probed properly.
LROC_PROBE_COUNT    = 12                 # coarse probes across a loaded window
LROC_PROBE_STEP_MIN = 200                # lines; floor on the coarse step
LROC_FINE_STEP_MIN  = 100                # lines; floor on the refinement step
INFLATE_BUDGET      = 600 * 1024 * 1024  # max compressed bytes to stream-inflate
