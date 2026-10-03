"""Filler-word detection: marks isolated Greek hesitation words as cut segments."""

from __future__ import annotations

import re
import unicodedata

from autocut.config import FillerConfig
from autocut.models import Segment, Transcript, WordTimestamp

_PUNCT = re.compile(r"[^\w]", re.UNICODE)
_LONG_RUN = re.compile(r"(.)\1{2,}", re.UNICODE)   # 3+ same char → collapse to 2


def _normalize(word: str) -> str:
    """Lowercase, strip punctuation, collapse elongations (εεεε → εε)."""
    w = word.lower().strip()
    w = _PUNCT.sub("", w)
    w = _LONG_RUN.sub(r"\1\1", w)
    return unicodedata.normalize("NFC", w)


def _is_isolated(idx: int, words: list[WordTimestamp], min_gap_s: float) -> bool:
    """True if the word at `idx` has sufficient silence on both sides."""
    w = words[idx]
    before = idx == 0 or (w.start - words[idx - 1].end) >= min_gap_s
    after = idx == len(words) - 1 or (words[idx + 1].start - w.end) >= min_gap_s
    return before and after


def detect_fillers(transcript: Transcript, cfg: FillerConfig) -> list[Segment]:
    """Return a cut Segment for every isolated filler word in *transcript*.

    A word is a filler if, after normalisation, it matches one of `cfg.words`
    AND is surrounded by at least `cfg.min_isolation_ms` of silence on each
    side (or sits at the start/end of the word list).
    """
    filler_set = {_normalize(f) for f in cfg.words}
    min_gap_s = cfg.min_isolation_ms / 1000.0
    pad_s = cfg.padding_ms / 1000.0

    segments: list[Segment] = []
    words = transcript.words
    for i, w in enumerate(words):
        if _normalize(w.word) not in filler_set:
            continue
        if not _is_isolated(i, words, min_gap_s):
            continue
        segments.append(Segment(
            clip_id=w.clip_id,
            start=max(0.0, w.start - pad_s),
            end=w.end + pad_s,
            decision="cut",
            decision_source="auto",
            reasons=[f"filler: {w.word.strip()}"],
        ))
    return segments


def punch_out_fillers(
    segments: list[Segment],
    filler_cuts: list[Segment],
    min_duration: float = 0.05,
) -> list[Segment]:
    """Punch filler cuts into an existing tiling segment list.

    Each keep segment that overlaps a filler cut is split into
    keep / cut / keep sub-segments.  Sub-segments shorter than
    `min_duration` are dropped to avoid creating hairline clips.
    The result is sorted by (clip_id, start).
    """
    if not filler_cuts:
        return sorted(segments, key=lambda s: (s.clip_id, s.start))

    result: list[Segment] = []
    for seg in segments:
        if seg.decision == "cut":
            result.append(seg)
            continue

        overlapping = sorted(
            (f for f in filler_cuts
             if f.clip_id == seg.clip_id
             and f.start < seg.end
             and f.end > seg.start),
            key=lambda f: f.start,
        )
        if not overlapping:
            result.append(seg)
            continue

        cursor = seg.start
        for fc in overlapping:
            cut_start = max(cursor, fc.start)
            cut_end = min(seg.end, fc.end)

            if cut_start - cursor >= min_duration:
                result.append(Segment(
                    clip_id=seg.clip_id, start=cursor, end=cut_start,
                    features=seg.features, interest_score=seg.interest_score,
                    decision="keep", decision_source=seg.decision_source,
                    reasons=seg.reasons[:],
                ))
            if cut_end > cut_start:
                result.append(Segment(
                    clip_id=seg.clip_id, start=cut_start, end=cut_end,
                    decision="cut", decision_source="auto",
                    reasons=fc.reasons[:],
                ))
            cursor = cut_end

        if seg.end - cursor >= min_duration:
            result.append(Segment(
                clip_id=seg.clip_id, start=cursor, end=seg.end,
                features=seg.features, interest_score=seg.interest_score,
                decision="keep", decision_source=seg.decision_source,
                reasons=seg.reasons[:],
            ))

    return sorted(result, key=lambda s: (s.clip_id, s.start))
