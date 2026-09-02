"""
Pydantic request/response models for the Lunar-MatchBench API.
"""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class Instrument(str, Enum):
    tmc  = "tmc"
    ohrc = "ohrc"


class Matcher(str, Enum):
    xfeat = "xfeat"
    sift  = "sift"


class RegisterRequest(BaseModel):
    lat:        float      = Field(..., ge=-90, le=90,  description="Target latitude (°N, planetocentric)")
    lon:        float      = Field(..., ge=0,   le=360, description="Target longitude (°E, 0-360)")
    instrument: Instrument = Field(Instrument.tmc, description="CH2 source instrument")
    matcher:    Matcher    = Field(Matcher.xfeat,  description="Feature matcher")


class JobStatus(str, Enum):
    queued     = "queued"
    running    = "running"
    done       = "done"
    failed     = "failed"


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus


class MetricsResult(BaseModel):
    matcher:             str
    n_inliers:           int
    n_raw_matches:       int
    inlier_ratio_pct:    float
    rmse_px:             float
    spatial_uniformity:  float
    elapsed_sec:         float


class RegistrationResult(BaseModel):
    job_id:           str
    status:           JobStatus
    metrics:          Optional[MetricsResult] = None
    # Keyed by pipeline stage: extracted, keypoints, matches, inliers, final.
    # Populated incrementally as each stage completes, even on a failed run,
    # so a failure can be inspected at the exact step it broke down.
    step_image_urls:  dict[str, str]          = {}
    overlap_map_url:  Optional[str]           = None
    provenance:       Optional[dict]          = None
    error:            Optional[str]           = None

