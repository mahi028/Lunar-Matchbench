"""
Lunar-MatchBench FastAPI Application
======================================

Endpoints
---------
GET  /                          → serves the web UI
POST /api/register              → enqueues a registration job → {job_id}
GET  /api/status/{job_id}       → polling endpoint → {status, step_image_urls, ...}
GET  /api/result/{job_id}       → full result once done
GET  /images/{filename}         → serves poster / overlap map PNGs
"""
from __future__ import annotations

import ctypes
import gc
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from lunar_matchbench.api.models import (
    RegisterRequest, JobResponse, JobStatus, RegistrationResult, MetricsResult,
)
from lunar_matchbench.config import POSTER_DIR, OVERLAP_DIR, ensure_dirs

ensure_dirs()

PROGRESS_TOTAL = 8

app = FastAPI(
    title="Lunar-MatchBench",
    description="Cross-mission lunar image registration (ISRO CH2 ↔ NASA LROC NAC)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ── In-memory job store (simple prototype) ─────────────────────────────────
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _store(job_id: str, data: dict) -> None:
    with _lock:
        _jobs[job_id] = {**_jobs.get(job_id, {}), **data}


def _read(job_id: str) -> dict | None:
    with _lock:
        return _jobs.get(job_id)


def _poster_url(path: str) -> str:
    return f"/images/posters/{Path(path).name}"


def _release_memory() -> None:
    """
    Force Python and glibc to actually hand freed memory back to the OS.

    A single registration allocates and frees hundreds of MB (raw science
    rasters read during the coarse-to-fine LROC scan, matplotlib figures,
    torch's CPU working buffers) -- and glibc's allocator does NOT return
    freed heap pages to the OS on its own once it has claimed them, even
    with the arena count capped. On an unconstrained host that's invisible;
    on a memory-limited container it means the process's resident size only
    ever goes up, and a second registration that would fit fine on its own
    gets OOM-killed on top of the first one's leftover high-water mark.
    `gc.collect()` clears any Python-level reference cycles (figures/arrays
    can have them) first, since glibc can't reclaim memory Python is still
    holding onto; `malloc_trim(0)` then asks glibc itself to release
    whatever's left. Only glibc exposes malloc_trim, so this is a no-op
    (silently) anywhere else -- fine, since the leak this targets is a
    Linux-container deployment concern, not a local-dev one.
    """
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


def _run_pipeline(job_id: str, req: RegisterRequest) -> None:
    """Background thread target."""
    _store(job_id, {"status": JobStatus.running})
    try:
        from lunar_matchbench.core.pipeline import run_pipeline

        def _cb(step, total, msg, step_images=None):
            data = {"progress_msg": msg, "progress_step": step}
            if step_images:
                data["step_image_urls"] = {k: _poster_url(v) for k, v in step_images.items()}
            _store(job_id, data)

        result = run_pipeline(
            lat=req.lat, lon=req.lon,
            instrument=req.instrument.value,
            matcher=req.matcher.value,
            job_id=job_id,
            progress_cb=_cb,
        )
        if result["status"] == "SUCCESS":
            _store(job_id, {
                "status": JobStatus.done,
                "result": result,
                "step_image_urls": {k: _poster_url(v) for k, v in result.get("step_images", {}).items()},
            })
        else:
            _store(job_id, {
                "status": JobStatus.failed,
                "error":  result.get("reason", "Registration failed."),
                "result": result,
                "step_image_urls": {k: _poster_url(v) for k, v in result.get("step_images", {}).items()},
            })
    except Exception as exc:
        _store(job_id, {"status": JobStatus.failed, "error": str(exc)})
    finally:
        _release_memory()


# ── Routes ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    template = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(template.read_text(encoding="utf-8"))


@app.post("/api/register", response_model=JobResponse, status_code=202)
async def start_registration(req: RegisterRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())[:8]
    _store(job_id, {"status": JobStatus.queued, "request": req.model_dump()})
    background_tasks.add_task(_run_pipeline, job_id, req)
    return JobResponse(job_id=job_id, status=JobStatus.queued)


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    job = _read(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id":          job_id,
        "status":          job["status"],
        "progress_msg":    job.get("progress_msg", ""),
        "progress_step":   job.get("progress_step", 0),
        "progress_total":  PROGRESS_TOTAL,
        "step_image_urls": job.get("step_image_urls", {}),
    }


@app.get("/api/result/{job_id}", response_model=RegistrationResult)
async def get_result(job_id: str):
    job = _read(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] not in (JobStatus.done, JobStatus.failed):
        raise HTTPException(status_code=202, detail="Job not yet complete")

    r = job.get("result", {})
    step_image_urls = job.get("step_image_urls", {})
    overlap_fname = Path(r["overlap_map_path"]).name if "overlap_map_path" in r else None
    overlap_url = f"/images/overlap/{overlap_fname}" if overlap_fname else None

    if job["status"] == JobStatus.failed:
        return RegistrationResult(
            job_id          = job_id,
            status          = JobStatus.failed,
            error           = job.get("error"),
            step_image_urls = step_image_urls,
            overlap_map_url = overlap_url,
            provenance      = r.get("provenance"),
        )

    return RegistrationResult(
        job_id          = job_id,
        status          = JobStatus.done,
        metrics         = MetricsResult(**r["metrics"]) if "metrics" in r else None,
        step_image_urls = step_image_urls,
        overlap_map_url = overlap_url,
        provenance      = r.get("provenance"),
    )


@app.get("/images/posters/{filename}")
async def serve_poster(filename: str):
    path = POSTER_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(path), media_type="image/png")


@app.get("/images/overlap/{filename}")
async def serve_overlap_map(filename: str):
    path = OVERLAP_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(path), media_type="image/png")
