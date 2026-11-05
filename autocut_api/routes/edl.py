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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autocut.edl import edl_to_dict, load_edl, save_edl
from autocut_api.dependencies import get_db
from autocut_api.models import Job, SegmentOverride
from autocut_api.schemas import (
    BatchSegmentUpdate,
    SegmentResponse,
    SegmentUpdate,
    WordResponse,
    WordUpdate,
)

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


_MIN_SEGMENT_DURATION = 0.05  # seconds — guard against zero-width trim


@router.patch("/{job_id}/segments/{index}", response_model=SegmentResponse)
def patch_segment(
    request: Request,
    job_id: str,
    index: int,
    update: SegmentUpdate,
    preset: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> SegmentResponse:
    """Override a segment's keep/cut decision and/or its start/end boundaries."""
    has_decision = update.decision is not None
    has_trim = update.start is not None or update.end is not None
    if not has_decision and not has_trim:
        raise HTTPException(status_code=422, detail="must provide decision, start, or end")
    if has_decision and update.decision not in _VALID_DECISIONS:
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

    # ── Decision change ───────────────────────────────────────────────────────
    if has_decision:
        previous_decision = seg.decision
        # Snapshot the override BEFORE mutating — the row is append-only
        # training data; the first row for a (job, preset, segment) carries
        # the pipeline's original auto_decision as its previous_decision.
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

    # ── Boundary trim ─────────────────────────────────────────────────────────
    if has_trim:
        # Find the clip to know its duration boundary.
        clip = next((c for c in edl.clips if c.clip_id == seg.clip_id), None)
        clip_dur = clip.duration if clip else float("inf")
        new_start = seg.start if update.start is None else float(update.start)
        new_end   = seg.end   if update.end   is None else float(update.end)
        # Clamp: stay within clip, maintain minimum duration, start < end.
        new_start = max(0.0, new_start)
        new_end   = min(clip_dur, new_end)
        if new_end - new_start < _MIN_SEGMENT_DURATION:
            if update.start is not None:
                new_start = new_end - _MIN_SEGMENT_DURATION
            else:
                new_end = new_start + _MIN_SEGMENT_DURATION
        new_start = max(0.0, new_start)
        new_end   = min(clip_dur, new_end)
        seg.start = new_start
        seg.end   = new_end

    save_edl(edl, path)
    session.commit()

    # Trigger background retrain only when a decision changed (trims are not
    # labelled examples for the classifier).
    if has_decision:
        override_count = session.scalar(
            select(func.count()).select_from(SegmentOverride)
        ) or 0
        cfg = getattr(request.app.state, "config", None)
        session_factory = getattr(request.app.state, "session_factory", None)
        if cfg is not None and session_factory is not None:
            from autocut_api.routes.train import maybe_auto_train
            maybe_auto_train(override_count, session_factory, cfg)

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


@router.patch("/{job_id}/segments", response_model=list[SegmentResponse])
def batch_patch_segments(
    request: Request,
    job_id: str,
    update: BatchSegmentUpdate,
    preset: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> list[SegmentResponse]:
    """Apply multiple keep/cut overrides atomically (Keep All / Cut All / Invert).

    Loads the EDL once, applies every update, and saves once — far cheaper
    than N individual PATCHes. Logs one SegmentOverride per changed segment.
    """
    for item in update.updates:
        if item.decision not in _VALID_DECISIONS:
            raise HTTPException(
                status_code=422,
                detail=f"decision must be one of {list(_VALID_DECISIONS)}",
            )

    job = get_done_job(session, job_id)
    preset_label, path = resolve_edl(job, preset)
    edl = load_edl(path)

    results: list[SegmentResponse] = []
    for item in update.updates:
        if item.index < 0 or item.index >= len(edl.segments):
            raise HTTPException(
                status_code=400,
                detail=f"segment index {item.index} out of range [0, {len(edl.segments)})",
            )
        seg = edl.segments[item.index]

        session.add(SegmentOverride(
            job_id=job.id,
            preset=preset_label,
            segment_index=item.index,
            clip_id=seg.clip_id,
            segment_start=seg.start,
            segment_end=seg.end,
            previous_decision=seg.decision,
            new_decision=item.decision,
            interest_score=seg.interest_score,
            features_json=json.dumps(seg.features),
        ))

        seg.decision = item.decision   # type: ignore[assignment]
        seg.decision_source = "user"
        seg.reasons.append(f"user {item.decision}")

        results.append(SegmentResponse(
            index=item.index,
            clip_id=seg.clip_id,
            start=seg.start,
            end=seg.end,
            duration=seg.duration,
            decision=seg.decision,
            decision_source=seg.decision_source,
            interest_score=seg.interest_score,
            reasons=seg.reasons,
            features=seg.features,
        ))

    save_edl(edl, path)
    session.commit()

    override_count = session.scalar(
        select(func.count()).select_from(SegmentOverride)
    ) or 0
    cfg = getattr(request.app.state, "config", None)
    session_factory = getattr(request.app.state, "session_factory", None)
    if cfg is not None and session_factory is not None:
        from autocut_api.routes.train import maybe_auto_train
        maybe_auto_train(override_count, session_factory, cfg)

    return results


# Keep caption lines sane — mis-paste of a whole paragraph into one word
# would blow up the SRT layout. 200 chars is well past any real correction.
_MAX_WORD_LEN = 200


@router.patch(
    "/{job_id}/transcripts/{clip_id}/words/{word_index}",
    response_model=WordResponse,
)
def patch_word(
    job_id: str,
    clip_id: str,
    word_index: int,
    update: WordUpdate,
    preset: str | None = Query(default=None),
    session: Session = Depends(get_db),
) -> WordResponse:
    """Correct a mis-transcribed word. Captions on next re-render reflect it."""
    new_text = update.word.strip()
    if not new_text:
        raise HTTPException(status_code=422, detail="word must not be empty")
    if len(new_text) > _MAX_WORD_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"word too long (max {_MAX_WORD_LEN} chars)",
        )

    job = get_done_job(session, job_id)
    _, path = resolve_edl(job, preset)
    edl = load_edl(path)

    transcript = next((t for t in edl.transcripts if t.clip_id == clip_id), None)
    if transcript is None:
        raise HTTPException(
            status_code=404,
            detail=f"clip {clip_id} has no transcript",
        )
    if word_index < 0 or word_index >= len(transcript.words):
        raise HTTPException(
            status_code=400,
            detail=f"word index {word_index} out of range [0, {len(transcript.words)})",
        )

    w = transcript.words[word_index]
    w.word = new_text
    save_edl(edl, path)

    return WordResponse(
        clip_id=clip_id,
        word_index=word_index,
        word=new_text,
        start=w.start,
        end=w.end,
    )
