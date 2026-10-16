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

from autocut.captions import write_cut_captions
from autocut.config import load_config
from autocut.edl import load_edl
from autocut.pipeline import (
    PipelineError,
    PipelineOptions,
    PipelineReporter,
    run_pipeline,
)
from autocut.render import RenderError, burn_subtitles, render
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
    """Reconstruct PipelineOptions from the Job's stored config_json.

    Handles the admin-set ``model_path`` specially — it's persisted as a
    string in config_json (JSON doesn't know Path) and ``model_used`` is a
    display-only flag that must be stripped before unpacking into the
    PipelineOptions dataclass.
    """
    with session_factory() as session:
        job = session.get(Job, job_id)
        raw = job.config_json if job else None
    data = json.loads(raw) if raw else {}
    model_path_str = data.pop("model_path", None)
    data.pop("model_used", None)
    options = PipelineOptions(**data)
    if model_path_str:
        options.model_path = Path(model_path_str)
    return options


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


def rerender_from_edl(
    job_id: str,
    workspace: Path,
    edl_path: Path,
    output_name: str,
    session_factory: sessionmaker,
    burn_subtitles_flag: bool = False,
) -> None:
    """Re-render ``output_name`` using the EDL on disk — no re-analysis.

    This is the caching path that makes toggling-a-segment-and-re-rendering
    fast: proxies are already extracted, segments + hook + features are in
    the EDL, so we skip ingest, VAD, transcribe, scoring entirely and jump
    straight to the ffmpeg concat.

    When ``burn_subtitles_flag`` is True, the regenerated ASS captions are
    overlaid onto the mp4 as a second pass so the output has hard-coded
    subtitles. When False, the base mp4 stays clean and captions remain
    side files for NLE import.
    """
    try:
        update_job(session_factory, job_id,
                   status="running", stage="rendering", progress=0.9)

        cfg = load_config()
        with session_factory() as session:
            job = session.get(Job, job_id)
            opts = json.loads(job.config_json) if (job and job.config_json) else {}
        if opts.get("zoom"):
            cfg.zoom.enabled = True

        edl = load_edl(edl_path)
        render(
            proxies=edl.clips,
            segments=edl.segments,
            output_dir=workspace,
            cfg=cfg.render,
            output_name=output_name,
            hook=edl.hook,
            zoom=cfg.zoom,
        )
        # Regenerate captions so they reflect both segment keep/cut changes
        # AND any word-text corrections the user made via PATCH .../words/N.
        # Stem must match the mp4 filename so the SRT/ASS lands next to it.
        stem = output_name.removesuffix(".mp4")
        captions_written: dict[str, Path] = {}
        if any(t.words for t in edl.transcripts):
            clip_order = {c.clip_id: i for i, c in enumerate(edl.clips)}
            captions_written = write_cut_captions(
                edl, cfg.captions, workspace,
                stem=stem, clip_order=clip_order,
            )
        # Second pass: burn ASS onto the mp4 in place when requested. We write
        # to a temp name first so a failed burn doesn't leave the clean mp4
        # replaced by a half-written file.
        if burn_subtitles_flag and "ass" in captions_written:
            final_mp4 = workspace / output_name
            tmp_mp4 = workspace / f"{stem}.subs.tmp.mp4"
            try:
                burn_subtitles(final_mp4, captions_written["ass"], tmp_mp4)
                tmp_mp4.replace(final_mp4)
            finally:
                tmp_mp4.unlink(missing_ok=True)
        update_job(session_factory, job_id,
                   status="done", stage="complete", progress=1.0)
    except RenderError as e:
        update_job(session_factory, job_id,
                   status="failed", stage="error", error=f"render failed: {e}")
    except Exception as e:   # noqa: BLE001
        log.exception("re-render crashed for job %s", job_id)
        update_job(session_factory, job_id,
                   status="failed", stage="error",
                   error=f"{type(e).__name__}: {e}")
