"""Repeated take detection using rapidfuzz sentence similarity."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from autocut.config import TakesConfig
from autocut.models import Segment, Transcript, WordTimestamp

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def _take_text(words: list[WordTimestamp]) -> str:
    """Normalised, punctuation-free text for a group of words."""
    parts = []
    for w in words:
        t = _PUNCT.sub("", w.word.lower()).strip()
        if t:
            parts.append(t)
    return " ".join(parts)


def _split_into_takes(
    words: list[WordTimestamp],
    min_gap_s: float,
    min_words: int,
) -> list[list[WordTimestamp]]:
    """Group words into takes separated by pauses >= min_gap_s.

    Takes with fewer than min_words are dropped (too short to compare).
    """
    if not words:
        return []
    takes: list[list[WordTimestamp]] = [[words[0]]]
    for prev, curr in zip(words, words[1:]):
        if curr.start - prev.end >= min_gap_s:
            takes.append([])
        takes[-1].append(curr)
    return [t for t in takes if len(t) >= min_words]


def detect_repeated_takes(
    transcript: Transcript,
    cfg: TakesConfig,
) -> list[Segment]:
    """Return cut Segments for all but the last take in each repeat group.

    Splits the transcript into pause-separated takes, then greedily groups
    adjacent takes whose normalised text similarity is >= cfg.similarity_threshold.
    Within each group every take except the last is marked cut.
    """
    takes = _split_into_takes(transcript.words, cfg.min_gap_s, cfg.min_words)
    if len(takes) < 2:
        return []

    threshold = cfg.similarity_threshold

    # Greedily chain adjacent similar takes into groups.
    groups: list[list[int]] = [[0]]
    for i in range(1, len(takes)):
        prev_idx = groups[-1][-1]
        score = fuzz.ratio(_take_text(takes[prev_idx]), _take_text(takes[i])) / 100.0
        if score >= threshold:
            groups[-1].append(i)
        else:
            groups.append([i])

    segments: list[Segment] = []
    for group in groups:
        if len(group) < 2:
            continue
        n = len(group)
        for idx in group[:-1]:
            take = takes[idx]
            segments.append(Segment(
                clip_id=take[0].clip_id,
                start=take[0].start,
                end=take[-1].end,
                decision="cut",
                decision_source="auto",
                reasons=[f"repeated take ({n} takes)"],
            ))
    return segments
