"""Tests for autocut_api.pipeline_bridge — real bridge + fake pipeline.

The real `run_api_pipeline` is wired in by default via create_app(), so we
monkey-patch the underlying autocut.pipeline functions (the same fakes used
in test_pipeline.py / test_cli.py) and then drive a job end-to-end through
the API.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import torch
from fastapi.testclient import TestClient

from autocut_api.config import ApiConfig
from autocut_api.jobs import update_job
from autocut_api.main import create_app
from autocut_api.pipeline_bridge import JobReporter, run_api_pipeline
from autocut.models import Transcript
from tests.conftest import make_clip, skip_no_ffmpeg


# ---------------------------------------------------------------------------
# Shared fakes (match test_pipeline.py)
# ---------------------------------------------------------------------------

class _FakeVad:
    def reset_states(self) -> None: ...
    def __call__(self, chunk: torch.Tensor, sr: int) -> torch.Tensor:
        return torch.tensor(0.9)


def _fake_get_ts(wav, model, sampling_rate=16000, **_kw):
    dur = len(wav) / sampling_rate
    mid = dur / 2.0
    return [
        {"start": 0.1, "end": mid - 0.5},
        {"start": mid + 0.5, "end": dur - 0.1},
    ]


def _fake_load_vad():
    return _FakeVad(), _fake_get_ts


def _fake_load_whisper(cfg):
    return object()


def _fake_transcribe(audio_path, speech_regions, clip_id, cfg, model=None):
    return Transcript(clip_id=clip_id, language="el", words=[])


def _fake_enrich(segments, audio_path, transcript=None, sr=16000):
    pass


def _fake_enrich_motion(segments, proxy_path, sample_fps=5.0):
    pass


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr("autocut.pipeline.load_vad_model", _fake_load_vad)
    monkeypatch.setattr("autocut.pipeline.load_whisper_model", _fake_load_whisper)
    monkeypatch.setattr("autocut.pipeline.transcribe_clip", _fake_transcribe)
    monkeypatch.setattr("autocut.pipeline.enrich_segments", _fake_enrich)
    monkeypatch.setattr("autocut.pipeline.enrich_segments_motion", _fake_enrich_motion)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> ApiConfig:
    return ApiConfig(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        upload_chunk_size=1024 * 1024,  # 1 MB — bigger because real videos are bigger
    )


def _upload_video(client: TestClient, video: Path, as_name: str | None = None) -> str:
    """Upload a real file through the chunked-upload API, return upload_id."""
    data = video.read_bytes()
    name = as_name or video.name
    r = client.post("/uploads", json={"filename": name, "total_size": len(data)})
    uid = r.json()["id"]
    chunk_size = r.json()["chunk_size"]
    for i in range(0, len(data), chunk_size):
        client.put(f"/uploads/{uid}/chunks/{i // chunk_size}", content=data[i:i + chunk_size])
    r = client.post(f"/uploads/{uid}/complete")
    assert r.status_code == 200, r.text
    return uid


def _wait_until(client: TestClient, job_id: str, timeout: float = 30.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# JobReporter in isolation
# ---------------------------------------------------------------------------

def test_job_reporter_updates_stage_and_progress(tmp_path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, max_workers=1)
    with TestClient(app) as client:
        # Seed a job row directly (bypass the create endpoint).
        from autocut_api.models import Job
        with app.state.session_factory() as session:
            job = Job()
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id

        reporter = JobReporter(app.state.session_factory, job_id)
        reporter.stage("transcribing", 0.5)
        r = client.get(f"/jobs/{job_id}").json()
        assert r["status"] == "running"
        assert r["stage"] == "transcribing"
        assert r["progress"] == pytest.approx(0.5)


def test_job_reporter_info_is_log_only(tmp_path, caplog):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, max_workers=1)
    with TestClient(app):
        with app.state.session_factory() as session:
            from autocut_api.models import Job
            job = Job()
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id
        caplog.set_level("INFO", logger="autocut_api.pipeline_bridge")
        JobReporter(app.state.session_factory, job_id).info("hello")
    assert any("hello" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Failure path: real bridge, non-existent clip → job marked failed
# ---------------------------------------------------------------------------

def test_bridge_marks_job_failed_on_pipeline_error(tmp_path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, max_workers=1)   # default pipeline_fn = run_api_pipeline
    with TestClient(app) as client:
        # Create an Upload row with a bogus final_path so the real pipeline's
        # ingest step raises IngestError → bridge records failure.
        from autocut_api.models import Upload
        with app.state.session_factory() as session:
            up = Upload(
                filename="nope.mp4", total_size=1, chunk_size=cfg.upload_chunk_size,
                total_chunks=1, status="complete",
                final_path=str(tmp_path / "does-not-exist.mp4"),
            )
            session.add(up)
            session.commit()
            uid = up.id

        job_id = client.post("/jobs", json={"upload_ids": [uid]}).json()["id"]
        final = _wait_until(client, job_id)
        assert final["status"] == "failed"
        assert final["error"]


# ---------------------------------------------------------------------------
# Happy path: real bridge + real tiny clip (fakes for models), end-to-end
# ---------------------------------------------------------------------------

@skip_no_ffmpeg
def test_bridge_runs_real_pipeline_end_to_end(tmp_path: Path, patched):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, max_workers=1)   # default pipeline_fn = run_api_pipeline
    with TestClient(app) as client:
        clip = make_clip(tmp_path / "clip.mp4", duration=5.0)
        uid = _upload_video(client, clip)
        job_id = client.post("/jobs", json={"upload_ids": [uid]}).json()["id"]
        final = _wait_until(client, job_id, timeout=60.0)
        assert final["status"] == "done", final
        assert final["stage"] == "complete"
        assert final["progress"] == pytest.approx(1.0)
        # Workspace should contain the rendered mp4 + edl.
        workspace = Path(final["workspace"])
        assert (workspace / "rough_cut.mp4").exists()
        assert (workspace / "edl.json").exists()


@skip_no_ffmpeg
def test_bridge_reports_progress_through_stages(tmp_path: Path, patched):
    """A job's stage field should move away from 'created' during the run."""
    cfg = _cfg(tmp_path)
    app = create_app(cfg, max_workers=1)
    with TestClient(app) as client:
        clip = make_clip(tmp_path / "clip.mp4", duration=4.0)
        uid = _upload_video(client, clip)
        job_id = client.post("/jobs", json={"upload_ids": [uid]}).json()["id"]
        final = _wait_until(client, job_id, timeout=60.0)
        assert final["stage"] == "complete"
