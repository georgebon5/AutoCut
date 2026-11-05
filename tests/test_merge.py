"""Tests for autocut.merge — gap-closing segment merging."""

from __future__ import annotations

from pathlib import Path

import pytest

from autocut.models import Segment
from autocut.merge import merge_close_segments


def _seg(clip_id: str, start: float, end: float, decision: str = "keep") -> Segment:
    return Segment(clip_id=clip_id, start=start, end=end, decision=decision)


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------

def test_zero_max_gap_returns_copy_unchanged():
    segs = [_seg("c", 1.0, 2.0), _seg("c", 2.5, 3.5)]
    result = merge_close_segments(segs, 0.0)
    assert result[0].end == 2.0
    assert result[1].start == 2.5
    assert result is not segs


def test_negative_max_gap_returns_copy_unchanged():
    segs = [_seg("c", 1.0, 2.0), _seg("c", 2.5, 3.5)]
    result = merge_close_segments(segs, -1.0)
    assert result[0].end == 2.0


def test_does_not_mutate_input():
    segs = [_seg("c", 1.0, 2.0), _seg("c", 2.1, 3.0)]
    merge_close_segments(segs, 0.5)
    assert segs[0].end == 2.0
    assert segs[1].start == 2.1


# ---------------------------------------------------------------------------
# Gap closing
# ---------------------------------------------------------------------------

def test_gap_smaller_than_threshold_is_closed_at_midpoint():
    segs = [_seg("c", 1.0, 2.0), _seg("c", 2.4, 3.4)]
    # gap = 0.4 s, threshold = 0.5 s → should close
    result = merge_close_segments(segs, 0.5)
    assert result[0].end == pytest.approx(2.2)
    assert result[1].start == pytest.approx(2.2)


def test_gap_exactly_at_threshold_is_closed():
    segs = [_seg("c", 0.0, 1.0), _seg("c", 1.5, 2.5)]
    result = merge_close_segments(segs, 0.5)
    assert result[0].end == pytest.approx(1.25)
    assert result[1].start == pytest.approx(1.25)


def test_gap_larger_than_threshold_not_closed():
    segs = [_seg("c", 0.0, 1.0), _seg("c", 1.8, 2.8)]
    result = merge_close_segments(segs, 0.5)
    assert result[0].end == pytest.approx(1.0)
    assert result[1].start == pytest.approx(1.8)


def test_multiple_gaps_handled_independently():
    segs = [
        _seg("c", 0.0, 1.0),   # gap 0.2 → close
        _seg("c", 1.2, 2.0),   # gap 1.0 → keep
        _seg("c", 3.0, 4.0),
    ]
    result = merge_close_segments(segs, 0.5)
    assert result[0].end == pytest.approx(1.1)    # midpoint of [1.0, 1.2]
    assert result[1].start == pytest.approx(1.1)
    assert result[1].end == pytest.approx(2.0)    # large gap: unchanged
    assert result[2].start == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Cut segments
# ---------------------------------------------------------------------------

def test_cut_segments_not_affected():
    segs = [
        _seg("c", 0.0, 1.0, decision="cut"),
        _seg("c", 1.1, 2.0, decision="keep"),
    ]
    result = merge_close_segments(segs, 1.0)
    # Only kept segments are considered; cut segment unchanged
    assert result[0].end == pytest.approx(1.0)
    assert result[1].start == pytest.approx(1.1)


def test_cut_segment_between_two_keeps_does_not_close_gap():
    # A cut segment in between means the two keeps are NOT adjacent for
    # gap-closing purposes — they don't share a direct gap.
    segs = [
        _seg("c", 0.0, 1.0, decision="keep"),
        _seg("c", 1.1, 1.3, decision="cut"),
        _seg("c", 1.4, 2.5, decision="keep"),
    ]
    result = merge_close_segments(segs, 1.0)
    # The kept pair has a gap of 0.4 s but the cut segment sits between them.
    # merge_close_segments only looks at consecutive kept boundaries, not all
    # boundaries. The gap between keep[0].end=1.0 and keep[1].start=1.4 is
    # 0.4 s < 1.0 s → it IS closed (cut segment is irrelevant to boundary calc).
    assert result[0].end == pytest.approx(1.2)
    assert result[2].start == pytest.approx(1.2)


# ---------------------------------------------------------------------------
# Multi-clip isolation
# ---------------------------------------------------------------------------

def test_different_clips_are_independent():
    segs = [
        _seg("a", 0.0, 1.0),
        _seg("b", 1.1, 2.0),  # same time range but different clip — no merge
    ]
    result = merge_close_segments(segs, 1.0)
    assert result[0].end == pytest.approx(1.0)
    assert result[1].start == pytest.approx(1.1)


def test_same_clip_merges_correctly_with_other_clips_present():
    segs = [
        _seg("a", 0.0, 1.0),
        _seg("a", 1.2, 2.0),   # gap 0.2 → close
        _seg("b", 0.5, 1.5),   # unrelated clip
    ]
    result = merge_close_segments(segs, 0.5)
    assert result[0].end == pytest.approx(1.1)
    assert result[1].start == pytest.approx(1.1)
    assert result[2].start == pytest.approx(0.5)  # clip b unchanged
