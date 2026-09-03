"""
Lunar-MatchBench: cached demo runs
===================================
A public deployment cannot fetch from ISSDC on a visitor's behalf -- that would
run every stranger's registration against one personal PRADAN account. But a
demo that shows nothing is worthless, so the preset coordinates are *baked*:
a real run is executed once, and its genuine result, provenance and imagery are
committed so the console can replay them.

Replayed runs are real data. They are not fabricated and not a mock -- the
metrics, tie-points and patches are exactly what the pipeline produced against
the live archives on the bake date. What is cached is the fetching, not the
answer. The UI says so on every replayed run, because a judge who later learns a
number was cached and was not told will distrust every other number.

A visitor who supplies their own ISSDC credentials bypasses all of this and runs
live against any coordinate.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from lunar_matchbench.config import PROJECT_ROOT

DEMO_DIR = PROJECT_ROOT / "demo"
MANIFEST = DEMO_DIR / "manifest.json"
ASSET_DIR = DEMO_DIR / "assets"

# A preset matches if the request lands within this many degrees of it. Presets
# are entered by clicking a button, so this only has to absorb float formatting.
MATCH_TOLERANCE_DEG = 1e-4


def _load_manifest() -> list[dict]:
    if not MANIFEST.exists():
        return []
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8")).get("runs", [])
    except (OSError, ValueError):
        return []


def available() -> list[dict]:
    """The baked runs a deployment can replay, without their payloads."""
    return [
        {k: entry[k] for k in ("slug", "label", "lat", "lon", "instrument", "matcher", "status")}
        for entry in _load_manifest()
        if (DEMO_DIR / "runs" / f"{entry['slug']}.json").exists()
    ]


def find(lat: float, lon: float, instrument: str, matcher: str) -> dict | None:
    """Return the baked entry matching this request, if there is one."""
    for entry in _load_manifest():
        if (
            abs(entry["lat"] - lat) <= MATCH_TOLERANCE_DEG
            and abs(entry["lon"] - lon) <= MATCH_TOLERANCE_DEG
            and entry["instrument"] == instrument
            and entry["matcher"] == matcher
        ):
            if (DEMO_DIR / "runs" / f"{entry['slug']}.json").exists():
                return entry
    return None


def load(slug: str) -> dict[str, Any] | None:
    """Load a baked run, repointing its asset paths at this checkout."""
    path = DEMO_DIR / "runs" / f"{slug}.json"
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    result = record.get("result") or {}
    # Paths were absolute on the machine that baked them.
    patches = {}
    for which in ("ch2", "lroc", "warped"):
        asset = ASSET_DIR / f"{slug}_{which}.png"
        if asset.exists():
            patches[which] = str(asset)
    if patches:
        result["raw_patches"] = patches

    overlap = ASSET_DIR / f"{slug}_overlap.png"
    if overlap.exists():
        result["overlap_map_path"] = str(overlap)
    else:
        result.pop("overlap_map_path", None)

    record["result"] = result
    # Marks every replayed run so the UI can label it. Never removed, never
    # defaulted to False elsewhere: a cached run must not be able to present
    # itself as a live fetch.
    record["replayed"] = True
    record["replay_baked_at"] = record.get("baked_at", "")
    return record


def bake(job_record: dict, entry: dict, poster_dir: Path, overlap_path: str | None) -> None:
    """Write a completed run into the demo bundle."""
    slug = entry["slug"]
    (DEMO_DIR / "runs").mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)

    result = dict(job_record.get("result") or {})
    for which, src in (result.get("raw_patches") or {}).items():
        src_path = Path(src)
        if src_path.exists():
            shutil.copy2(src_path, ASSET_DIR / f"{slug}_{which}.png")
    if overlap_path and Path(overlap_path).exists():
        shutil.copy2(overlap_path, ASSET_DIR / f"{slug}_overlap.png")

    # Absolute machine paths and the raw candidate are stripped: the first are
    # meaningless elsewhere, and the second would let a replay try to stream.
    result.pop("raw_patches", None)
    result.pop("overlap_map_path", None)
    result.pop("lroc_candidate", None)
    result.pop("step_images", None)

    record = {
        "status": job_record.get("status"),
        "error": job_record.get("error"),
        "progress_steps": job_record.get("progress_steps", []),
        "result": result,
        "baked_at": entry.get("baked_at", ""),
    }
    (DEMO_DIR / "runs" / f"{slug}.json").write_text(
        json.dumps(record, indent=1, default=str), encoding="utf-8")
