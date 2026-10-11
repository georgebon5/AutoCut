"""Tests for GET /jobs/{id}/export/fcpxml."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

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


def _make_edl(n: int = 2) -> EDL:
    proxy = ProxyInfo(
        clip_id="c",
        original_path=Path("/footage/original.mp4"),
        proxy_path=Path("/tmp/c_proxy.mp4"),
        audio_path=Path("/tmp/c.wav"),
        duration=10.0, fps=30.0, width=1920, height=1080,
    )
    segs = [Segment(clip_id="c", start=float(i), end=float(i) + 1.0, decision="keep")
            for i in range(n)]
    return EDL(clips=[proxy], segments=segs)


def _seed_job(
    app,
    *,
    status: str = "done",
    preset: str = "none",
    edl: EDL | None = None,
    extra_edls: dict[str, EDL] | None = None,
) -> str:
    workspace = app.state.config.jobs_dir / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    if edl is not None:
        name = "edl.json" if preset == "none" else f"edl_{preset}.json"
        save_edl(edl, workspace / name)
    for p, e in (extra_edls or {}).items():
        save_edl(e, workspace / f"edl_{p}.json")
    with app.state.session_factory() as session:
        job = Job(
            status=status,
            stage="complete" if status == "done" else "created",
            progress=1.0 if status == "done" else 0.0,
            workspace=str(workspace),
            config_json=json.dumps({"preset": preset}),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id


@pytest.fixture
def app_client(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, max_workers=1)
    with TestClient(app) as client:
        yield app, client


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def test_unknown_job_returns_404(app_client):
    _, client = app_client
    r = client.get("/jobs/does-not-exist/export/fcpxml")
    assert r.status_code == 404


def test_before_done_returns_409(app_client):
    app, client = app_client
    jid = _seed_job(app, status="running", edl=_make_edl())
    r = client.get(f"/jobs/{jid}/export/fcpxml")
    assert r.status_code == 409


def test_missing_edl_returns_404(app_client):
    app, client = app_client
    jid = _seed_job(app, edl=None)
    r = client.get(f"/jobs/{jid}/export/fcpxml")
    assert r.status_code == 404


def test_preset_all_without_query_returns_400(app_client):
    app, client = app_client
    jid = _seed_job(
        app, preset="all", edl=None,
        extra_edls={"tight": _make_edl(1), "medium": _make_edl(2)},
    )
    r = client.get(f"/jobs/{jid}/export/fcpxml")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_returns_valid_fcpxml(app_client):
    app, client = app_client
    jid = _seed_job(app, edl=_make_edl(2))
    r = client.get(f"/jobs/{jid}/export/fcpxml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(r.text)
    assert root.tag == "fcpxml"
    assert len(root.findall("library/event/project/sequence/spine/asset-clip")) == 2


def test_content_disposition_filename_includes_preset_and_job_id(app_client):
    app, client = app_client
    jid = _seed_job(app, preset="medium", edl=_make_edl(1))
    r = client.get(f"/jobs/{jid}/export/fcpxml")
    disposition = r.headers.get("content-disposition", "")
    assert "fcpxml" in disposition
    assert "medium" in disposition
    assert jid[:8] in disposition


def test_preset_query_routes_to_right_edl(app_client):
    app, client = app_client
    jid = _seed_job(
        app, preset="all", edl=None,
        extra_edls={"tight": _make_edl(1), "medium": _make_edl(3)},
    )
    tight = client.get(f"/jobs/{jid}/export/fcpxml?preset=tight").text
    medium = client.get(f"/jobs/{jid}/export/fcpxml?preset=medium").text
    assert len(ET.fromstring(tight).findall(".//asset-clip")) == 1
    assert len(ET.fromstring(medium).findall(".//asset-clip")) == 3


def test_openapi_includes_export_route(app_client):
    _, client = app_client
    schema = client.get("/openapi.json").json()
    assert "/jobs/{job_id}/export/fcpxml" in schema["paths"]
