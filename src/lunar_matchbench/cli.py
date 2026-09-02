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
