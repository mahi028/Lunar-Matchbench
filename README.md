---
title: Lunar-MatchBench
emoji: 🌕
colorFrom: gray
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
short_description: Chandrayaan-2 to LROC NAC image registration
---

# Lunar-MatchBench 🌕

> **SIH 2026 Space Technology | ISRO Problem ID: SIH26166**  
> Cross-Mission Lunar Optical Image Registration & Benchmark Engine  
> **ISRO Chandrayaan-2 (TMC-2 & OHRC) ↔ NASA LRO (LROC NAC Reference)**

---

## 🚀 Key Features

- **Byte-Range Streaming**: A run fetches only the bytes it needs instead of
  downloading whole products. Measured against the live archives:

  | Product | Full size | Transferred |
  |---|---|---|
  | LROC NAC `.IMG` | 529 MB | 38.7 MB (TMC-2 window) / 1.9 MB (OHRC) |
  | CH2 TMC-2 `.zip` | 508 MB | 0.79 MB geometry CSV + a line window |

  Both `pds.lroc.im-ldi.com` and `pradan.issdc.gov.in` honour single byte-ranges.
  Multi-range requests are **not** supported by the PDS host — it ignores them and
  returns the entire body — so the reader issues one contiguous interval per
  request. Every response's `Content-Range` is validated against what was asked
  for; see `tests/test_live.py`, which fails loudly if either fact changes.
- **End-to-End Autonomous Pipeline**: Enter lunar coordinates (`lat`, `lon`), and the engine automatically:
  1. Searches ISRO pushbroom geometry CSVs to locate the exact scan line & pixel.
  2. Queries NASA ODE REST API to discover overlapping calibrated LROC NAC images (`CDRNAC4`).
  3. Streams just the needed window of the PDS product, caching each range on disk.
  4. Runs a coarse-to-fine descriptor scan to resolve ephemeris/cross-track offsets.
  5. Performs sub-pixel cross-mission registration using **XFeat (CVPR 2024)** + MAGSAC++ homography.
  6. Generates 6-panel verification posters, geographic footprint overlap maps, and SHA-256 sensor provenance.
- **Mission-control console**: A dark FastAPI web console where the result is
  explorable rather than asserted:
  - a **scan-line strip locator** drawing the whole LROC strip to scale, with the
    pushbroom geometry estimate, the span actually searched, and the achieved
    lock on it — so a 2,105-line drift, or a strip searched end to end with no
    match, is visible at a glance;
  - a **swipe/fade comparator** between the registered CH2 patch and the LROC
    reference, keyboard-operable;
  - a **tie-point overlay** drawn from the pipeline's own correspondence arrays
    over the CH2 patch — hover any point for its reprojection error, filter to
    kept or rejected, toggle the flow field;
  - **evidence charts** computed from real output: a residual histogram against
    the RANSAC threshold, the verification proportion, and the 8×8 spatial
    coverage grid.

  Colour encodes mission provenance throughout — saffron is always
  Chandrayaan-2, blue is always LROC NAC. The kept/rejected verdict is carried
  by shape as well as colour, because that pair sits at deutan ΔE 8.5.
- **Reproducible Environment**: Fully managed with `uv` and standard `pyproject.toml`.

---

## 📁 Repository Structure

```
Lunar-Matchbench/
├── pyproject.toml                 # Package dependencies and project config (uv managed)
├── src_prot/                      # Archived prototype development scripts
├── src/
│   └── lunar_matchbench/          # Clean modular package
│       ├── __init__.py            # Package root & public exports
│       ├── __main__.py            # Entry point for `python -m lunar_matchbench`
│       ├── cli.py                 # CLI commands: register and serve
│       ├── config.py              # Centralised paths, instruments, constants
│       ├── core/
│       │   ├── downloader.py      # ISRO & NASA ODE API downloaders + PDS3 decoder
│       │   ├── register.py        # XFeat + SIFT matchers, MAGSAC++, metrics
│       │   └── pipeline.py        # 6-step end-to-end registration orchestrator
│       ├── api/
│       │   ├── app.py             # FastAPI backend with async job runner
│       │   ├── models.py          # Pydantic request/response schemas
│       │   ├── templates/
│       │   │   └── index.html     # Mission-control console markup
│       │   └── static/
│       │       ├── css/
│       │       │   ├── tokens.css     # Palette, type scale, focus, reduced motion
│       │       │   ├── console.css    # Status bar, rail, panels, responsive
│       │       │   └── panels.css     # Comparator, overlay, charts, diagnosis
│       │       └── js/
│       │           ├── api.js         # Every network call the console makes
│       │           ├── locator.js     # Scan-line strip locator
│       │           ├── comparator.js  # Swipe / fade between the two patches
│       │           ├── tiepoints.js   # Canvas correspondence overlay
│       │           ├── charts.js      # Residuals, verification, coverage
│       │           └── main.js        # Run lifecycle and panel wiring
│       ├── core/
│       │   └── streaming.py       # Byte-range reader, PDS3 + remote-ZIP readers
│       └── utils/
│           ├── geo.py             # Spherical Moon geometry, BBox, IoU
│           └── image.py           # CLAHE, percentiles, checkerboard, anaglyph
├── tests/
│   ├── test_core.py               # Unit tests for image processing & geo utils
│   ├── test_api.py                # FastAPI endpoint integration tests
│   ├── test_streaming.py          # Byte-range, remote ZIP, resume integrity
│   ├── test_ui.py                 # Console checks (headless browser, offline)
│   └── test_live.py               # Opt-in checks against the live archives
├── outputs/
│   ├── posters/                   # Generated 6-panel verification posters
│   └── overlap/                   # Geographic footprint overlap maps
└── data_store/                    # Cached ISRO and NASA science data
```

---

## ⚡ Quick Start with `uv`

### 1. Launch the Web UI

```powershell
uv run lunar-matchbench serve --host 127.0.0.1 --port 8000
```
Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in your browser.

### 2. Run Registration from CLI

```powershell
# Register Chandrayaan-2 TMC-2 onto NASA LROC NAC at 15.0°N, 289.2°E:
uv run lunar-matchbench register --lat 15.0 --lon 289.2 --instrument tmc --matcher xfeat

# Register Chandrayaan-2 OHRC:
uv run lunar-matchbench register --lat 15.0 --lon 289.2 --instrument ohrc --matcher xfeat
```

### 3. Pre-warm the cache before a demo

```powershell
uv run lunar-matchbench warm --instrument tmc
```

Pre-fetches the UI's preset coordinates into the byte-range cache so those runs
return in seconds. A coordinate typed live still runs genuinely against the
archives, and the UI always reports whether a fetch was served from cache or
from the network.

### 4. Run Automated Tests

```powershell
uv run pytest              # offline suite, no network
uv run pytest -m network   # opt-in live checks against ISSDC and NASA
```

The offline suite is hermetic. Live tests are deselected by default so the
suite stays fast and works without credentials.

### Credentials

Streaming CH2 data needs an ISSDC/PRADAN account. Copy `.env.example` to `.env`
and fill it in; `.env` is gitignored.

```
PRADAN_USERNAME=your_issdc_username
PRADAN_PASSWORD=your_issdc_password
```

---

## 📊 Benchmark Results (Real Cross-Mission Data)

- **Test Target**: Oceanus Procellarum (`15.000°N, 289.200°E`)
- **ISRO Sensor**: Chandrayaan-2 TMC-2 (Fore View, ~5 m/GSD)
- **NASA Reference**: LRO LROC NAC (`M1359306139LC`, ~0.8 m/GSD)
- **Time Separation**: 324 days (Dec 18, 2019 vs Nov 6, 2020)
- **Inlier Tie-Points**: **872 inliers** / 1,831 raw matches (**47.62% inlier ratio**)
- **Reprojection RMSE**: **1.503 px**
- **Spatial Uniformity**: **75.0%**
- **Execution Time**: **~3.25 s**

---

## 🛠 Adding New Dependencies

```powershell
uv add <package-name>
```
