"""Silence padding: expand kept segments slightly on both sides.

After scoring and selection, each kept segment's boundaries are nudged
outward by ``padding_s`` seconds so the first/last word of a phrase isn't
clipped at the very edge of the cut.  Constraints enforced:

- Boundaries never go below 0 or past the clip's total duration.
- Two adjacent kept segments from the same clip never overlap after padding:
  their boundary is clamped to the midpoint of the gap between them.
"""

from __future__ import annotations

import copy

from autocut.models import ProxyInfo, Segment


def apply_padding(
    segments: list[Segment],
    proxies: list[ProxyInfo],
    padding_s: float,
) -> list[Segment]:
    """Return a deep copy of *segments* with kept-segment boundaries expanded.

    ``padding_s`` is the number of seconds to add to each side.  Pass 0 (or a
    negative value) to get an identical copy with no changes.
    """
    if padding_s <= 0:
        return copy.deepcopy(segments)

    clip_dur = {p.clip_id: p.duration for p in proxies}
    out = copy.deepcopy(segments)

    # Build per-clip index lists for kept segments only.
    by_clip: dict[str, list[int]] = {}
    for i, seg in enumerate(out):
        if seg.decision == "keep":
            by_clip.setdefault(seg.clip_id, []).append(i)

    for clip_id, indices in by_clip.items():
        dur = clip_dur.get(clip_id, float("inf"))
        # Sort by start time (should already be ordered, but be safe).
        indices.sort(key=lambda i: out[i].start)

        new_starts = [out[i].start - padding_s for i in indices]
        new_ends   = [out[i].end   + padding_s for i in indices]

        # Clamp to [0, clip_duration].
        new_starts = [max(0.0, s) for s in new_starts]
        new_ends   = [min(dur, e) for e in new_ends]

        # Resolve overlaps between consecutive kept segments: if two expanded
        # boundaries would cross, pin both to the midpoint of the original gap.
        for k in range(len(indices) - 1):
            if new_ends[k] > new_starts[k + 1]:
                mid = (out[indices[k]].end + out[indices[k + 1]].start) / 2.0
                new_ends[k]      = mid
                new_starts[k + 1] = mid

        for k, i in enumerate(indices):
            out[i].start = new_starts[k]
            out[i].end   = new_ends[k]

    return out
