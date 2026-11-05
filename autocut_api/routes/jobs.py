"""Job endpoints: create a pipeline run and poll its progress.

POST /jobs validates all referenced uploads are complete, validates the
chosen pipeline options (preset/hook/pacing/zoom), creates a workspace
directory, persists the options as JSON on the Job row, and submits the
pipeline function (resolved from ``app.state``) to the thread pool.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete as sa_delete, desc, select
from sqlalchemy.orm import Session

from autocut.pipeline import PipelineError, PipelineOptions, validate_options
from autocut_api.config import ApiConfig
from autocut_api.dependencies import get_config, get_db
from autocut_api.models import Job, SegmentOverride, Upload
from autocut.edl import load_edl
from autocut_api.schemas import JobCloneRequest, JobCreateRequest, JobPatchRequest, JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

# Only these PipelineOptions fields are exposed to the API — the rest
# (transcribe, fillers, takes, captions, features) stay at the defaults
# because the web UI always runs the full pipeline.
_API_OPTION_KEYS = (
    "preset", "hook", "pacing", "zoom",
    "audio_enhance", "audio_denoise", "output_aspect",
    "semantic_match",
    "transition_type",
    "color_grading",
    "padding_ms",
    "merge_gap_ms",
    "min_segment_ms",
    "order_by_time",
    # music_path is a Path, handled specially in _options_to_json like voiceover_audio_path.
)


def _options_from_request(body: JobCreateRequest) -> PipelineOptions:
    return PipelineOptions(
        preset=body.preset or "none",
        hook=body.hook,
        pacing=body.pacing,
        zoom=body.zoom,
        audio_enhance=body.audio_enhance,
        audio_denoise=body.audio_denoise,
        output_aspect=body.output_aspect,
        semantic_match=body.semantic_match,
        transition_type=body.transition or "none",
        color_grading=body.color_grading or "none",
        padding_ms=body.padding_ms,
        merge_gap_ms=body.merge_gap_ms,
        min_segment_ms=body.min_segment_ms,
        order_by_time=body.order_by_time,
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
    if opts.music_path is not None:
        data["music_path"] = str(opts.music_path)
    return json.dumps(data)


def _add_webhook_url(config_json: str, webhook_url: str | None) -> str:
    """Inject webhook_url into an already-serialised config_json string."""
    if not webhook_url:
        return config_json
    data = json.loads(config_json)
    data["webhook_url"] = webhook_url
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
        label=job.label,
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
    # Music upload resolution — same lifecycle checks as voiceover.
    if body.music_upload_id:
        mu = session.get(Upload, body.music_upload_id)
        if mu is None:
            raise HTTPException(
                status_code=400,
                detail=f"unknown music upload {body.music_upload_id}",
            )
        if mu.status != "complete" or mu.final_path is None:
            raise HTTPException(
                status_code=400,
                detail=f"music upload {body.music_upload_id} not complete",
            )
        options.music_path = Path(mu.final_path)

    try:
        validate_options(options)
    except PipelineError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    job = Job(config_json=_add_webhook_url(_options_to_json(options), body.webhook_url))
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


@router.post("/{job_id}/clone", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def clone_job(
    job_id: str,
    body: JobCloneRequest,
    request: Request,
    cfg: ApiConfig = Depends(get_config),
    session: Session = Depends(get_db),
) -> JobResponse:
    """Re-run the same clips with (optionally) different settings.

    Clip paths are recovered from the original job's EDL so no re-upload is
    needed. Voiceover and music paths are inherited from the original config
    when the files still exist on disk.
    """
    original = session.get(Job, job_id)
    if original is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not original.workspace:
        raise HTTPException(status_code=409, detail="original job has no workspace")

    orig_cfg: dict = json.loads(original.config_json) if original.config_json else {}
    workspace = Path(original.workspace)

    # Find an EDL to extract the original clip paths from.
    orig_preset = orig_cfg.get("preset", "none")
    candidates = ["edl.json"] + [
        f"edl_{p}.json" for p in (orig_preset, "tight", "medium", "loose") if p != "none"
    ]
    edl_path = next((workspace / f for f in candidates if (workspace / f).exists()), None)
    if edl_path is None:
        raise HTTPException(
            status_code=409,
            detail="original job has no EDL — cannot recover clip paths for clone",
        )

    edl = load_edl(edl_path)
    clip_paths = [Path(c.original_path) for c in edl.clips]
    missing = [str(p) for p in clip_paths if not p.exists()]
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"clip(s) no longer on disk: {', '.join(missing)}",
        )

    # Build options: inherit original, apply body overrides.
    new_preset = body.preset or orig_preset
    opts = PipelineOptions(
        preset=new_preset,
        hook=orig_cfg.get("hook", False),
        pacing=orig_cfg.get("pacing", False),
        zoom=orig_cfg.get("zoom", False),
        audio_enhance=orig_cfg.get("audio_enhance", False),
        audio_denoise=orig_cfg.get("audio_denoise", False),
        output_aspect=orig_cfg.get("output_aspect", "source"),
        semantic_match=orig_cfg.get("semantic_match", False),
        transition_type=orig_cfg.get("transition_type", "none"),
        color_grading=orig_cfg.get("color_grading", "none"),
        padding_ms=orig_cfg.get("padding_ms", 0),
        merge_gap_ms=orig_cfg.get("merge_gap_ms", 0),
        min_segment_ms=orig_cfg.get("min_segment_ms", 0),
        order_by_time=orig_cfg.get("order_by_time", False),
    )
    if orig_cfg.get("voiceover_audio_path"):
        vo = Path(orig_cfg["voiceover_audio_path"])
        if vo.exists():
            opts.voiceover_audio_path = vo
    if orig_cfg.get("music_path"):
        mu = Path(orig_cfg["music_path"])
        if mu.exists():
            opts.music_path = mu
    resolved = cfg.resolved_model_path
    if resolved.exists():
        opts.model_path = resolved

    new_job = Job(
        config_json=_options_to_json(opts),
        label=body.label or (
            f"Clone — {original.label}" if original.label else None
        ),
    )
    session.add(new_job)
    session.flush()

    new_workspace = cfg.jobs_dir / new_job.id
    new_workspace.mkdir(parents=True, exist_ok=True)
    new_job.workspace = str(new_workspace)
    session.commit()
    session.refresh(new_job)

    session_factory = request.app.state.session_factory
    pipeline_fn = request.app.state.pipeline_fn
    runner = request.app.state.runner
    new_job_id = new_job.id
    runner.submit(
        lambda: pipeline_fn(new_job_id, new_workspace, clip_paths, session_factory)
    )
    return _job_response(new_job)


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


@router.patch("/{job_id}", response_model=JobResponse)
def patch_job(
    job_id: str,
    body: JobPatchRequest,
    session: Session = Depends(get_db),
) -> JobResponse:
    """Update mutable user-facing fields (currently: label)."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if body.label is not None:
        stripped = body.label.strip()
        job.label = stripped if stripped else None
    session.commit()
    session.refresh(job)
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


_TERMINAL = frozenset({"done", "failed"})
_SSE_POLL_INTERVAL = 0.35   # seconds between DB reads
_SSE_KEEPALIVE_EVERY = 15   # send a comment every N seconds to prevent proxy timeouts


@router.get("/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    """Server-Sent Events stream for a single job.

    Sends a ``data`` event whenever stage/progress/status changes and a named
    ``done`` event when the job reaches a terminal state. A comment keepalive
    is sent every 15 s so proxies don't time out the idle connection.
    """
    factory = request.app.state.session_factory

    async def generate():
        prev = ("", -1.0, "")
        ticks_since_keepalive = 0

        while True:
            if await request.is_disconnected():
                return

            with factory() as s:
                job = s.get(Job, job_id)

            if job is None:
                yield f"data: {json.dumps({'error': 'not found'})}\n\n"
                return

            current = (job.stage, round(job.progress, 4), job.status)
            if current != prev:
                prev = current
                payload = json.dumps({
                    "stage": job.stage,
                    "progress": job.progress,
                    "status": job.status,
                    "error": job.error,
                })
                yield f"data: {payload}\n\n"
                ticks_since_keepalive = 0

            if job.status in _TERMINAL:
                yield f"event: done\ndata: {{}}\n\n"
                return

            ticks_since_keepalive += 1
            if ticks_since_keepalive >= _SSE_KEEPALIVE_EVERY / _SSE_POLL_INTERVAL:
                yield ": keepalive\n\n"
                ticks_since_keepalive = 0

            await asyncio.sleep(_SSE_POLL_INTERVAL)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",          # prevents nginx from buffering SSE
            "Connection": "keep-alive",
        },
    )
