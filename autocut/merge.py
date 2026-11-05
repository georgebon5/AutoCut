"""Segment gap-closing: merge adjacent kept segments with tiny gaps between them.

After scoring, selection, and padding, two kept segments from the same clip
may still have a small gap between them.  A gap of 200–400 ms creates an
audible micro-cut that sounds like a stutter — the two segments feel like
they should be one continuous piece of speech.

``merge_close_segments`` closes these gaps by extending each segment's
boundary to the midpoint of the gap.  Gaps larger than ``max_gap_s`` are
left untouched; they represent intentional silences or distinct moments that
should remain separate.

Relationship to padding (``autocut.padding``):
  - Padding expands ALL sides of every kept segment by a fixed amount.
  - Gap-closing targets only the INTERNAL boundaries between adjacent segments
    whose gap falls below a threshold, leaving unrelated boundaries alone.

Call order: apply_padding → merge_close_segments, so padding has already
extended boundaries outward before we check whether the remaining gap is
small enough to close completely.
"""

from __future__ import annotations

import copy

from autocut.models import Segment


def merge_close_segments(
    segments: list[Segment],
    max_gap_s: float,
) -> list[Segment]:
    """Return a deep copy of *segments* with small inter-segment gaps closed.

    For each pair of consecutive kept segments in the same clip whose gap is
    ≤ *max_gap_s*, both boundaries are extended to meet at the gap midpoint.
    If *max_gap_s* ≤ 0 the input is returned as a deep copy unchanged.

    Segments are assumed to be in chronological order within each clip, which
    the pipeline guarantees. Cut segments are never modified.
    """
    if max_gap_s <= 0:
        return copy.deepcopy(segments)

    out = copy.deepcopy(segments)

    # Build a per-clip list of (list-index, segment) for kept segments only,
    # sorted by start time so we look at consecutive pairs correctly.
    by_clip: dict[str, list[int]] = {}
    for i, seg in enumerate(out):
        if seg.decision == "keep":
            by_clip.setdefault(seg.clip_id, []).append(i)

    for indices in by_clip.values():
        indices.sort(key=lambda i: out[i].start)
        for k in range(len(indices) - 1):
            a = out[indices[k]]
            b = out[indices[k + 1]]
            gap = b.start - a.end
            if 0 < gap <= max_gap_s:
                mid = (a.end + b.start) / 2.0
                a.end = mid
                b.start = mid

    return out
