"""Job state helpers + the pluggable pipeline function used by the runner.

``update_job`` opens a fresh session per call because the caller runs in a
background thread (SQLAlchemy sessions are not thread-safe).

``run_stub_pipeline`` is a placeholder that steps a Job through fake stages
so the HTTP progress feed can be built against a real-looking status stream.
Task 23 will replace it with the actual autocut pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from sqlalchemy.orm import sessionmaker

from autocut_api.models import Job

# Signature every pipeline function must satisfy. Tests inject their own.
PipelineFn = Callable[[str, Path, Sequence[Path], sessionmaker], None]


def update_job(
    session_factory: sessionmaker,
    job_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: float | None = None,
    error: str | None = None,
) -> None:
    """Atomic field update on a Job row. No-op if the job was deleted."""
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        if status is not None:
            job.status = status
        if stage is not None:
            job.stage = stage
        if progress is not None:
            job.progress = progress
        if error is not None:
            job.error = error
        session.commit()


def run_stub_pipeline(
    job_id: str,
    workspace: Path,
    clip_paths: Sequence[Path],
    session_factory: sessionmaker,
) -> None:
    """Fake pipeline that walks through stages matching the real one.

    Will be swapped for the autocut pipeline in Task 23. Kept here so the API
    (and future UI) can be built and tested against a real progress feed.
    """
    try:
        update_job(session_factory, job_id,
                   status="running", stage="normalizing", progress=0.15)
        update_job(session_factory, job_id, stage="transcribing", progress=0.40)
        update_job(session_factory, job_id, stage="scoring", progress=0.75)
        update_job(session_factory, job_id, stage="rendering", progress=0.95)
        update_job(session_factory, job_id,
                   status="done", stage="complete", progress=1.0)
    except Exception as e:    # noqa: BLE001 — report any failure to the client
        update_job(session_factory, job_id,
                   status="failed", stage="error", error=str(e))
