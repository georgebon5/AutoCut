"""Tests for the job-runner endpoints and the pluggable pipeline function."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Sequence

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from autocut_api.config import ApiConfig
from autocut_api.jobs import run_stub_pipeline, update_job
from autocut_api.main import create_app


SMALL_CHUNK = 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> ApiConfig:
    return ApiConfig(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        upload_chunk_size=SMALL_CHUNK,
    )


def _completed_upload(client: TestClient, filename: str = "clip.mp4") -> str:
    payload = os.urandom(SMALL_CHUNK + 100)
    r = client.post("/uploads", json={"filename": filename, "total_size": len(payload)})
    uid = r.json()["id"]
    client.put(f"/uploads/{uid}/chunks/0", content=payload[:SMALL_CHUNK])
    client.put(f"/uploads/{uid}/chunks/1", content=payload[SMALL_CHUNK:])
    client.post(f"/uploads/{uid}/complete")
    return uid


def _wait_until(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = client.get(f"/jobs/{job_id}")
        body = r.json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.02)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


@pytest.fixture
def default_client(tmp_path: Path):
    # Explicit stub pipeline so the test does not try to run the real
    # autocut pipeline (which needs real video files).
    cfg = _cfg(tmp_path)
    app = create_app(cfg, pipeline_fn=run_stub_pipeline, max_workers=1)
    with TestClient(app) as client:
        yield client, cfg


# ---------------------------------------------------------------------------
# Create + run (stub pipeline)
# ---------------------------------------------------------------------------

def test_create_job_returns_pending_or_running(default_client):
    client, _ = default_client
    uid = _completed_upload(client)
    r = client.post("/jobs", json={"upload_ids": [uid]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"]
    assert body["status"] in ("pending", "running", "done")
    assert body["workspace"] is not None


def test_stub_pipeline_reaches_done(default_client):
    client, _ = default_client
    uid = _completed_upload(client)
    created = client.post("/jobs", json={"upload_ids": [uid]}).json()
    final = _wait_until(client, created["id"])
    assert final["status"] == "done"
    assert final["stage"] == "complete"
    assert final["progress"] == pytest.approx(1.0)
    assert final["error"] is None


def test_job_workspace_dir_created(default_client):
    client, cfg = default_client
    uid = _completed_upload(client)
    created = client.post("/jobs", json={"upload_ids": [uid]}).json()
    _wait_until(client, created["id"])
    assert (cfg.jobs_dir / created["id"]).is_dir()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_create_rejects_empty_upload_ids(default_client):
    client, _ = default_client
    r = client.post("/jobs", json={"upload_ids": []})
    assert r.status_code == 422


def test_create_rejects_unknown_upload(default_client):
    client, _ = default_client
    r = client.post("/jobs", json={"upload_ids": ["nope"]})
    assert r.status_code == 400
    assert "unknown upload" in r.json()["detail"]


def test_create_rejects_incomplete_upload(default_client):
    client, _ = default_client
    r = client.post("/uploads", json={"filename": "x.mp4", "total_size": SMALL_CHUNK})
    uid = r.json()["id"]
    # No chunks + no complete call → upload stuck at "pending".
    r = client.post("/jobs", json={"upload_ids": [uid]})
    assert r.status_code == 400
    assert "not complete" in r.json()["detail"]


# ---------------------------------------------------------------------------
# List + get
# ---------------------------------------------------------------------------

def test_list_jobs_returns_recent_first(default_client):
    client, _ = default_client
    u1 = _completed_upload(client, "a.mp4")
    u2 = _completed_upload(client, "b.mp4")
    j1 = client.post("/jobs", json={"upload_ids": [u1]}).json()["id"]
    j2 = client.post("/jobs", json={"upload_ids": [u2]}).json()["id"]
    _wait_until(client, j1)
    _wait_until(client, j2)
    r = client.get("/jobs")
    ids = [j["id"] for j in r.json()]
    assert j2 == ids[0] or j1 == ids[0]
    assert {j1, j2}.issubset(set(ids))


def test_get_unknown_job_returns_404(default_client):
    client, _ = default_client
    r = client.get("/jobs/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Pluggable pipeline function
# ---------------------------------------------------------------------------

def test_custom_pipeline_fn_runs_and_records_failure(tmp_path: Path):
    """A pipeline fn that raises should mark the job failed with its error."""
    def boom(job_id: str, workspace: Path, clips: Sequence[Path],
             session_factory: sessionmaker) -> None:
        try:
            raise RuntimeError("transcription OOM")
        except Exception as e:
            update_job(session_factory, job_id,
                       status="failed", stage="error", error=str(e))

    cfg = _cfg(tmp_path)
    app = create_app(cfg, pipeline_fn=boom, max_workers=1)
    with TestClient(app) as client:
        uid = _completed_upload(client)
        job_id = client.post("/jobs", json={"upload_ids": [uid]}).json()["id"]
        final = _wait_until(client, job_id)
        assert final["status"] == "failed"
        assert final["error"] == "transcription OOM"


def test_custom_pipeline_fn_receives_clip_paths(tmp_path: Path):
    """Pipeline fn should be called with the uploaded files' final paths."""
    captured: dict = {}

    def capture(job_id: str, workspace: Path, clips: Sequence[Path],
                session_factory: sessionmaker) -> None:
        captured["clips"] = [str(c) for c in clips]
        captured["workspace"] = str(workspace)
        update_job(session_factory, job_id, status="done", stage="complete", progress=1.0)

    cfg = _cfg(tmp_path)
    app = create_app(cfg, pipeline_fn=capture, max_workers=1)
    with TestClient(app) as client:
        uid = _completed_upload(client, "vlog.mp4")
        job_id = client.post("/jobs", json={"upload_ids": [uid]}).json()["id"]
        _wait_until(client, job_id)

    assert len(captured["clips"]) == 1
    assert captured["clips"][0].endswith("vlog.mp4")
    assert captured["workspace"].endswith(job_id)


# ---------------------------------------------------------------------------
# update_job helper
# ---------------------------------------------------------------------------

def test_update_job_noop_for_missing_id(default_client):
    """Updating a nonexistent job should be a silent no-op, not raise."""
    client, _ = default_client
    app = client.app
    # Should not raise.
    update_job(app.state.session_factory, "no-such-job", status="done")


def test_update_job_sets_fields(default_client):
    client, _ = default_client
    uid = _completed_upload(client)
    job_id = client.post("/jobs", json={"upload_ids": [uid]}).json()["id"]
    _wait_until(client, job_id)
    # Manually push it to a new state after completion.
    update_job(client.app.state.session_factory, job_id,
               stage="rechecked", progress=0.5)
    r = client.get(f"/jobs/{job_id}").json()
    assert r["stage"] == "rechecked"
    assert r["progress"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# OpenAPI exposes the routes
# ---------------------------------------------------------------------------

def test_openapi_includes_job_routes(default_client):
    client, _ = default_client
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/jobs" in paths
    assert "/jobs/{job_id}" in paths
