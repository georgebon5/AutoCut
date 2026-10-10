"""Tests for the EDL review endpoints: GET + segment PATCH overrides."""

from __future__ import annotations

import json
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


def _make_proxy(clip_id: str = "c") -> ProxyInfo:
    return ProxyInfo(
        clip_id=clip_id,
        original_path=Path("v.mp4"),
        proxy_path=Path("v_proxy.mp4"),
        audio_path=Path("v.wav"),
        duration=10.0, fps=30.0, width=1280, height=720,
    )


def _make_edl(num_segments: int = 3, decision: str = "keep") -> EDL:
    segs = []
    for i in range(num_segments):
        s = Segment(
            clip_id="c", start=float(i), end=float(i + 1), decision=decision,
        )
        s.interest_score = 0.5
        segs.append(s)
    return EDL(clips=[_make_proxy()], segments=segs)


def _seed_job(
    app,
    tmp_path: Path,
    *,
    preset: str = "none",
    status: str = "done",
    edl: EDL | None = None,
    extra_edls: dict[str, EDL] | None = None,
) -> str:
    """Create a Job row + its workspace populated with EDL file(s)."""
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
            config_json=json.dumps({
                "preset": preset, "hook": False, "pacing": False, "zoom": False,
            }),
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
        yield app, client, tmp_path


# ---------------------------------------------------------------------------
# GET /jobs/{id}/edl
# ---------------------------------------------------------------------------

def test_get_edl_unknown_job_returns_404(app_client):
    _, client, _ = app_client
    r = client.get("/jobs/does-not-exist/edl")
    assert r.status_code == 404


def test_get_edl_before_done_returns_409(app_client):
    app, client, tmp = app_client
    jid = _seed_job(app, tmp, edl=_make_edl(), status="running")
    r = client.get(f"/jobs/{jid}/edl")
    assert r.status_code == 409


def test_get_edl_missing_file_returns_404(app_client):
    app, client, tmp = app_client
    jid = _seed_job(app, tmp, edl=None)   # no EDL file written
    r = client.get(f"/jobs/{jid}/edl")
    assert r.status_code == 404


def test_get_edl_returns_segments(app_client):
    app, client, tmp = app_client
    jid = _seed_job(app, tmp, edl=_make_edl(num_segments=3))
    body = client.get(f"/jobs/{jid}/edl").json()
    assert len(body["segments"]) == 3
    assert body["segments"][0]["decision"] == "keep"


# ---------------------------------------------------------------------------
# PATCH /jobs/{id}/segments/{index}
# ---------------------------------------------------------------------------

def test_patch_flips_decision_and_marks_user(app_client):
    app, client, tmp = app_client
    jid = _seed_job(app, tmp, edl=_make_edl(num_segments=2))
    r = client.patch(
        f"/jobs/{jid}/segments/0",
        json={"decision": "cut"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "cut"
    assert body["decision_source"] == "user"
    assert any("user cut" in reason for reason in body["reasons"])


def test_patch_persists_across_get(app_client):
    app, client, tmp = app_client
    jid = _seed_job(app, tmp, edl=_make_edl(num_segments=2))
    client.patch(f"/jobs/{jid}/segments/1", json={"decision": "cut"})
    edl = client.get(f"/jobs/{jid}/edl").json()
    assert edl["segments"][1]["decision"] == "cut"
    assert edl["segments"][1]["decision_source"] == "user"


def test_patch_can_toggle_back(app_client):
    app, client, tmp = app_client
    jid = _seed_job(app, tmp, edl=_make_edl(num_segments=2, decision="cut"))
    client.patch(f"/jobs/{jid}/segments/0", json={"decision": "keep"})
    client.patch(f"/jobs/{jid}/segments/0", json={"decision": "cut"})
    seg = client.get(f"/jobs/{jid}/edl").json()["segments"][0]
    assert seg["decision"] == "cut"
    # Both overrides should be recorded in reasons.
    reasons = [r for r in seg["reasons"] if r.startswith("user")]
    assert len(reasons) == 2


def test_patch_out_of_range_returns_400(app_client):
    app, client, tmp = app_client
    jid = _seed_job(app, tmp, edl=_make_edl(num_segments=2))
    r = client.patch(f"/jobs/{jid}/segments/5", json={"decision": "cut"})
    assert r.status_code == 400


def test_patch_negative_index_returns_400(app_client):
    app, client, tmp = app_client
    jid = _seed_job(app, tmp, edl=_make_edl(num_segments=2))
    r = client.patch(f"/jobs/{jid}/segments/-1", json={"decision": "cut"})
    assert r.status_code == 400


def test_patch_invalid_decision_returns_422(app_client):
    app, client, tmp = app_client
    jid = _seed_job(app, tmp, edl=_make_edl(num_segments=2))
    r = client.patch(f"/jobs/{jid}/segments/0", json={"decision": "maybe"})
    assert r.status_code == 422


def test_patch_before_done_returns_409(app_client):
    app, client, tmp = app_client
    jid = _seed_job(app, tmp, edl=_make_edl(), status="running")
    r = client.patch(f"/jobs/{jid}/segments/0", json={"decision": "cut"})
    assert r.status_code == 409


def test_patch_unknown_job_returns_404(app_client):
    _, client, _ = app_client
    r = client.patch("/jobs/no-such-job/segments/0", json={"decision": "cut"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Preset routing
# ---------------------------------------------------------------------------

def test_get_edl_uses_preset_from_job_config(app_client):
    app, client, tmp = app_client
    jid = _seed_job(app, tmp, preset="medium", edl=_make_edl(num_segments=2))
    r = client.get(f"/jobs/{jid}/edl")
    assert r.status_code == 200
    assert len(r.json()["segments"]) == 2


def test_get_edl_preset_query_overrides_config(app_client):
    app, client, tmp = app_client
    # Job config says preset=medium, but we write a different EDL under 'tight'
    # and ask for it explicitly.
    jid = _seed_job(
        app, tmp, preset="medium",
        edl=_make_edl(num_segments=2),           # written as edl_medium.json
        extra_edls={"tight": _make_edl(num_segments=5)},
    )
    r = client.get(f"/jobs/{jid}/edl?preset=tight")
    assert r.status_code == 200
    assert len(r.json()["segments"]) == 5


def test_get_edl_preset_all_without_query_returns_400(app_client):
    app, client, tmp = app_client
    jid = _seed_job(
        app, tmp, preset="all", edl=None,
        extra_edls={
            "tight": _make_edl(1),
            "medium": _make_edl(2),
            "loose": _make_edl(3),
        },
    )
    r = client.get(f"/jobs/{jid}/edl")
    assert r.status_code == 400
    assert "preset=all" in r.json()["detail"]


def test_get_edl_preset_all_with_query_returns_right_edl(app_client):
    app, client, tmp = app_client
    jid = _seed_job(
        app, tmp, preset="all", edl=None,
        extra_edls={
            "tight": _make_edl(1),
            "medium": _make_edl(2),
            "loose": _make_edl(3),
        },
    )
    r = client.get(f"/jobs/{jid}/edl?preset=medium")
    assert r.status_code == 200
    assert len(r.json()["segments"]) == 2


def test_patch_preset_query_scoped_to_right_file(app_client):
    app, client, tmp = app_client
    jid = _seed_job(
        app, tmp, preset="all", edl=None,
        extra_edls={
            "tight": _make_edl(2),
            "medium": _make_edl(2),
        },
    )
    client.patch(
        f"/jobs/{jid}/segments/0?preset=tight",
        json={"decision": "cut"},
    )
    tight = client.get(f"/jobs/{jid}/edl?preset=tight").json()
    medium = client.get(f"/jobs/{jid}/edl?preset=medium").json()
    assert tight["segments"][0]["decision"] == "cut"
    assert medium["segments"][0]["decision"] == "keep"


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------

def test_openapi_includes_edl_routes(app_client):
    _, client, _ = app_client
    schema = client.get("/openapi.json").json()
    assert "/jobs/{job_id}/edl" in schema["paths"]
    assert "/jobs/{job_id}/segments/{index}" in schema["paths"]
