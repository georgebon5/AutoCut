"""Job endpoints: create a pipeline run and poll its progress.

POST /jobs validates all referenced uploads are complete, creates a workspace
directory, and submits the pipeline function (resolved from ``app.state``) to
the thread pool. GET /jobs and GET /jobs/{id} are the status-polling surface
the UI uses to track progress.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from autocut_api.config import ApiConfig
from autocut_api.dependencies import get_config, get_db
from autocut_api.models import Job, Upload
from autocut_api.schemas import JobCreateRequest, JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


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

    job = Job()
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

    return JobResponse.model_validate(job)


@router.get("", response_model=list[JobResponse])
def list_jobs(session: Session = Depends(get_db)) -> list[JobResponse]:
    jobs = session.scalars(
        select(Job).order_by(desc(Job.created_at)).limit(100)
    ).all()
    return [JobResponse.model_validate(j) for j in jobs]


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, session: Session = Depends(get_db)) -> JobResponse:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobResponse.model_validate(job)
