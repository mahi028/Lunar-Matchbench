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
    assert "Target" in response.text


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


def test_patch_endpoint_404s_for_unknown_job():
    response = client.get("/api/patch/nope/ch2.png")
    assert response.status_code == 404


def test_patch_endpoint_rejects_unknown_kind():
    response = client.get("/api/patch/nope/sideways.png")
    assert response.status_code == 422


def test_result_model_accepts_tiepoints():
    from lunar_matchbench.api.models import RegistrationResult, TiePoints

    result = RegistrationResult(
        job_id="abc",
        status="done",
        tiepoints=TiePoints(
            moving=[[1.0, 2.0]], ref=[[3.0, 4.0]],
            inlier_mask=[True], residuals_px=[0.5],
        ),
        homography=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        patch_size=1024,
    )
    assert result.tiepoints.moving == [[1.0, 2.0]]
    assert result.patch_size == 1024
    assert result.tiepoints.residuals_px == [0.5]


def test_result_exposes_tiepoints_from_a_finished_job():
    """The browser draws matches itself, so the raw arrays must reach it."""
    from lunar_matchbench.api import app as app_mod

    job_id = "tptest01"
    app_mod._store(job_id, {
        "status": app_mod.JobStatus.done,
        "step_image_urls": {},
        "result": {
            "metrics": {
                "matcher": "SIFT", "n_inliers": 1, "n_raw_matches": 2,
                "inlier_ratio_pct": 50.0, "rmse_px": 1.5,
                "spatial_uniformity": 0.5, "elapsed_sec": 0.1,
            },
            "register_result": {
                "mkpts_moving": [[1.0, 2.0], [3.0, 4.0]],
                "mkpts_ref": [[5.0, 6.0], [7.0, 8.0]],
                "inlier_mask": [True, False],
                "residuals_px": [0.5, 9.0],
                "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            },
            "transfer": {"fetched_bytes": 100, "cached_bytes": 0,
                         "requests": 1, "product_bytes": 5000},
        },
    })
    data = client.get(f"/api/result/{job_id}").json()
    assert data["tiepoints"]["moving"] == [[1.0, 2.0], [3.0, 4.0]]
    assert data["tiepoints"]["inlier_mask"] == [True, False]
    assert data["tiepoints"]["residuals_px"] == [0.5, 9.0]
    assert data["transfer"]["product_bytes"] == 5000
    assert data["patch_size"] == 1024


def test_failed_job_still_exposes_tiepoints():
    """A failure is where the tie-point view matters most -- do not withhold it."""
    from lunar_matchbench.api import app as app_mod

    job_id = "tptest02"
    app_mod._store(job_id, {
        "status": app_mod.JobStatus.failed,
        "error": "did not converge",
        "step_image_urls": {},
        "result": {
            "register_result": {
                "mkpts_moving": [[1.0, 2.0]],
                "mkpts_ref": [[5.0, 6.0]],
                "inlier_mask": [False],
                "residuals_px": [42.0],
            },
        },
    })
    data = client.get(f"/api/result/{job_id}").json()
    assert data["status"] == "failed"
    assert data["tiepoints"]["residuals_px"] == [42.0]


def test_ui_serves_console_shell():
    """The console shell and its module entry point must both be reachable."""
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "Lunar-MatchBench" in body
    for element_id in ("run-form", "locator", "stage", "panels", "status-chip"):
        assert f'id="{element_id}"' in body, f"missing #{element_id}"
    assert 'type="module"' in body
    assert "/static/js/main.js" in body


def test_ui_static_assets_are_served():
    for path in ("/static/css/tokens.css", "/static/css/console.css"):
        assert client.get(path).status_code == 200, path
