"""Tests for the autocut_api skeleton: health endpoint, DB bootstrap, Job model."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from autocut_api import __version__
from autocut_api.config import ApiConfig
from autocut_api.database import make_engine
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


@pytest.fixture
def app_cfg(tmp_path: Path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    return app, cfg


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

def test_health_returns_ok(app_cfg):
    app, _ = app_cfg
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__}


def test_health_version_matches_package(app_cfg):
    app, _ = app_cfg
    client = TestClient(app)
    assert client.get("/health").json()["version"] == __version__


# ---------------------------------------------------------------------------
# Data directory bootstrap
# ---------------------------------------------------------------------------

def test_data_dir_and_jobs_dir_created(tmp_path: Path):
    cfg = _cfg(tmp_path)
    assert not cfg.data_dir.exists()
    create_app(cfg)
    assert cfg.data_dir.is_dir()
    assert cfg.jobs_dir.is_dir()


def test_resolved_database_url_defaults_under_data_dir(tmp_path: Path):
    cfg = ApiConfig(data_dir=tmp_path, database_url=None)
    assert cfg.resolved_database_url == f"sqlite:///{tmp_path / 'autocut.db'}"


def test_resolved_database_url_uses_override(tmp_path: Path):
    override = f"sqlite:///{tmp_path / 'custom.db'}"
    cfg = ApiConfig(data_dir=tmp_path, database_url=override)
    assert cfg.resolved_database_url == override


# ---------------------------------------------------------------------------
# Database bootstrap
# ---------------------------------------------------------------------------

def test_jobs_table_created_on_startup(tmp_path: Path):
    cfg = _cfg(tmp_path)
    create_app(cfg)
    engine = make_engine(cfg.resolved_database_url)
    assert "jobs" in inspect(engine).get_table_names()


def test_sqlite_wal_mode_enabled(tmp_path: Path):
    cfg = _cfg(tmp_path)
    create_app(cfg)
    engine = make_engine(cfg.resolved_database_url)
    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert str(mode).lower() == "wal"


# ---------------------------------------------------------------------------
# Job model round-trip
# ---------------------------------------------------------------------------

def test_job_defaults(app_cfg):
    app, _ = app_cfg
    with app.state.session_factory() as session:
        job = Job()
        session.add(job)
        session.commit()
        session.refresh(job)
        assert job.id and len(job.id) == 36
        assert job.status == "pending"
        assert job.stage == "created"
        assert job.progress == 0.0
        assert job.error is None
        assert job.workspace is None
        assert job.created_at is not None
        assert job.updated_at is not None


def test_job_persisted_and_queryable(app_cfg):
    app, _ = app_cfg
    with app.state.session_factory() as session:
        job = Job(stage="transcribing", progress=0.42)
        session.add(job)
        session.commit()
        job_id = job.id

    with app.state.session_factory() as session:
        loaded = session.scalar(select(Job).where(Job.id == job_id))
        assert loaded is not None
        assert loaded.stage == "transcribing"
        assert loaded.progress == pytest.approx(0.42)


def test_job_updated_at_moves_on_change(app_cfg):
    app, _ = app_cfg
    with app.state.session_factory() as session:
        job = Job()
        session.add(job)
        session.commit()
        initial = job.updated_at

        job.stage = "scoring"
        session.commit()
        session.refresh(job)
        assert job.updated_at >= initial


# ---------------------------------------------------------------------------
# App wiring
# ---------------------------------------------------------------------------

def test_app_state_exposes_engine_and_factory(app_cfg):
    app, cfg = app_cfg
    assert app.state.config is cfg
    assert app.state.engine is not None
    assert app.state.session_factory is not None


def test_openapi_includes_health(app_cfg):
    app, _ = app_cfg
    client = TestClient(app)
    schema = client.get("/openapi.json").json()
    assert "/health" in schema["paths"]
