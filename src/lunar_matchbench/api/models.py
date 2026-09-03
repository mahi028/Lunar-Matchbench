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
    # A visitor's own ISSDC account, used for this request only. Never written
    # to disk, never logged, and never persisted into the job record -- a public
    # deployment must not run every stranger's registration on one operator's
    # credentials, and must not accumulate anyone else's either.
    issdc_username: Optional[str] = Field(None, exclude=True, repr=False)
    issdc_password: Optional[str] = Field(None, exclude=True, repr=False)


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


class TiePoints(BaseModel):
    """Raw correspondences, so the browser can draw them itself.

    All four lists are the same length and index-aligned: entry i of `moving`
    corresponds to entry i of `ref`, was kept or rejected per `inlier_mask[i]`,
    and reprojects with `residuals_px[i]` error. Sent on failed runs too --
    a failure is where inspecting the matches matters most.
    """
    moving:       list[list[float]]
    ref:          list[list[float]]
    inlier_mask:  list[bool]
    residuals_px: list[float]


class TransferStats(BaseModel):
    """How many bytes the run actually moved, versus the product's full size."""
    fetched_bytes: int = 0
    cached_bytes:  int = 0
    requests:      int = 0
    product_bytes: int = 0


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
    # Interactive-view payload: the browser composites and draws these itself
    # rather than receiving a pre-rendered picture of the answer.
    # True when the run was replayed from the baked demo bundle rather than
      # fetched. Real data, cached fetching -- and always surfaced.
    replayed:         bool                    = False
    tiepoints:        Optional[TiePoints]     = None
    homography:       Optional[list[list[float]]] = None
    patch_size:       Optional[int]           = None
    transfer:         Optional[TransferStats] = None

