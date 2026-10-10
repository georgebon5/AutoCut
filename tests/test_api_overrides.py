"""Tests for user-override logging and the overrides listing endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autocut.edl import save_edl
from autocut.models import EDL, ProxyInfo, Segment
from autocut_api.config import ApiConfig
from autocut_api.main import create_app
from autocut_api.models import Job, SegmentOverride


# ---------------------------------------------------------------------------
# Helpers (mirrors tests/test_api_edl.py)
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> ApiConfig:
    return ApiConfig(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )


def _make_edl(num_segments: int = 3) -> EDL:
    segs: list[Segment] = []
    for i in range(num_segments):
        s = Segment(clip_id="c", start=float(i), end=float(i + 1), decision="keep")
        s.interest_score = 0.5 + i * 0.1
        s.features = {"rms_energy": 0.08, "motion_mean": 0.12}
        segs.append(s)
    proxy = ProxyInfo(
        clip_id="c",
        original_path=Path("v.mp4"), proxy_path=Path("v_proxy.mp4"),
        audio_path=Path("v.wav"),
        duration=10.0, fps=30.0, width=1280, height=720,
    )
    return EDL(clips=[proxy], segments=segs)


def _seed_job(
    app,
    *,
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
            status="done", stage="complete", progress=1.0,
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
# PATCH inserts override rows
# ---------------------------------------------------------------------------

def test_patch_inserts_override_row(app_client):
    app, client = app_client
    jid = _seed_job(app, edl=_make_edl(num_segments=3))
    client.patch(f"/jobs/{jid}/segments/1", json={"decision": "cut"})

    with app.state.session_factory() as session:
        rows = session.query(SegmentOverride).all()
        assert len(rows) == 1
        r = rows[0]
        assert r.job_id == jid
        assert r.preset == "none"
        assert r.segment_index == 1
        assert r.previous_decision == "keep"
        assert r.new_decision == "cut"
        assert r.interest_score == pytest.approx(0.6)
        assert json.loads(r.features_json) == {"rms_energy": 0.08, "motion_mean": 0.12}


def test_patch_captures_segment_start_end_and_clip(app_client):
    app, client = app_client
    jid = _seed_job(app, edl=_make_edl(num_segments=2))
    client.patch(f"/jobs/{jid}/segments/0", json={"decision": "cut"})
    with app.state.session_factory() as session:
        row = session.query(SegmentOverride).first()
        assert row.clip_id == "c"
        assert row.segment_start == pytest.approx(0.0)
        assert row.segment_end == pytest.approx(1.0)


def test_multiple_patches_append_rows_in_order(app_client):
    app, client = app_client
    jid = _seed_job(app, edl=_make_edl(num_segments=2))
    client.patch(f"/jobs/{jid}/segments/0", json={"decision": "cut"})
    client.patch(f"/jobs/{jid}/segments/0", json={"decision": "keep"})
    client.patch(f"/jobs/{jid}/segments/0", json={"decision": "cut"})

    with app.state.session_factory() as session:
        rows = session.query(SegmentOverride).order_by(SegmentOverride.id).all()
        assert len(rows) == 3
        # Previous decisions should chain: auto(keep) → user(cut) → user(keep)
        assert [r.previous_decision for r in rows] == ["keep", "cut", "keep"]
        assert [r.new_decision for r in rows] == ["cut", "keep", "cut"]


def test_first_row_previous_is_the_auto_decision(app_client):
    """Phase-5 training rule: first override for a segment captures auto_decision."""
    app, client = app_client
    jid = _seed_job(app, edl=_make_edl(num_segments=2))
    # Flip segment 1 twice; its auto was "keep".
    client.patch(f"/jobs/{jid}/segments/1", json={"decision": "cut"})
    client.patch(f"/jobs/{jid}/segments/1", json={"decision": "keep"})
    with app.state.session_factory() as session:
        first = session.query(SegmentOverride).order_by(SegmentOverride.id).first()
        assert first.previous_decision == "keep"   # == original auto


def test_preset_label_recorded_from_single_preset(app_client):
    app, client = app_client
    jid = _seed_job(app, preset="medium", edl=_make_edl(num_segments=2))
    client.patch(f"/jobs/{jid}/segments/0", json={"decision": "cut"})
    with app.state.session_factory() as session:
        row = session.query(SegmentOverride).first()
        assert row.preset == "medium"


def test_preset_label_recorded_from_query_on_all(app_client):
    app, client = app_client
    jid = _seed_job(
        app, preset="all", edl=None,
        extra_edls={"tight": _make_edl(2), "medium": _make_edl(2)},
    )
    client.patch(f"/jobs/{jid}/segments/0?preset=tight", json={"decision": "cut"})
    client.patch(f"/jobs/{jid}/segments/0?preset=medium", json={"decision": "cut"})
    with app.state.session_factory() as session:
        rows = session.query(SegmentOverride).order_by(SegmentOverride.id).all()
        assert [r.preset for r in rows] == ["tight", "medium"]


def test_failed_patch_does_not_insert_row(app_client):
    """A rejected PATCH (bad decision, out-of-range) must leave the log untouched."""
    app, client = app_client
    jid = _seed_job(app, edl=_make_edl(num_segments=2))
    client.patch(f"/jobs/{jid}/segments/5", json={"decision": "cut"})     # 400
    client.patch(f"/jobs/{jid}/segments/0", json={"decision": "maybe"})   # 422
    with app.state.session_factory() as session:
        assert session.query(SegmentOverride).count() == 0


# ---------------------------------------------------------------------------
# GET /jobs/{id}/overrides
# ---------------------------------------------------------------------------

def test_get_overrides_unknown_job_returns_404(app_client):
    _, client = app_client
    r = client.get("/jobs/does-not-exist/overrides")
    assert r.status_code == 404


def test_get_overrides_empty_list_when_no_patches(app_client):
    app, client = app_client
    jid = _seed_job(app, edl=_make_edl(num_segments=1))
    r = client.get(f"/jobs/{jid}/overrides")
    assert r.status_code == 200
    assert r.json() == {"overrides": []}


def test_get_overrides_returns_chronological_list(app_client):
    app, client = app_client
    jid = _seed_job(app, edl=_make_edl(num_segments=3))
    client.patch(f"/jobs/{jid}/segments/0", json={"decision": "cut"})
    client.patch(f"/jobs/{jid}/segments/2", json={"decision": "cut"})
    client.patch(f"/jobs/{jid}/segments/0", json={"decision": "keep"})
    body = client.get(f"/jobs/{jid}/overrides").json()
    assert len(body["overrides"]) == 3
    rows = body["overrides"]
    assert [r["segment_index"] for r in rows] == [0, 2, 0]
    assert [r["new_decision"] for r in rows] == ["cut", "cut", "keep"]
    assert rows[0]["features"] == {"rms_energy": 0.08, "motion_mean": 0.12}


def test_get_overrides_includes_preset_and_clip(app_client):
    app, client = app_client
    jid = _seed_job(app, preset="loose", edl=_make_edl(num_segments=1))
    client.patch(f"/jobs/{jid}/segments/0", json={"decision": "cut"})
    row = client.get(f"/jobs/{jid}/overrides").json()["overrides"][0]
    assert row["preset"] == "loose"
    assert row["clip_id"] == "c"
    assert row["interest_score"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------

def test_openapi_includes_overrides_route(app_client):
    _, client = app_client
    schema = client.get("/openapi.json").json()
    assert "/jobs/{job_id}/overrides" in schema["paths"]
