"""
Lunar-MatchBench CLI Runner
=============================
Run end-to-end registration or start the web server from the terminal.

Usage:
  # Run registration directly
  python -m lunar_matchbench.cli register --lat 15.0 --lon 289.2 --instrument tmc

  # Start the web UI server
  python -m lunar_matchbench.cli serve --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path


# Kept in step with the quick-preset buttons in index.html; a test asserts they
# match. Warming coordinates the demo does not use is worse than not warming at
# all -- it looks prepared and still stalls on the one actually clicked.
WARM_PRESETS = [
    (15.0, 289.2),
    (10.2, 289.5),
    (5.17879877, 288.954173),
    (3.613415864967716, 289.12239203822105),
]

# The labels the console shows, paired with their slugs in the demo bundle.
PRESET_LABELS = {
    (15.0, 289.2): ("oceanus-procellarum", "Oceanus Procellarum"),
    (10.2, 289.5): ("sinus-aestuum", "Sinus Aestuum"),
    (5.17879877, 288.954173): ("rayed-crater-5n", "Rayed crater 5.2\u00b0N"),
    (3.613415864967716, 289.12239203822105): ("known-failure-3n", "Known failure 3.6\u00b0N"),
}


def main():
    parser = argparse.ArgumentParser(
        prog="lunar-matchbench",
        description="Lunar-MatchBench: Chandrayaan-2 to NASA LROC NAC Cross-Mission Registration",
    )
    subparsers = parser.add_subparsers(dest="command", help="Sub-commands")

    # Sub-command: register
    p_reg = subparsers.add_parser("register", help="Run cross-mission image registration")
    p_reg.add_argument("--lat", type=float, default=15.0, help="Target latitude (degrees N, default: 15.0)")
    p_reg.add_argument("--lon", type=float, default=289.2, help="Target longitude (degrees E, default: 289.2)")
    p_reg.add_argument("--instrument", choices=["tmc", "ohrc"], default="tmc", help="CH2 source instrument (default: tmc)")
    p_reg.add_argument("--matcher", choices=["xfeat", "sift"], default="xfeat", help="Feature matcher (default: xfeat)")

    # Sub-command: bake-demo
    p_bake = subparsers.add_parser(
        "bake-demo",
        help="Run every preset and record it so a credential-less deployment can replay it")
    p_bake.add_argument("--instrument", choices=["tmc", "ohrc"], default="tmc")
    p_bake.add_argument("--matcher", choices=["xfeat", "sift"], default="xfeat")

    # Sub-command: warm
    p_warm = subparsers.add_parser("warm", help="Pre-fetch preset coordinates into the range cache")
    p_warm.add_argument("--instrument", choices=["tmc", "ohrc"], default="tmc",
                        help="CH2 source instrument (default: tmc)")

    # Sub-command: serve
    p_srv = subparsers.add_parser("serve", help="Launch FastAPI web UI server")
    p_srv.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    p_srv.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    p_srv.add_argument("--reload", action="store_true", help="Enable auto-reload")

    # Default to register if no subcommand specified but args given
    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn
        print(f"\n=======================================================")
        print(f"  Starting Lunar-MatchBench Web UI at http://{args.host}:{args.port}")
        print(f"=======================================================\n")
        uvicorn.run("lunar_matchbench.api.app:app", host=args.host, port=args.port, reload=args.reload)

    elif args.command == "bake-demo":
        import datetime
        import json

        from lunar_matchbench.core import demo
        from lunar_matchbench.core.pipeline import run_pipeline

        print("")
        print(f"Baking {len(WARM_PRESETS)} preset runs for offline replay")
        print("Each is a real run against the live archives; only the fetching is cached.")
        print("")

        entries = []
        for lat, lon in WARM_PRESETS:
            slug, label = PRESET_LABELS[(lat, lon)]
            print(f"  {label:<26} {lat:>10.5f} N {lon:>11.5f} E ... ", end="", flush=True)

            recorded = []

            def _cb(step, total, msg, step_images=None, transfer=None, _rec=recorded):
                _rec.append({"step": step, "msg": msg, "transfer": dict(transfer or {})})

            try:
                res = run_pipeline(lat=lat, lon=lon, instrument=args.instrument,
                                   matcher=args.matcher, job_id=slug, progress_cb=_cb)
            except Exception as exc:
                print(f"SKIPPED ({type(exc).__name__}: {exc})")
                continue

            status = "done" if res.get("status") == "SUCCESS" else "failed"
            entry = {
                "slug": slug, "label": label,
                "lat": lat, "lon": lon,
                "instrument": args.instrument, "matcher": args.matcher,
                "status": status,
                "baked_at": datetime.datetime.now(datetime.timezone.utc)
                                    .isoformat(timespec="seconds"),
            }
            demo.bake(
                {"status": status, "error": res.get("reason"),
                 "progress_steps": recorded, "result": res},
                entry,
                poster_dir=None,
                overlap_path=res.get("overlap_map_path"),
            )
            entries.append(entry)

            metrics = res.get("metrics") or {}
            if status == "done":
                print(f"SUCCESS  {metrics.get('n_inliers')} inliers, "
                      f"RMSE {metrics.get('rmse_px')} px")
            else:
                print(f"FAILED   {(res.get('reason') or '')[:58]}")

        demo.DEMO_DIR.mkdir(parents=True, exist_ok=True)
        demo.MANIFEST.write_text(json.dumps({"runs": entries}, indent=1), encoding="utf-8")
        print("")
        print(f"Wrote {len(entries)} runs to {demo.DEMO_DIR}")
        print("A deployment with no ISSDC account will replay these for the presets.")
        print("")

    elif args.command == "warm":
        from lunar_matchbench.core.pipeline import run_pipeline

        print("")
        print(f"Warming {len(WARM_PRESETS)} preset coordinates ({args.instrument.upper()})...")
        print("")
        for lat, lon in WARM_PRESETS:
            print(f"  {lat:>10.5f} N, {lon:>11.5f} E ... ", end="", flush=True)
            try:
                res = run_pipeline(lat=lat, lon=lon, instrument=args.instrument,
                                   matcher="xfeat")
                t = res.get("transfer", {})
                print(f"{res['status']}  "
                      f"(fetched {t.get('fetched_bytes', 0) / 1e6:.1f} MB, "
                      f"cached {t.get('cached_bytes', 0) / 1e6:.1f} MB)")
            except Exception as exc:
                print(f"SKIPPED ({type(exc).__name__}: {exc})")
        print("")
        print("Cache warm. Preset runs will now serve from disk.")
        print("")

    elif args.command == "register" or (args.command is None and len(sys.argv) > 1):
        # Default run registration
        lat = getattr(args, "lat", 15.0)
        lon = getattr(args, "lon", 289.2)
        inst = getattr(args, "instrument", "tmc")
        matcher = getattr(args, "matcher", "xfeat")

        from lunar_matchbench.core.pipeline import run_pipeline

        print(f"\n=======================================================")
        print(f"  Lunar-MatchBench Registration Pipeline")
        print(f"  Target: {lat:.4f} N, {lon:.4f} E | Instrument: {inst.upper()} | Matcher: {matcher.upper()}")
        print(f"=======================================================\n")

        def _print_cb(step, total, msg, step_images=None, transfer=None):
            print(f"[{step}/{total}] {msg}")

        result = run_pipeline(lat=lat, lon=lon, instrument=inst, matcher=matcher, progress_cb=_print_cb)

        if result["status"] == "SUCCESS":
            m = result["metrics"]
            print(f"\n Registration SUCCESSFUL!")
            print(f"   Inlier Tie-Points:    {m['n_inliers']} / {m['n_raw_matches']} ({m['inlier_ratio_pct']}%)")
            print(f"   Reprojection RMSE:    {m['rmse_px']:.4f} px")
            print(f"   Spatial Uniformity:   {m['spatial_uniformity']:.4f}")
            print(f"   Execution Time:       {m['elapsed_sec']:.2f} s")
            print(f"\n Artifacts:")
            for step_name, path in result.get("step_images", {}).items():
                print(f"   {step_name:<10} {path}")
            print(f"   overlap    {result['overlap_map_path']}")
            print()
        else:
            print(f"\n Registration FAILED: {result.get('reason')}\n")
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
