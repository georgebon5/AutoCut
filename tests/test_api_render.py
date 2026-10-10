"""Tests for POST /jobs/{id}/render — the Phase 4 caching contract.

The core invariant: re-rendering after an EDL override must NOT re-run
analysis (ingest / VAD / transcribe / features / scoring). The tests here
monkey-patch autocut.render.render inside pipeline_bridge so we can:
  1. Observe exactly which segments were handed to the renderer (proving
     user overrides from PATCH reach the ffmpeg call).
  2. Assert that no pipeline-analysis function was invoked.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autocut.edl import save_edl
from autocut.models import EDL, ProxyInfo, Segment
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


def _make_proxy(workspace: Path, clip_id: str = "c") -> ProxyInfo:
    # The proxy / audio files must exist for a real render; for a stubbed
    # render they just need to be referenced.
    return ProxyInfo(
        clip_id=clip_id,
        original_path=workspace / f"{clip_id}.mp4",
        proxy_path=workspace / f"{clip_id}_proxy.mp4",
        audio_path=workspace / f"{clip_id}.wav",
        duration=10.0, fps=30.0, width=1280, height=720,
    )


def _make_edl(workspace: Path, n: int = 3) -> EDL:
    proxy = _make_proxy(workspace)
    segs: list[Segment] = []
    for i in range(n):
        s = Segment(clip_id="c", start=float(i), end=float(i) + 1.0, decision="keep")
        s.interest_score = 0.5
        segs.append(s)
    return EDL(clips=[proxy], segments=segs)


def _seed_job(
    app,
    *,
    status: str = "done",
    preset: str = "none",
    edl: EDL | None = None,
) -> tuple[str, Path]:
    workspace = app.state.config.jobs_dir / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    if edl is not None:
        name = "edl.json" if preset == "none" else f"edl_{preset}.json"
        save_edl(edl, workspace / name)
    with app.state.session_factory() as session:
        job = Job(
            status=status,
            stage="complete" if status == "done" else "created",
            progress=1.0 if status == "done" else 0.0,
            workspace=str(workspace),
            config_json=json.dumps({"preset": preset, "zoom": False}),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id, workspace


def _wait_until(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.02)
    raise TimeoutError(f"job {job_id} did not finish in {timeout}s")


@pytest.fixture
def app_client(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, max_workers=1)
    with TestClient(app) as client:
        yield app, client, tmp_path


@pytest.fixture
def stub_render(monkeypatch):
    """Replace autocut.render.render with a recording stub."""
    calls: list[dict] = []

    def _fake_render(proxies, segments, output_dir, cfg, output_name="rough_cut.mp4",
                     hook=None, zoom=None):
        calls.append({
            "proxies": proxies, "segments": list(segments),
            "output_dir": output_dir, "output_name": output_name,
            "hook": hook, "zoom": zoom,
        })
        out = Path(output_dir) / output_name
        out.write_bytes(b"fake-mp4-body")
        return out

    import autocut_api.pipeline_bridge as bridge
    monkeypatch.setattr(bridge, "render", _fake_render)
    return calls


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def test_render_unknown_job_returns_404(app_client):
    _, client, _ = app_client
    r = client.post("/jobs/unknown/render")
    assert r.status_code == 404


def test_render_before_done_returns_409(app_client):
    app, client, _ = app_client
    jid, _ = _seed_job(app, status="running", edl=_make_edl(app.state.config.jobs_dir / "ws"))
    r = client.post(f"/jobs/{jid}/render")
    assert r.status_code == 409


def test_render_missing_edl_returns_404(app_client):
    app, client, _ = app_client
    jid, _ = _seed_job(app, edl=None)
    r = client.post(f"/jobs/{jid}/render")
    assert r.status_code == 404


def test_render_preset_all_without_query_returns_400(app_client):
    app, client, _ = app_client
    workspace = app.state.config.jobs_dir / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    save_edl(_make_edl(workspace), workspace / "edl_tight.json")
    with app.state.session_factory() as session:
        job = Job(status="done", stage="complete", progress=1.0,
                  workspace=str(workspace),
                  config_json=json.dumps({"preset": "all"}))
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id
    r = client.post(f"/jobs/{jid}/render")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Caching contract — no re-analysis, overrides reach the renderer
# ---------------------------------------------------------------------------

def test_render_lifecycle_done_running_done(app_client, stub_render):
    app, client, _ = app_client
    workspace = app.state.config.jobs_dir / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    jid, _ = _seed_job(app, edl=_make_edl(workspace))

    r = client.post(f"/jobs/{jid}/render")
    assert r.status_code == 200
    final = _wait_until(client, jid)
    assert final["status"] == "done"
    assert final["stage"] == "complete"
    assert final["progress"] == pytest.approx(1.0)


def test_render_uses_overrides_from_edl_file(app_client, stub_render):
    app, client, _ = app_client
    workspace = app.state.config.jobs_dir / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    jid, _ = _seed_job(app, edl=_make_edl(workspace, n=3))

    # Toggle middle segment to cut via the override API.
    client.patch(f"/jobs/{jid}/segments/1", json={"decision": "cut"})

    client.post(f"/jobs/{jid}/render")
    _wait_until(client, jid)

    assert len(stub_render) == 1
    received = stub_render[0]["segments"]
    assert received[1].decision == "cut"
    assert received[1].decision_source == "user"
    # Other segments must still be keep.
    assert received[0].decision == "keep"
    assert received[2].decision == "keep"


def test_render_writes_rough_cut_mp4_in_workspace(app_client, stub_render):
    app, client, _ = app_client
    workspace = app.state.config.jobs_dir / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    jid, _ = _seed_job(app, edl=_make_edl(workspace))

    client.post(f"/jobs/{jid}/render")
    _wait_until(client, jid)
    assert (workspace / "rough_cut.mp4").exists()


def test_render_output_name_uses_preset(app_client, stub_render):
    app, client, _ = app_client
    workspace = app.state.config.jobs_dir / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    jid, _ = _seed_job(app, preset="medium", edl=_make_edl(workspace))

    client.post(f"/jobs/{jid}/render")
    _wait_until(client, jid)
    assert stub_render[0]["output_name"] == "rough_cut_medium.mp4"


def test_render_does_not_invoke_analysis_functions(app_client, stub_render, monkeypatch):
    """Caching contract: no pipeline-analysis function should fire during re-render."""
    tripwires: list[str] = []

    def _boom(name):
        def _fn(*args, **kwargs):
            tripwires.append(name)
        return _fn

    # Patch the analysis entry points at the pipeline module level — if any
    # fire, the test records it.
    for name in ("ingest_clips", "detect_speech", "transcribe_clip",
                 "enrich_segments", "enrich_segments_motion", "score_segments",
                 "run_pipeline"):
        monkeypatch.setattr(f"autocut.pipeline.{name}", _boom(name), raising=False)

    app, client, _ = app_client
    workspace = app.state.config.jobs_dir / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    jid, _ = _seed_job(app, edl=_make_edl(workspace))

    client.post(f"/jobs/{jid}/render")
    _wait_until(client, jid)
    assert tripwires == [], f"re-render triggered analysis: {tripwires}"


def test_render_zoom_enabled_from_job_config(app_client, stub_render):
    """If the original job had zoom=True, the re-render should enable zoom too."""
    app, client, _ = app_client
    workspace = app.state.config.jobs_dir / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    with app.state.session_factory() as session:
        job = Job(status="done", stage="complete", progress=1.0,
                  workspace=str(workspace),
                  config_json=json.dumps({"preset": "none", "zoom": True}))
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.id
    save_edl(_make_edl(workspace), workspace / "edl.json")

    client.post(f"/jobs/{jid}/render")
    _wait_until(client, jid)
    zoom_cfg = stub_render[0]["zoom"]
    assert zoom_cfg is not None and zoom_cfg.enabled is True


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------

def test_openapi_includes_render_route(app_client):
    _, client, _ = app_client
    schema = client.get("/openapi.json").json()
    assert "/jobs/{job_id}/render" in schema["paths"]
