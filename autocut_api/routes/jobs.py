"""Job endpoints: create a pipeline run and poll its progress.

POST /jobs validates all referenced uploads are complete, validates the
chosen pipeline options (preset/hook/pacing/zoom), creates a workspace
directory, persists the options as JSON on the Job row, and submits the
pipeline function (resolved from ``app.state``) to the thread pool.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete as sa_delete, desc, select
from sqlalchemy.orm import Session

from autocut.pipeline import PipelineError, PipelineOptions, validate_options
from autocut_api.config import ApiConfig
from autocut_api.dependencies import get_config, get_db
from autocut_api.models import Job, SegmentOverride, Upload
from autocut_api.schemas import JobCreateRequest, JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

# Only these PipelineOptions fields are exposed to the API — the rest
# (transcribe, fillers, takes, captions, features) stay at the defaults
# because the web UI always runs the full pipeline.
_API_OPTION_KEYS = ("preset", "hook", "pacing", "zoom")


def _options_from_request(body: JobCreateRequest) -> PipelineOptions:
    return PipelineOptions(
        preset=body.preset or "none",
        hook=body.hook,
        pacing=body.pacing,
        zoom=body.zoom,
    )


def _options_to_json(opts: PipelineOptions) -> str:
    data: dict = {k: getattr(opts, k) for k in _API_OPTION_KEYS}
    if opts.model_path is not None:
        # JSON can't carry Path; stringify. ``model_used`` is a display-only
        # flag the UI reads to show "scored with trained model".
        data["model_path"] = str(opts.model_path)
        data["model_used"] = True
    if opts.voiceover_audio_path is not None:
        data["voiceover_audio_path"] = str(opts.voiceover_audio_path)
        data["voiceover_mode"] = True   # display flag for the review UI
    return json.dumps(data)


def _job_response(job: Job) -> JobResponse:
    config = json.loads(job.config_json) if job.config_json else None
    return JobResponse(
        id=job.id,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        error=job.error,
        workspace=job.workspace,
        config=config,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(
    body: JobCreateRequest,
    request: Request,
    cfg: ApiConfig = Depends(get_config),
    session: Session = Depends(get_db),
) -> JobResponse:
    if not body.upload_ids:
        raise HTTPException(status_code=422, detail="upload_ids must not be empty")

    clip_paths: list[Path] = []
    for uid in body.upload_ids:
        up = session.get(Upload, uid)
        if up is None:
            raise HTTPException(status_code=400, detail=f"unknown upload {uid}")
        if up.status != "complete" or up.final_path is None:
            raise HTTPException(
                status_code=400,
                detail=f"upload {uid} not complete (status={up.status})",
            )
        clip_paths.append(Path(up.final_path))

    options = _options_from_request(body)
    # Apply the admin-set trained model (if any) to every job. The model
    # may come from AUTOCUT_API_MODEL_PATH (env) or a POST /models upload
    # (persisted to data_dir/active_model.json); resolved_model_path hides
    # that distinction from the caller.
    resolved = cfg.resolved_model_path
    if resolved.exists():
        options.model_path = resolved

    # Voiceover upload resolution — same lifecycle checks as the clip
    # uploads: it must exist and be fully uploaded before we start.
    if body.voiceover_upload_id:
        vo_up = session.get(Upload, body.voiceover_upload_id)
        if vo_up is None:
            raise HTTPException(
                status_code=400,
                detail=f"unknown voiceover upload {body.voiceover_upload_id}",
            )
        if vo_up.status != "complete" or vo_up.final_path is None:
            raise HTTPException(
                status_code=400,
                detail=f"voiceover upload {body.voiceover_upload_id} not complete",
            )
        options.voiceover_audio_path = Path(vo_up.final_path)
    try:
        validate_options(options)
    except PipelineError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    job = Job(config_json=_options_to_json(options))
    session.add(job)
    session.flush()   # populate job.id without committing yet

    workspace = cfg.jobs_dir / job.id
    workspace.mkdir(parents=True, exist_ok=True)
    job.workspace = str(workspace)
    session.commit()
    session.refresh(job)

    session_factory = request.app.state.session_factory
    pipeline_fn = request.app.state.pipeline_fn
    runner = request.app.state.runner
    job_id = job.id
    runner.submit(lambda: pipeline_fn(job_id, workspace, clip_paths, session_factory))

    return _job_response(job)


@router.get("", response_model=list[JobResponse])
def list_jobs(session: Session = Depends(get_db)) -> list[JobResponse]:
    jobs = session.scalars(
        select(Job).order_by(desc(Job.created_at)).limit(100)
    ).all()
    return [_job_response(j) for j in jobs]


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, session: Session = Depends(get_db)) -> JobResponse:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_response(job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: str, session: Session = Depends(get_db)) -> Response:
    """Remove a finished job: workspace dir, Job row, and override log.

    Only ``done`` / ``failed`` jobs can be deleted — killing a live render
    half-way would leave orphan ffmpeg processes and partial output files.
    """
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status in ("pending", "running"):
        raise HTTPException(
            status_code=409,
            detail=f"cannot delete an active job (status={job.status})",
        )

    if job.workspace:
        shutil.rmtree(job.workspace, ignore_errors=True)
    session.execute(
        sa_delete(SegmentOverride).where(SegmentOverride.job_id == job_id)
    )
    session.delete(job)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
