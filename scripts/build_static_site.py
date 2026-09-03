"""Build the static public bundle for Hugging Face Spaces.

Docker Spaces need a PRO subscription; static Spaces are free. The four baked
preset runs need no server to replay -- they are JSON and PNGs -- so the console
can run entirely in the browser for anyone who just wants to see the pipeline.

The API responses in the bundle are NOT reimplemented in JavaScript. This script
drives the real FastAPI app through its real endpoints, in the same demo-only
mode the container uses, and writes down exactly what it returned. The static
site therefore serves the server's own answers; the only difference is that a
file replaces the request.

Live runs against arbitrary coordinates still require the container. The bundle
says so rather than pretending otherwise.

    uv run python scripts/build_static_site.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site"

# Must be set before the app is imported: they decide what the app believes it
# can do. Demo-only with no credentials is exactly the public container's mode.
os.environ["LMB_PROJECT_ROOT"] = str(ROOT)
os.environ["LMB_DEMO_ONLY"] = "1"
os.environ.pop("PRADAN_USERNAME", None)
os.environ.pop("PRADAN_PASSWORD", None)

sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from lunar_matchbench.api.app import app  # noqa: E402
from lunar_matchbench.core import demo  # noqa: E402

STATIC_SRC = ROOT / "src" / "lunar_matchbench" / "api" / "static"
TEMPLATE = ROOT / "src" / "lunar_matchbench" / "api" / "templates" / "index.html"


SPACE_README = """---
title: Lunar-MatchBench
emoji: 🌕
colorFrom: gray
colorTo: indigo
sdk: static
app_file: index.html
pinned: false
short_description: Chandrayaan-2 to LROC NAC image registration
---

# Lunar-MatchBench

Cross-mission optical image registration for **SIH 2026 - ISRO problem SIH26166**:
locate a Chandrayaan-2 TMC-2 image and a NASA LRO LROC NAC image of the same
lunar ground, and measure how precisely they line up.

Click a preset and press **Run registration**.

## What you are looking at

The four presets are **real runs** against the live ISSDC and LROC archives -
real metrics, real tie-points, real imagery, including two that genuinely fail
to converge, for different reasons. What is cached is the *fetching*, not the
answer, and every replayed run says so on screen.

| Preset | Outcome |
|---|---|
| Oceanus Procellarum | 523 inliers of 1,280, RMSE 1.532 px, 62.5% coverage |
| Rayed crater 5.2°N | 261 inliers of 856, RMSE 1.798 px, 50.0% coverage |
| Sinus Aestuum | Fails - all 15,360 lines searched, genuine mismatch |
| Known failure 3.6°N | Fails - only 4 inliers, localisation not confident |

The failures are on the page on purpose. A tool that only ever shows successes
tells you nothing about whether to trust the successes.

## Why this page has no server

Registering an arbitrary coordinate means streaming from ISRO's ISSDC archive,
which requires a personal account. Putting one operator's account behind a
public button is not something this project will do, so the public build is
static: the page plus the recorded runs.

The full pipeline - live coordinates, HTTP byte-range streaming from both
archives, and the draggable scan-line strip preview - runs from the container:

```
docker run -p 7860:7860 -e LMB_DEMO_ONLY=0 \
  -e PRADAN_USERNAME=<you> -e PRADAN_PASSWORD=<secret> lunar-matchbench
```

## How the pipeline works

1. **Locate** the Chandrayaan-2 patch for the coordinate from the product's
   geometry grid, reading only the bytes needed out of the remote ZIP.
2. **Find** LROC NAC products covering the same ground through the ODE REST API.
3. **Lock on** to the right scan line. Both instruments are pushbroom line
   scanners, so an LROC strip is tens of thousands of stacked single lines; the
   orbital-geometry estimate is refined by a coarse-to-fine SIFT search over
   windows streamed by HTTP byte-range.
4. **Match** with XFeat (CVPR 2024), then fit a homography with MAGSAC++.
5. **Report** inliers, reprojection RMSE, spatial uniformity, and the
   decomposed transform - and say plainly when it did not work.

A full live registration transfers roughly 80-250 MB instead of downloading
0.5-1.5 GB of products, under a hard per-run byte budget.

Built by team **Alibaba and 6 devs**, IIT Madras BS Programme.
"""


def emit(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    api_dir = OUT / "api"
    api_dir.mkdir(parents=True)

    client = TestClient(app)

    caps = client.get("/api/capabilities").json()
    assert caps["server_credentials"] is False, "refusing to bake a bundle with credentials"
    emit(api_dir / "capabilities.json", caps)
    print(f"capabilities.json    server_credentials=False, {len(caps['demo_runs'])} runs")

    runs = demo.available()
    emit(api_dir / "index.json", runs)

    for entry in runs:
        slug = entry["slug"]
        started = client.post("/api/register", json={
            "lat": entry["lat"], "lon": entry["lon"],
            "instrument": entry["instrument"], "matcher": entry["matcher"],
        })
        assert started.status_code == 202, f"{slug}: {started.status_code} {started.text[:120]}"
        job_id = started.json()["job_id"]

        result = client.get(f"/api/result/{job_id}").json()
        assert result["replayed"] is True, f"{slug} did not come back labelled as replayed"

        # The job id becomes the slug on the static site, and URLs the server
        # builds from job ids have to follow.
        result["job_id"] = slug
        if result.get("overlap_map_url"):
            result["overlap_map_url"] = f"./api/runs/{slug}/overlap.png"
        emit(api_dir / "runs" / slug / "result.json", result)

        record = demo.load(slug) or {}
        emit(api_dir / "runs" / slug / "steps.json", record.get("progress_steps") or [])

        copied = []
        for which in ("ch2", "lroc", "warped", "overlap"):
            src = demo.ASSET_DIR / f"{slug}_{which}.png"
            if src.exists():
                shutil.copy2(src, api_dir / "runs" / slug / f"{which}.png")
                copied.append(which)
        n_tie = len((result.get("tiepoints") or {}).get("moving") or [])
        print(f"{slug:<22} {result['status']:<7} {n_tie:>5} tie-points  "
              f"assets: {', '.join(copied)}")

    shutil.copytree(STATIC_SRC, OUT / "static")

    html = TEMPLATE.read_text(encoding="utf-8")
    # Root-absolute asset paths work behind FastAPI's StaticFiles mount; a
    # static host serves this file from a plain directory, so they have to be
    # relative to it.
    html = html.replace('href="/static/', 'href="static/').replace('src="/static/', 'src="static/')
    marker = '<script type="module"'
    assert marker in html
    html = html.replace(
        marker,
        '<script>window.LMB_STATIC_BASE = "./api";</script>\n  ' + marker,
        1,
    )
    (OUT / "index.html").write_text(html, encoding="utf-8", newline="\n")

    # Hugging Face reads this front matter to configure the Space. `sdk: static`
    # is the free tier; Docker Spaces need PRO, which is the whole reason this
    # bundle exists.
    (OUT / "README.md").write_text(SPACE_README, encoding="utf-8", newline=chr(10))

    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    n = sum(1 for p in OUT.rglob("*") if p.is_file())
    print(f"\nsite/  {n} files, {total / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
