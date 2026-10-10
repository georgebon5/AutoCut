"""Tests for the output listing + download endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autocut_api.config import ApiConfig
from autocut_api.main import create_app
from autocut_api.models import Job


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> ApiConfig:
    return ApiConfig(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )


def _seed_job(
    app,
    *,
    status: str = "done",
    files: dict[str, bytes] | None = None,
) -> tuple[str, Path]:
    """Create a Job row + workspace populated with the given files."""
    workspace = app.state.config.jobs_dir / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    for name, data in (files or {}).items():
        (workspace / name).write_bytes(data)

    with app.state.session_factory() as session:
        job = Job(
            status=status,
            stage="complete" if status == "done" else "created",
            progress=1.0 if status == "done" else 0.0,
            workspace=str(workspace),
            config_json=json.dumps({"preset": "none"}),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id, workspace


@pytest.fixture
def app_client(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, max_workers=1)
    with TestClient(app) as client:
        yield app, client


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

def test_list_unknown_job_returns_404(app_client):
    _, client = app_client
    r = client.get("/jobs/unknown/outputs")
    assert r.status_code == 404


def test_list_before_done_returns_409(app_client):
    app, client = app_client
    jid, _ = _seed_job(app, status="running", files={"rough_cut.mp4": b"x"})
    r = client.get(f"/jobs/{jid}/outputs")
    assert r.status_code == 409


def test_list_empty_workspace_returns_empty_list(app_client):
    app, client = app_client
    jid, _ = _seed_job(app, files={})
    r = client.get(f"/jobs/{jid}/outputs")
    assert r.status_code == 200
    assert r.json() == {"outputs": []}


def test_list_classifies_video_and_edl(app_client):
    app, client = app_client
    jid, _ = _seed_job(app, files={
        "rough_cut.mp4": b"\x00" * 50,
        "edl.json": b"{}",
    })
    outs = {o["name"]: o for o in client.get(f"/jobs/{jid}/outputs").json()["outputs"]}
    assert outs["rough_cut.mp4"]["kind"] == "video"
    assert outs["rough_cut.mp4"]["size"] == 50
    assert outs["edl.json"]["kind"] == "edl"


def test_list_classifies_captions(app_client):
    app, client = app_client
    jid, _ = _seed_job(app, files={
        "clip.srt": b"1\n00:00:00,000 --> 00:00:01,000\nhi\n",
        "clip.ass": b"[Script Info]\n",
    })
    outs = {o["name"]: o["kind"] for o in client.get(f"/jobs/{jid}/outputs").json()["outputs"]}
    assert outs["clip.srt"] == "captions_srt"
    assert outs["clip.ass"] == "captions_ass"


def test_list_excludes_proxies_and_wavs(app_client):
    app, client = app_client
    jid, _ = _seed_job(app, files={
        "rough_cut.mp4": b"\x00",
        "clip_proxy.mp4": b"\x00",     # internal proxy — must NOT be listed
        "clip.wav": b"\x00",            # extracted audio — ditto
        "autocut.db": b"\x00",          # internal DB
    })
    names = [o["name"] for o in client.get(f"/jobs/{jid}/outputs").json()["outputs"]]
    assert names == ["rough_cut.mp4"]


def test_list_includes_multi_preset_outputs(app_client):
    app, client = app_client
    jid, _ = _seed_job(app, files={
        "rough_cut_tight.mp4": b"\x00",
        "rough_cut_medium.mp4": b"\x00",
        "rough_cut_loose.mp4": b"\x00",
        "edl_tight.json": b"{}",
        "edl_medium.json": b"{}",
        "edl_loose.json": b"{}",
    })
    outs = client.get(f"/jobs/{jid}/outputs").json()["outputs"]
    kinds = {o["name"]: o["kind"] for o in outs}
    assert kinds["rough_cut_tight.mp4"] == "video"
    assert kinds["edl_loose.json"] == "edl"
    assert len(outs) == 6


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def test_download_returns_file_bytes(app_client):
    app, client = app_client
    content = b"fake-mp4-body"
    jid, _ = _seed_job(app, files={"rough_cut.mp4": content})
    r = client.get(f"/jobs/{jid}/outputs/rough_cut.mp4")
    assert r.status_code == 200
    assert r.content == content
    assert r.headers["content-type"] == "video/mp4"


def test_download_edl_has_json_media_type(app_client):
    app, client = app_client
    jid, _ = _seed_job(app, files={"edl.json": b'{"ok":true}'})
    r = client.get(f"/jobs/{jid}/outputs/edl.json")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"


def test_download_srt_has_subrip_media_type(app_client):
    app, client = app_client
    jid, _ = _seed_job(app, files={"a.srt": b"1\n00:00\n"})
    r = client.get(f"/jobs/{jid}/outputs/a.srt")
    assert r.headers["content-type"] == "application/x-subrip"


def test_download_missing_file_returns_404(app_client):
    app, client = app_client
    jid, _ = _seed_job(app, files={})
    r = client.get(f"/jobs/{jid}/outputs/rough_cut.mp4")
    assert r.status_code == 404


def test_download_path_traversal_rejected(app_client):
    """`../` segments must be rejected (even though workspace is per-job)."""
    app, client = app_client
    jid, _ = _seed_job(app, files={"rough_cut.mp4": b"x"})
    r = client.get(f"/jobs/{jid}/outputs/..%2Fautocut.db")
    assert r.status_code in (400, 404)   # Starlette may already 404 on bad path


def test_download_hidden_files_rejected(app_client):
    app, client = app_client
    workspace = app.state.config.jobs_dir / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".secret").write_bytes(b"nope")
    with app.state.session_factory() as session:
        job = Job(status="done", stage="complete", progress=1.0,
                  workspace=str(workspace), config_json='{"preset":"none"}')
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id
    r = client.get(f"/jobs/{jid}/outputs/.secret")
    assert r.status_code == 400


def test_download_non_whitelisted_filename_rejected(app_client):
    app, client = app_client
    jid, workspace = _seed_job(app, files={"notes.txt": b"secret"})
    r = client.get(f"/jobs/{jid}/outputs/notes.txt")
    assert r.status_code == 400


def test_download_unknown_job_returns_404(app_client):
    _, client = app_client
    r = client.get("/jobs/unknown/outputs/rough_cut.mp4")
    assert r.status_code == 404


def test_download_before_done_returns_409(app_client):
    app, client = app_client
    jid, _ = _seed_job(app, status="running", files={"rough_cut.mp4": b"x"})
    r = client.get(f"/jobs/{jid}/outputs/rough_cut.mp4")
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------

def test_openapi_includes_output_routes(app_client):
    _, client = app_client
    schema = client.get("/openapi.json").json()
    assert "/jobs/{job_id}/outputs" in schema["paths"]
    assert "/jobs/{job_id}/outputs/{filename}" in schema["paths"]
