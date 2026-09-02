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


def test_post_registration_job():
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
