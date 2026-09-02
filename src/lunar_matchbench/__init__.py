"""
Lunar-MatchBench: Cross-Mission Optical Registration Benchmark
(ISRO Chandrayaan-2 TMC-2/OHRC ↔ NASA LRO LROC NAC)
"""
__version__ = "1.0.0"

from lunar_matchbench.core.register import register
from lunar_matchbench.core.pipeline import run_pipeline
from lunar_matchbench.api.app import app

__all__ = ["register", "run_pipeline", "app"]
