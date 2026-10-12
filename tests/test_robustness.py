"""Regression tests for the three real-world bugs the first-run audit found:

1. DB schema drift when a column is added after an older DB exists.
2. Orphaned upload chunk directories growing unbounded.
3. Corrupt / unloadable trained-model file crashing the whole pipeline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, String, Table, inspect, text

from autocut.pipeline import NullReporter, PipelineOptions, run_pipeline
from autocut_api.config import ApiConfig
from autocut_api.database import Base, make_engine, make_session_factory, reconcile_schema
from autocut_api.main import create_app
from autocut_api.models import Job, Upload
from autocut_api.routes.uploads import cleanup_orphan_uploads


# ---------------------------------------------------------------------------
# 1. reconcile_schema — recovers from a stale DB
# ---------------------------------------------------------------------------

def test_reconcile_schema_adds_missing_nullable_column(tmp_path):
    """Simulate an older DB where ``config_json`` doesn't exist yet.

    reconcile_schema should ALTER TABLE and add it without destroying data.
    """
    db_url = f"sqlite:///{tmp_path / 'stale.db'}"
    engine = make_engine(db_url)

    # Hand-craft a jobs table missing the config_json column.
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE jobs (
                id VARCHAR(36) PRIMARY KEY,
                status VARCHAR(16) NOT NULL,
                stage VARCHAR(32) NOT NULL,
                progress FLOAT NOT NULL,
                error TEXT,
                workspace VARCHAR(512),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
        conn.execute(text(
            "INSERT INTO jobs VALUES ('legacy-id', 'done', 'complete', 1.0, "
            "NULL, NULL, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        ))

    reconcile_schema(engine, Base)

    cols = {c["name"] for c in inspect(engine).get_columns("jobs")}
    assert "config_json" in cols

    # The legacy row survived the migration intact.
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id, status FROM jobs")).first()
        assert row[0] == "legacy-id"
        assert row[1] == "done"


def test_reconcile_schema_noop_when_up_to_date(tmp_path):
    """Running on a fresh DB should produce no ALTERs and no errors."""
    db_url = f"sqlite:///{tmp_path / 'fresh.db'}"
    engine = make_engine(db_url)
    Base.metadata.create_all(engine)
    # Second call must be idempotent.
    reconcile_schema(engine, Base)
    reconcile_schema(engine, Base)


def test_app_boots_against_stale_db(tmp_path):
    """End-to-end: an existing DB missing config_json shouldn't crash create_app.

    Before the fix, this exact scenario is what blew up the user's first run.
    """
    db_url = f"sqlite:///{tmp_path / 'stale.db'}"
    engine = make_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE jobs (
                id VARCHAR(36) PRIMARY KEY,
                status VARCHAR(16) NOT NULL,
                stage VARCHAR(32) NOT NULL,
                progress FLOAT NOT NULL,
                error TEXT,
                workspace VARCHAR(512),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """))
    engine.dispose()

    cfg = ApiConfig(data_dir=tmp_path / "data", database_url=db_url)
    app = create_app(cfg)
    with TestClient(app) as client:
        # Health is enough; create_app would have raised before this line
        # if reconcile failed.
        assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------
# 2. cleanup_orphan_uploads — remove abandoned chunks + rows
# ---------------------------------------------------------------------------

def _cfg(tmp_path):
    return ApiConfig(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        upload_chunk_size=1024,
    )


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_cleanup_removes_abandoned_upload(tmp_path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    with TestClient(app):
        with app.state.session_factory() as session:
            up = Upload(
                filename="abandoned.mp4", total_size=100,
                chunk_size=1024, total_chunks=1,
                status="uploading", final_path=None,
            )
            session.add(up)
            session.commit()
            session.refresh(up)
            uid = up.id

        chunks_dir = cfg.uploads_dir / uid / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        (chunks_dir / "0000.bin").write_bytes(b"x" * 100)

        # Age the row past the TTL.
        with app.state.session_factory() as session:
            up = session.get(Upload, uid)
            up.updated_at = _naive_utc_now() - timedelta(hours=3)
            session.commit()

        removed = cleanup_orphan_uploads(
            cfg, app.state.session_factory, ttl=timedelta(hours=2),
        )
    assert removed == 1
    assert not chunks_dir.exists()

    # Row gone from DB.
    with app.state.session_factory() as session:
        assert session.get(Upload, uid) is None


def test_cleanup_leaves_fresh_upload_alone(tmp_path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    with TestClient(app):
        with app.state.session_factory() as session:
            up = Upload(
                filename="recent.mp4", total_size=100,
                chunk_size=1024, total_chunks=1,
                status="uploading", final_path=None,
            )
            session.add(up)
            session.commit()
            session.refresh(up)
            uid = up.id
        removed = cleanup_orphan_uploads(
            cfg, app.state.session_factory, ttl=timedelta(hours=2),
        )
    assert removed == 0
    with app.state.session_factory() as session:
        assert session.get(Upload, uid) is not None


def test_cleanup_leaves_completed_upload_alone(tmp_path):
    """Even if a completed upload is ancient, it still carries the user's clip —
    we only garbage-collect the ``pending``/``uploading`` ones."""
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    with TestClient(app):
        with app.state.session_factory() as session:
            up = Upload(
                filename="done.mp4", total_size=100,
                chunk_size=1024, total_chunks=1,
                status="complete",
                final_path=str(tmp_path / "done.mp4"),
                updated_at=_naive_utc_now() - timedelta(days=30),
            )
            session.add(up)
            session.commit()
            session.refresh(up)
            uid = up.id
        removed = cleanup_orphan_uploads(
            cfg, app.state.session_factory, ttl=timedelta(hours=2),
        )
    assert removed == 0
    with app.state.session_factory() as session:
        assert session.get(Upload, uid) is not None


# ---------------------------------------------------------------------------
# 3. Pipeline tolerates a corrupt / unloadable model file
# ---------------------------------------------------------------------------

class _CaptureReporter:
    def __init__(self):
        self.messages: list[str] = []
    def stage(self, name, progress): pass
    def info(self, message): self.messages.append(message)


def test_pipeline_falls_back_to_heuristic_on_corrupt_model(tmp_path, monkeypatch):
    """If model_path points at a corrupt file, the pipeline logs it and uses
    the heuristic scorer instead of crashing the whole job."""
    import torch
    from autocut.models import Transcript

    # Monkey-patch the slow bits — only interested in the model-loading branch.
    monkeypatch.setattr("autocut.pipeline.load_vad_model",
                        lambda: (object(), lambda *a, **kw: []))
    monkeypatch.setattr("autocut.pipeline.load_whisper_model",
                        lambda cfg: object())
    monkeypatch.setattr("autocut.pipeline.transcribe_clip",
                        lambda *a, **kw: Transcript(clip_id="c", language="el", words=[]))
    monkeypatch.setattr("autocut.pipeline.enrich_segments",
                        lambda *a, **kw: None)
    monkeypatch.setattr("autocut.pipeline.enrich_segments_motion",
                        lambda *a, **kw: None)

    import subprocess
    clip = tmp_path / "clip.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(clip),
    ], capture_output=True, check=True)

    corrupt = tmp_path / "corrupt_model.json"
    corrupt.write_text("{this is not valid json for a model")

    from autocut.config import load_config
    cfg = load_config()
    reporter = _CaptureReporter()
    run_pipeline(
        [clip], tmp_path / "out", cfg,
        PipelineOptions(model_path=corrupt, captions=False),
        reporter=reporter,
    )
    assert any("unreadable" in m for m in reporter.messages), reporter.messages
    assert (tmp_path / "out" / "rough_cut.mp4").exists()
