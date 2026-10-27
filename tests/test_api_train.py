"""Tests for the auto-training endpoints (POST /train, GET /train/status)."""

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
from autocut_api.models import Job, SegmentOverride, TrainingRun
from autocut_api.routes.train import AUTO_TRAIN_EVERY, MIN_EXAMPLES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> ApiConfig:
    return ApiConfig(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )


def _make_edl(num_segments: int = 6) -> EDL:
    segs: list[Segment] = []
    for i in range(num_segments):
        s = Segment(
            clip_id="c",
            start=float(i),
            end=float(i + 1),
            decision="keep" if i % 2 == 0 else "cut",
        )
        s.interest_score = 0.3 + i * 0.05
        s.features = {
            "rms_energy": 0.05 + i * 0.01,
            "motion_mean": 0.1 + i * 0.02,
            "speech_ratio": 0.4 + i * 0.03,
        }
        segs.append(s)
    proxy = ProxyInfo(
        clip_id="c",
        original_path=Path("v.mp4"), proxy_path=Path("v_proxy.mp4"),
        audio_path=Path("v.wav"),
        duration=float(num_segments), fps=30.0, width=1280, height=720,
    )
    return EDL(clips=[proxy], segments=segs)


def _seed_job(app, *, preset: str = "none", edl: EDL | None = None) -> str:
    workspace = app.state.config.jobs_dir / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    if edl is not None:
        name = "edl.json" if preset == "none" else f"edl_{preset}.json"
        save_edl(edl, workspace / name)
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


def _seed_overrides(app, client, n: int) -> None:
    """Patch segments 0..n-1 to cut (they all start as keep/cut alternating)."""
    jid = _seed_job(app, edl=_make_edl(num_segments=max(n, 4)))
    for i in range(n):
        client.patch(f"/jobs/{jid}/segments/{i}", json={"decision": "cut"})


@pytest.fixture
def app_client(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, max_workers=1)
    with TestClient(app) as client:
        yield app, client


# ---------------------------------------------------------------------------
# GET /train/status — baseline behaviour
# ---------------------------------------------------------------------------

def test_status_empty_db(app_client):
    _, client = app_client
    r = client.get("/train/status")
    assert r.status_code == 200
    body = r.json()
    assert body["n_overrides"] == 0
    assert body["model_active"] is False
    assert body["last_trained_at"] is None
    assert body["model_f1"] is None
    assert body["auto_train_threshold"] == AUTO_TRAIN_EVERY


def test_status_counts_overrides(app_client):
    app, client = app_client
    _seed_overrides(app, client, n=3)
    body = client.get("/train/status").json()
    assert body["n_overrides"] == 3


def test_status_model_active_flag(app_client):
    app, client = app_client
    # Write a model file manually so the flag flips.
    model_path = app.state.config.resolved_model_path
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text("{}")
    body = client.get("/train/status").json()
    assert body["model_active"] is True


# ---------------------------------------------------------------------------
# POST /train — not enough data
# ---------------------------------------------------------------------------

def test_train_too_few_examples_returns_400(app_client):
    app, client = app_client
    _seed_overrides(app, client, n=1)
    r = client.post("/train")
    assert r.status_code == 400
    assert "examples" in r.json()["detail"]


# ---------------------------------------------------------------------------
# POST /train — successful run
# ---------------------------------------------------------------------------

def test_train_returns_metrics(app_client):
    app, client = app_client
    _seed_overrides(app, client, n=MIN_EXAMPLES + 4)
    r = client.post("/train")
    assert r.status_code == 200
    body = r.json()
    assert "model_f1" in body
    assert "heuristic_f1" in body
    assert isinstance(body["beats_baseline"], bool)
    assert body["n_examples"] >= MIN_EXAMPLES
    assert "message" in body


def test_train_deploys_when_beating_baseline(app_client):
    """When the model beats heuristic, the model file should appear on disk."""
    app, client = app_client
    # Create enough varied examples that the model has a real chance of winning.
    _seed_overrides(app, client, n=MIN_EXAMPLES + 4)
    body = client.post("/train").json()
    model_path = app.state.config.resolved_model_path
    if body["beats_baseline"]:
        assert model_path.exists()
        # Should be loadable as a LogisticModel.
        from autocut.trainer import LogisticModel
        m = LogisticModel.load(model_path)
        assert len(m.feature_names) > 0
    # Either way, no crash.


def test_train_records_training_run_in_db(app_client):
    app, client = app_client
    _seed_overrides(app, client, n=MIN_EXAMPLES + 2)
    client.post("/train")
    with app.state.session_factory() as session:
        runs = session.query(TrainingRun).all()
        assert len(runs) == 1
        run = runs[0]
        assert run.n_examples >= MIN_EXAMPLES
        assert run.model_f1 >= 0.0
        assert run.heuristic_f1 >= 0.0


def test_train_status_reflects_last_run(app_client):
    app, client = app_client
    _seed_overrides(app, client, n=MIN_EXAMPLES + 2)
    client.post("/train")
    status = client.get("/train/status").json()
    assert status["last_trained_at"] is not None
    assert status["model_f1"] is not None
    assert status["n_examples"] >= MIN_EXAMPLES


def test_multiple_train_calls_accumulate_runs(app_client):
    app, client = app_client
    _seed_overrides(app, client, n=MIN_EXAMPLES + 2)
    client.post("/train")
    client.post("/train")
    with app.state.session_factory() as session:
        assert session.query(TrainingRun).count() == 2


# ---------------------------------------------------------------------------
# collect_from_override_rows — unit tests
# ---------------------------------------------------------------------------

def _fake_override(id_, seg_idx, prev, new, feat, ts_sec=0):
    """Lightweight stand-in for SegmentOverride that collect_from_override_rows accepts."""
    from types import SimpleNamespace
    from datetime import datetime
    return SimpleNamespace(
        id=id_,
        job_id="j1",
        preset="none",
        segment_index=seg_idx,
        previous_decision=prev,
        new_decision=new,
        features_json=json.dumps(feat),
        created_at=datetime(2026, 1, 1, 0, 0, ts_sec),
    )


def test_collect_from_override_rows_basic():
    from autocut.trainer import collect_from_override_rows
    rows = [
        _fake_override(0, 0, "keep", "cut", {"rms_energy": 0.1, "motion_mean": 0.2}),
        _fake_override(1, 1, "cut", "keep", {"rms_energy": 0.3, "motion_mean": 0.4}),
    ]
    examples = collect_from_override_rows(rows)
    assert len(examples) == 2
    labels = {e.label for e in examples}
    assert labels == {0, 1}


def test_collect_from_override_rows_keeps_last_decision():
    """When a segment is flipped multiple times, only the last counts."""
    from autocut.trainer import collect_from_override_rows
    rows = [
        _fake_override(1, 0, "keep", "cut",  {"rms_energy": 0.1}, ts_sec=0),
        _fake_override(2, 0, "cut",  "keep", {"rms_energy": 0.1}, ts_sec=1),
        _fake_override(3, 0, "keep", "cut",  {"rms_energy": 0.1}, ts_sec=2),  # final
    ]
    examples = collect_from_override_rows(rows)
    assert len(examples) == 1
    assert examples[0].label == 0          # "cut"
    assert examples[0].auto_decision == "keep"   # from first row


def test_collect_from_override_rows_skips_missing_features():
    from autocut.trainer import collect_from_override_rows
    rows = [_fake_override(1, 0, "keep", "cut", {})]   # empty → no float features → skip
    examples = collect_from_override_rows(rows)
    assert examples == []


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------

def test_openapi_includes_train_routes(app_client):
    _, client = app_client
    paths = client.get("/openapi.json").json()["paths"]
    assert "/train" in paths
    assert "/train/status" in paths
