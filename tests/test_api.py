"""
Tests for Lunar-MatchBench FastAPI Application
==============================================
"""
import pytest
from fastapi.testclient import TestClient
from lunar_matchbench.api.app import app


client = TestClient(app)


def test_serve_ui():
    response = client.get("/")
    assert response.status_code == 200
    assert "Lunar-MatchBench" in response.text
    assert "Registration Parameters" in response.text


def test_status_not_found():
    response = client.get("/api/status/nonexistent_job_123")
    assert response.status_code == 404


def test_post_registration_job(monkeypatch):
    """Enqueueing a job must not perform a real ISSDC/NASA fetch.

    FastAPI's TestClient runs BackgroundTasks inline once the response is
    returned, so without this stub the test logs into ISSDC for real and starts
    downloading a multi-hundred-megabyte product -- which is exactly what
    happened once a working .env appeared. The endpoint contract is what is
    under test here; the pipeline itself is covered elsewhere.
    """
    import lunar_matchbench.core.pipeline as pipeline_mod

    def _stub(*args, **kwargs):
        return {"status": "FAILED", "reason": "stubbed out in tests", "step_images": {}}

    monkeypatch.setattr(pipeline_mod, "run_pipeline", _stub)

    payload = {
        "lat": 15.0,
        "lon": 289.2,
        "instrument": "tmc",
        "matcher": "xfeat"
    }
    response = client.post("/api/register", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert "job_id" in data
    assert data["status"] in ["queued", "running"]
