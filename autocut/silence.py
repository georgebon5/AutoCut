"""Silence removal: gap thresholding, padding, and word-boundary snapping."""

from __future__ import annotations

from autocut.config import SilenceConfig
from autocut.models import Segment, SpeechRegion


def _merge_speech_regions(regions: list[SpeechRegion]) -> list[tuple[float, float]]:
    """Merge overlapping / adjacent speech regions into disjoint intervals."""
    if not regions:
        return []
    intervals = sorted((r.start, r.end) for r in regions)
    merged: list[list[float]] = [list(intervals[0])]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def _silence_gaps(
    speech_intervals: list[tuple[float, float]],
    duration: float,
) -> list[tuple[float, float]]:
    """Return silence gap intervals ([0,first_speech], between speeches, [last_speech,end])."""
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in speech_intervals:
        if start > cursor + 1e-6:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration - 1e-6:
        gaps.append((cursor, duration))
    return gaps


def _speech_prob_for_interval(
    regions: list[SpeechRegion],
    start: float,
    end: float,
) -> float:
    """Average speech_prob of VAD regions that overlap [start, end]."""
    overlapping = [r for r in regions if r.start < end and r.end > start]
    if not overlapping:
        return 0.0
    return sum(r.speech_prob for r in overlapping) / len(overlapping)


def _snap_to_word_boundary(
    t: float,
    word_timestamps: list[dict],
    window: float = 0.15,
) -> float:
    """Snap time t to the nearest word boundary within window seconds.

    Word boundaries are the .start and .end of every word dict.
    If nothing is within the window, t is returned unchanged.
    Implemented in Task 7 once Whisper timestamps are available.
    """
    best = t
    best_dist = float("inf")
    for word in word_timestamps:
        for boundary in (word.get("start", t), word.get("end", t)):
            dist = abs(boundary - t)
            if dist < best_dist and dist <= window:
                best_dist = dist
                best = boundary
    return best


def apply_silence_removal(
    speech_regions: list[SpeechRegion],
    clip_id: str,
    duration: float,
    cfg: SilenceConfig,
    word_timestamps: list[dict] | None = None,
) -> list[Segment]:
    """Convert VAD speech regions to a contiguous keep/cut Segment list.

    Algorithm:
    1. Merge overlapping speech regions.
    2. Identify silence gaps between them.
    3. Gaps >= min_silence are cut, shrunk by `padding` on both sides.
    4. Remaining timeline is kept.
    5. If word_timestamps are present, cut boundaries snap to word edges.

    Args:
        speech_regions: Output of autocut.vad.detect_speech.
        clip_id: Propagated into every Segment.
        duration: Total clip duration in seconds (proxy clip length).
        cfg: min_silence and padding thresholds.
        word_timestamps: Optional word dicts with 'start'/'end' keys (Task 7).

    Returns:
        Non-overlapping Segment list covering [0.0, duration], sorted by start.
    """
    if not speech_regions:
        return [Segment(
            clip_id=clip_id,
            start=0.0,
            end=duration,
            features={"speech_prob": 0.0},
            decision="keep",
            decision_source="auto",
            reasons=["no speech detected — kept as B-roll candidate"],
        )]

    speech_ivs = _merge_speech_regions(speech_regions)
    gaps = _silence_gaps(speech_ivs, duration)

    # Collect cut windows after padding (and optional word snapping)
    cut_windows: list[tuple[float, float, str]] = []
    for gap_start, gap_end in gaps:
        if (gap_end - gap_start) < cfg.min_silence:
            continue

        cut_start = gap_start + cfg.padding
        cut_end = gap_end - cfg.padding

        if word_timestamps:
            cut_start = _snap_to_word_boundary(cut_start, word_timestamps)
            cut_end = _snap_to_word_boundary(cut_end, word_timestamps)

        if cut_start >= cut_end:
            # Gap collapses after padding — skip
            continue

        cut_start = max(0.0, cut_start)
        cut_end = min(duration, cut_end)
        reason = f"silence {gap_end - gap_start:.2f}s"
        cut_windows.append((cut_start, cut_end, reason))

    # Stitch together: fill keep segments between cuts
    result: list[Segment] = []
    cursor = 0.0

    for cut_start, cut_end, reason in sorted(cut_windows):
        if cut_start > cursor + 1e-6:
            result.append(Segment(
                clip_id=clip_id,
                start=cursor,
                end=cut_start,
                features={"speech_prob": _speech_prob_for_interval(speech_regions, cursor, cut_start)},
                decision="keep",
                decision_source="auto",
                reasons=[],
            ))
        result.append(Segment(
            clip_id=clip_id,
            start=cut_start,
            end=cut_end,
            features={"speech_prob": 0.0},
            decision="cut",
            decision_source="auto",
            reasons=[reason],
        ))
        cursor = cut_end

    if cursor < duration - 1e-6:
        result.append(Segment(
            clip_id=clip_id,
            start=cursor,
            end=duration,
            features={"speech_prob": _speech_prob_for_interval(speech_regions, cursor, duration)},
            decision="keep",
            decision_source="auto",
            reasons=[],
        ))

    return result
