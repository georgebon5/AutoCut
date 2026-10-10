"""EDL-review endpoints: inspect decisions and override segments.

The UI uses these to render the keep/cut timeline and persist manual
overrides. Each PATCH loads the EDL file from disk, flips one segment,
sets ``decision_source="user"`` and appends a reason, then writes the
file back. Analysis is not re-run — segment timings and features are
whatever the pipeline produced.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from autocut.edl import edl_to_dict, load_edl, save_edl
from autocut_api.dependencies import get_db
from autocut_api.models import Job, SegmentOverride
from autocut_api.schemas import SegmentResponse, SegmentUpdate

router = APIRouter(prefix="/jobs", tags=["edl"])


_VALID_DECISIONS = ("keep", "cut")


def edl_filename(preset: str) -> str:
    return "edl.json" if preset == "none" else f"edl_{preset}.json"


def resolve_edl(job: Job, preset_query: str | None) -> tuple[str, Path]:
    """Pick the EDL file to operate on, favouring ``?preset=X`` over job config.

    For jobs created with preset=all, the client MUST pass ``?preset=X`` so the
    intent is explicit. Returns ``(preset_label, edl_path)``.
    """
    if not job.workspace:
        raise HTTPException(status_code=409, detail="job has no workspace")
    workspace = Path(job.workspace)

    if preset_query is not None:
        chosen = preset_query
    else:
        cfg = json.loads(job.config_json) if job.config_json else {}
        job_preset = cfg.get("preset", "none")
        if job_preset == "all":
            raise HTTPException(
                status_code=400,
                detail="job used preset=all; specify ?preset=tight|medium|loose",
            )
        chosen = job_preset

    path = workspace / edl_filename(chosen)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"EDL {path.name} not found in workspace",
        )
    return chosen, path


def resolve_edl_path(job: Job, preset_query: str | None) -> Path:
    """Backwards-compatible wrapper returning only the path."""
    return resolve_edl(job, preset_query)[1]


def get_done_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "done":
        raise HTTPException(
            status_code=409,
            detail=f"job not ready for review (status={job.status})",
        )
    return job


@router.get("/{job_id}/edl")
def get_edl(
    job_id: str,
    preset: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> dict:
    """Return the full EDL (segments, transcripts, hook) as JSON."""
    job = get_done_job(session, job_id)
    path = resolve_edl_path(job, preset)
    return edl_to_dict(load_edl(path))


@router.patch("/{job_id}/segments/{index}", response_model=SegmentResponse)
def patch_segment(
    job_id: str,
    index: int,
    update: SegmentUpdate,
    preset: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> SegmentResponse:
    """Override one segment's keep/cut decision. Marks source as 'user'."""
    if update.decision not in _VALID_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"decision must be one of {list(_VALID_DECISIONS)}",
        )

    job = get_done_job(session, job_id)
    preset_label, path = resolve_edl(job, preset)
    edl = load_edl(path)

    if index < 0 or index >= len(edl.segments):
        raise HTTPException(
            status_code=400,
            detail=f"segment index {index} out of range [0, {len(edl.segments)})",
        )

    seg = edl.segments[index]
    previous_decision = seg.decision

    # Snapshot the override BEFORE mutating state — the row is append-only
    # training data; the first row for a (job, preset, segment) carries the
    # pipeline's original auto_decision as its previous_decision.
    session.add(SegmentOverride(
        job_id=job.id,
        preset=preset_label,
        segment_index=index,
        clip_id=seg.clip_id,
        segment_start=seg.start,
        segment_end=seg.end,
        previous_decision=previous_decision,
        new_decision=update.decision,
        interest_score=seg.interest_score,
        features_json=json.dumps(seg.features),
    ))

    seg.decision = update.decision   # type: ignore[assignment]
    seg.decision_source = "user"
    seg.reasons.append(f"user {update.decision}")
    save_edl(edl, path)
    session.commit()

    return SegmentResponse(
        index=index,
        clip_id=seg.clip_id,
        start=seg.start,
        end=seg.end,
        duration=seg.duration,
        decision=seg.decision,
        decision_source=seg.decision_source,
        interest_score=seg.interest_score,
        reasons=seg.reasons,
        features=seg.features,
    )
