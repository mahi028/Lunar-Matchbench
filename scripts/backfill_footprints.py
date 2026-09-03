"""Add footprint geometry to the baked demo runs.

The console draws its own footprint map from numbers in `provenance.footprint`.
Runs baked before that field existed do not carry it -- and `bake()` strips the
raw LROC candidate, so the geometry cannot be recovered from the record alone.

It can be recovered from NASA's ODE REST API, which is where it came from in the
first place. That is a metadata query measured in kilobytes: no imagery is
fetched, so this does not touch the transfer budget the pipeline exists to
respect. Re-baking the runs would mean streaming hundreds of megabytes to
recover four bounding boxes.

    uv run python scripts/backfill_footprints.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lunar_matchbench.core.downloader import discover_lroc_products  # noqa: E402
from lunar_matchbench.core.pipeline import _ch2_bbox, _lroc_bbox, CH2_PATCH_HALF_DEG  # noqa: E402
from lunar_matchbench.utils.geo import overlap_report  # noqa: E402

DEMO = ROOT / "demo"


def main() -> int:
    manifest = json.loads((DEMO / "manifest.json").read_text(encoding="utf-8"))["runs"]
    changed = 0

    for entry in manifest:
        slug = entry["slug"]
        path = DEMO / "runs" / f"{slug}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        prov = (record.get("result") or {}).get("provenance") or {}
        want = prov.get("lroc_product_id")
        if not want:
            print(f"{slug:<22} no lroc_product_id -- skipped")
            continue
        if "footprint" in prov:
            print(f"{slug:<22} already has footprint")
            continue

        candidates = discover_lroc_products(entry["lat"], entry["lon"])
        match = next((c for c in candidates if c.get("pds_id") == want), None)
        if match is None:
            print(f"{slug:<22} {want} not in {len(candidates)} ODE results -- skipped")
            continue

        prov["footprint"] = {
            **overlap_report(_ch2_bbox(entry["lat"], entry["lon"]), _lroc_bbox(match)),
            "target": {"lat": entry["lat"], "lon": entry["lon"]},
            "ch2_half_deg": CH2_PATCH_HALF_DEG,
            "lroc_filename": match["filename"],
        }
        record["result"]["provenance"] = prov
        path.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
        fp = prov["footprint"]
        print(f"{slug:<22} {want}  overlap {fp['overlap_area_km2']} km2 "
              f"({fp['ch2_overlap_pct']}% of patch), IoU {fp['iou']}")
        changed += 1

    print(f"\n{changed} record(s) updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
