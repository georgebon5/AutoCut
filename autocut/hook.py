"""Opening-hook suggestion: pick the top-scoring short moment as a teaser."""

from __future__ import annotations

from autocut.config import HookConfig
from autocut.models import Segment


def find_hook_segment(
    segments: list[Segment],
    cfg: HookConfig,
) -> Segment | None:
    """Return a new Segment representing the suggested opening hook, or None.

    Among keep segments at least ``cfg.min_duration_s`` long, picks the one
    with the highest ``interest_score``. If it exceeds ``cfg.max_duration_s``,
    the returned segment is a centre-aligned slice of that length (so we
    capture the climax rather than a dead-air opening). Returns a NEW Segment
    with ``reasons=["hook (opening)"]``; the input segments are not mutated.
    """
    candidates = [
        s for s in segments
        if s.decision == "keep" and s.duration >= cfg.min_duration_s
    ]
    if not candidates:
        return None

    best = max(candidates, key=lambda s: s.interest_score)
    if best.duration <= cfg.max_duration_s:
        hook_start, hook_end = best.start, best.end
    else:
        centre = (best.start + best.end) / 2.0
        half = cfg.max_duration_s / 2.0
        hook_start = centre - half
        hook_end = centre + half

    hook = Segment(
        clip_id=best.clip_id,
        start=hook_start,
        end=hook_end,
        decision="keep",
        decision_source="auto",
        reasons=["hook (opening)"],
    )
    hook.features = dict(best.features)
    hook.interest_score = best.interest_score
    return hook
