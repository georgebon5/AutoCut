"""Bridge the autocut pipeline into the API job runner.

Provides ``run_api_pipeline`` matching the PipelineFn signature expected by
``create_app``. The pipeline's stage reporter is translated into Job row
updates, so a client polling ``GET /jobs/{id}`` sees live progress.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Sequence

from sqlalchemy.orm import sessionmaker

from autocut.config import load_config
from autocut.pipeline import (
    PipelineError,
    PipelineOptions,
    PipelineReporter,
    run_pipeline,
)
from autocut_api.jobs import update_job
from autocut_api.models import Job

log = logging.getLogger("autocut_api.pipeline_bridge")


class JobReporter:
    """Pipeline reporter that mirrors stage/progress onto a Job row."""

    def __init__(self, session_factory: sessionmaker, job_id: str) -> None:
        self._factory = session_factory
        self._job_id = job_id

    def stage(self, name: str, progress: float) -> None:
        update_job(
            self._factory, self._job_id,
            status="running", stage=name, progress=progress,
        )

    def info(self, message: str) -> None:
        # Per-item details aren't persisted yet — surface them in the server
        # log so operators can tail progress.
        log.info("job %s: %s", self._job_id, message)


def _load_options(session_factory: sessionmaker, job_id: str) -> PipelineOptions:
    """Reconstruct PipelineOptions from the Job's stored config_json."""
    with session_factory() as session:
        job = session.get(Job, job_id)
        raw = job.config_json if job else None
    data = json.loads(raw) if raw else {}
    return PipelineOptions(**data)


def run_api_pipeline(
    job_id: str,
    workspace: Path,
    clip_paths: Sequence[Path],
    session_factory: sessionmaker,
) -> None:
    """Execute the real autocut pipeline, updating the Job row along the way.

    Pipeline options are read from the Job's ``config_json`` column, which was
    populated at job creation time from the HTTP request.
    """
    reporter = JobReporter(session_factory, job_id)
    try:
        cfg = load_config()
        options = _load_options(session_factory, job_id)
        run_pipeline(list(clip_paths), workspace, cfg, options, reporter=reporter)
        update_job(
            session_factory, job_id,
            status="done", stage="complete", progress=1.0,
        )
    except PipelineError as e:
        update_job(
            session_factory, job_id,
            status="failed", stage="error", error=str(e),
        )
    except Exception as e:   # noqa: BLE001 — surface any crash to the client
        log.exception("pipeline crashed for job %s", job_id)
        update_job(
            session_factory, job_id,
            status="failed", stage="error", error=f"{type(e).__name__}: {e}",
        )
