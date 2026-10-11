"""Tests for the model-management endpoints (POST /models, GET /models/active)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autocut_api.config import ApiConfig
from autocut_api.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> ApiConfig:
    return ApiConfig(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )


def _valid_model_dict(n_features: int = 2) -> dict:
    """The minimal JSON LogisticModel.save() would write."""
    return {
        "feature_names": [f"f{i}" for i in range(n_features)],
        "weights": [0.1] * n_features,
        "bias": 0.0,
        "feature_mean": [0.0] * n_features,
        "feature_std": [1.0] * n_features,
    }


@pytest.fixture
def app_client(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg, max_workers=1)
    with TestClient(app) as client:
        yield app, client, tmp_path


# ---------------------------------------------------------------------------
# POST /models — upload
# ---------------------------------------------------------------------------

def test_upload_accepts_valid_model(app_client):
    app, client, _ = app_client
    r = client.post("/models", json=_valid_model_dict(n_features=5))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["n_features"] == 5
    assert body["bias"] == 0.0
    assert body["size_bytes"] > 0


def test_upload_writes_file_to_resolved_path(app_client):
    app, client, _ = app_client
    client.post("/models", json=_valid_model_dict())
    assert app.state.config.resolved_model_path.exists()


def test_upload_rejects_missing_required_fields(app_client):
    _, client, _ = app_client
    bad = _valid_model_dict()
    del bad["bias"]
    r = client.post("/models", json=bad)
    assert r.status_code == 400
    assert "invalid model" in r.json()["detail"]


def test_upload_rejects_wrong_type_weights(app_client):
    _, client, _ = app_client
    bad = _valid_model_dict()
    bad["weights"] = "not-a-list"
    r = client.post("/models", json=bad)
    assert r.status_code == 400


def test_upload_atomic_swap_overwrites_existing(app_client):
    """Second POST must atomically replace the first — no half-written files."""
    app, client, _ = app_client
    client.post("/models", json=_valid_model_dict(n_features=3))
    first_size = app.state.config.resolved_model_path.stat().st_size

    client.post("/models", json=_valid_model_dict(n_features=12))
    assert app.state.config.resolved_model_path.exists()
    new_size = app.state.config.resolved_model_path.stat().st_size
    assert new_size != first_size   # content changed

    # Confirm metadata matches the newer model.
    info = client.get("/models/active").json()
    assert info["n_features"] == 12


# ---------------------------------------------------------------------------
# GET /models/active
# ---------------------------------------------------------------------------

def test_get_active_returns_404_when_absent(app_client):
    _, client, _ = app_client
    r = client.get("/models/active")
    assert r.status_code == 404


def test_get_active_returns_metadata_after_upload(app_client):
    _, client, _ = app_client
    client.post("/models", json=_valid_model_dict(n_features=7))
    body = client.get("/models/active").json()
    assert body["n_features"] == 7
    assert body["path"].endswith("active_model.json")


def test_get_active_500_when_file_is_junk(app_client, tmp_path):
    """If something external corrupts the model file, surface a clear 500."""
    app, client, _ = app_client
    path = app.state.config.resolved_model_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all")
    r = client.get("/models/active")
    assert r.status_code == 500
    assert "unreadable" in r.json()["detail"]


# ---------------------------------------------------------------------------
# DELETE /models/active
# ---------------------------------------------------------------------------

def test_delete_active_removes_file(app_client):
    app, client, _ = app_client
    client.post("/models", json=_valid_model_dict())
    assert client.delete("/models/active").status_code == 204
    assert not app.state.config.resolved_model_path.exists()
    assert client.get("/models/active").status_code == 404


def test_delete_active_when_absent_returns_404(app_client):
    _, client, _ = app_client
    assert client.delete("/models/active").status_code == 404


# ---------------------------------------------------------------------------
# Integration: subsequent POST /jobs picks up the uploaded model
# ---------------------------------------------------------------------------

def test_new_job_picks_up_uploaded_model(app_client):
    """After upload, Job.config.model_used should become True on the next create."""
    from autocut_api.models import Upload
    app, client, _ = app_client

    client.post("/models", json=_valid_model_dict(n_features=4))

    with app.state.session_factory() as session:
        dummy = app.state.config.data_dir / "dummy.mp4"
        dummy.parent.mkdir(parents=True, exist_ok=True)
        dummy.write_bytes(b"\0")
        up = Upload(
            filename="dummy.mp4", total_size=1,
            chunk_size=app.state.config.upload_chunk_size, total_chunks=1,
            status="complete", final_path=str(dummy),
        )
        session.add(up)
        session.commit()
        uid = up.id

    # The real pipeline will fail on the dummy clip; we only inspect what the
    # job creation handler persists into config_json.
    body = client.post("/jobs", json={"upload_ids": [uid]}).json()
    assert body["config"]["model_used"] is True
    assert body["config"]["model_path"].endswith("active_model.json")


def test_new_job_without_uploaded_model_has_no_model_used(app_client):
    from autocut_api.models import Upload
    app, client, _ = app_client

    with app.state.session_factory() as session:
        dummy = app.state.config.data_dir / "dummy.mp4"
        dummy.parent.mkdir(parents=True, exist_ok=True)
        dummy.write_bytes(b"\0")
        up = Upload(
            filename="dummy.mp4", total_size=1,
            chunk_size=app.state.config.upload_chunk_size, total_chunks=1,
            status="complete", final_path=str(dummy),
        )
        session.add(up)
        session.commit()
        uid = up.id

    body = client.post("/jobs", json={"upload_ids": [uid]}).json()
    assert body["config"].get("model_used") in (None, False)


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------

def test_openapi_includes_model_routes(app_client):
    _, client, _ = app_client
    schema = client.get("/openapi.json").json()
    assert "/models" in schema["paths"]
    assert "/models/active" in schema["paths"]
