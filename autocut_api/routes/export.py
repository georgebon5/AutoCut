"""Timeline-export endpoints: hand off the cut to Final Cut / Resolve / Premiere.

For now only FCPXML is supported — it's the lingua franca that every major
NLE (FCP X, DaVinci Resolve, Premiere, iMovie, Motion) imports natively.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from autocut.edl import load_edl
from autocut.fcpxml import edl_to_fcpxml
from autocut_api.dependencies import get_db
from autocut_api.routes.edl import get_done_job, resolve_edl

router = APIRouter(prefix="/jobs", tags=["export"])


@router.get("/{job_id}/export/fcpxml")
def export_fcpxml(
    job_id: str,
    preset: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> Response:
    job = get_done_job(session, job_id)
    preset_label, path = resolve_edl(job, preset)
    edl = load_edl(path)

    xml = edl_to_fcpxml(
        edl,
        sequence_name=f"autocut_{preset_label}",
        event_name=f"AutoCut · {job.id[:8]}",
    )
    filename = f"{job.id[:8]}_{preset_label}.fcpxml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
