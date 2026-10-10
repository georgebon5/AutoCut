"""User-override log endpoint.

Append-only history of PATCH flips. Feeds the Phase 5 training dataset:
each row carries the segment features captured at override time plus the
previous and new decisions, so later analysis can learn where the heuristic
scorer disagrees with the editor.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from autocut_api.dependencies import get_db
from autocut_api.models import Job, SegmentOverride
from autocut_api.schemas import OverrideRecord, OverridesListResponse

router = APIRouter(prefix="/jobs", tags=["overrides"])


def _to_record(row: SegmentOverride) -> OverrideRecord:
    return OverrideRecord(
        id=row.id,
        job_id=row.job_id,
        preset=row.preset,
        segment_index=row.segment_index,
        clip_id=row.clip_id,
        segment_start=row.segment_start,
        segment_end=row.segment_end,
        previous_decision=row.previous_decision,
        new_decision=row.new_decision,
        interest_score=row.interest_score,
        features=json.loads(row.features_json) if row.features_json else {},
        created_at=row.created_at,
    )


@router.get("/{job_id}/overrides", response_model=OverridesListResponse)
def list_overrides(
    job_id: str,
    session: Session = Depends(get_db),
) -> OverridesListResponse:
    """Return every override logged for this job, oldest first."""
    if session.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    rows = session.scalars(
        select(SegmentOverride)
        .where(SegmentOverride.job_id == job_id)
        .order_by(SegmentOverride.created_at, SegmentOverride.id)
    ).all()
    return OverridesListResponse(overrides=[_to_record(r) for r in rows])
