"""Re-render endpoint: trigger a render-only pass after user overrides.

Caching contract (Phase 4 acceptance):
    Toggling a segment's keep/cut and POSTing /jobs/{id}/render must NOT
    re-run analysis. We reload the EDL (which carries the user's overrides),
    reuse the proxies already on disk, and jump straight to ffmpeg concat.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from autocut_api.dependencies import get_db
from autocut_api.pipeline_bridge import rerender_from_edl
from autocut_api.routes.edl import edl_filename, get_done_job, resolve_edl_path
from autocut_api.routes.jobs import _job_response
from autocut_api.schemas import JobResponse

router = APIRouter(prefix="/jobs", tags=["render"])


def _output_name(preset: str) -> str:
    return "rough_cut.mp4" if preset == "none" else f"rough_cut_{preset}.mp4"


def _resolve_preset(job, preset_query: str | None) -> str:
    """Mirror the preset-selection logic of the EDL route."""
    if preset_query is not None:
        return preset_query
    cfg = json.loads(job.config_json) if job.config_json else {}
    job_preset = cfg.get("preset", "none")
    if job_preset == "all":
        raise HTTPException(
            status_code=400,
            detail="job used preset=all; specify ?preset=tight|medium|loose",
        )
    return job_preset


@router.post("/{job_id}/render", response_model=JobResponse)
def post_render(
    job_id: str,
    request: Request,
    preset: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> JobResponse:
    job = get_done_job(session, job_id)
    chosen = _resolve_preset(job, preset)

    workspace = Path(job.workspace)
    edl_path = workspace / edl_filename(chosen)
    if not edl_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"EDL {edl_path.name} not found in workspace",
        )

    output_name = _output_name(chosen)

    runner = request.app.state.runner
    session_factory = request.app.state.session_factory
    runner.submit(lambda: rerender_from_edl(
        job_id, workspace, edl_path, output_name, session_factory,
    ))

    # Return the current (freshly re-fetched) job snapshot so the client sees
    # an in-flight state immediately.
    session.refresh(job)
    return _job_response(job)
