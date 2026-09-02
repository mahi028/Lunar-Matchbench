# Lunar-MatchBench 🌕

> **SIH 2026 Space Technology | ISRO Problem ID: SIH26166**  
> Cross-Mission Lunar Optical Image Registration & Benchmark Engine  
> **ISRO Chandrayaan-2 (TMC-2 & OHRC) ↔ NASA LRO (LROC NAC Reference)**

---

## 🚀 Key Features

- **End-to-End Autonomous Pipeline**: Enter lunar coordinates (`lat`, `lon`), and the engine automatically:
  1. Searches ISRO pushbroom geometry CSVs to locate the exact scan line & pixel.
  2. Queries NASA ODE REST API to discover overlapping calibrated LROC NAC images (`CDRNAC4`).
  3. Downloads and caches the PDS product.
  4. Runs a coarse-to-fine descriptor scan to resolve ephemeris/cross-track offsets.
  5. Performs sub-pixel cross-mission registration using **XFeat (CVPR 2024)** + MAGSAC++ homography.
  6. Generates 6-panel verification posters, geographic footprint overlap maps, and SHA-256 sensor provenance.
- **FastAPI Web UI**: Interactive dashboard with real-time pipeline step tracker, quick presets, metrics bar, and interactive comparison tabs.
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
│       │   │   └── index.html     # Interactive Web UI
│       │   └── static/
│       │       ├── app.js         # Frontend controller and polling
│       │       └── style.css      # Dark-themed dashboard UI styling
│       └── utils/
│           ├── geo.py             # Spherical Moon geometry, BBox, IoU
│           └── image.py           # CLAHE, percentiles, checkerboard, anaglyph
├── tests/
│   ├── test_core.py               # Unit tests for image processing & geo utils
│   └── test_api.py                # FastAPI endpoint integration tests
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

### 3. Run Automated Tests

```powershell
uv run pytest
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
