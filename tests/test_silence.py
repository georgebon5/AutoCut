from __future__ import annotations

import pytest

from autocut.config import SilenceConfig
from autocut.models import SpeechRegion
from autocut.silence import (
    _merge_speech_regions,
    _silence_gaps,
    _snap_to_word_boundary,
    _speech_prob_for_interval,
    apply_silence_removal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_region(start: float, end: float, clip_id: str = "c", prob: float = 0.9) -> SpeechRegion:
    return SpeechRegion(clip_id=clip_id, start=start, end=end, speech_prob=prob)


def cfg(min_silence: float = 0.5, padding: float = 0.1) -> SilenceConfig:
    return SilenceConfig(min_silence=min_silence, padding=padding)


# ---------------------------------------------------------------------------
# _merge_speech_regions
# ---------------------------------------------------------------------------

def test_merge_no_overlap():
    regions = [make_region(0.0, 1.0), make_region(2.0, 3.0)]
    assert _merge_speech_regions(regions) == [(0.0, 1.0), (2.0, 3.0)]


def test_merge_overlapping():
    regions = [make_region(0.0, 2.0), make_region(1.5, 3.0)]
    assert _merge_speech_regions(regions) == [(0.0, 3.0)]


def test_merge_adjacent():
    regions = [make_region(0.0, 1.0), make_region(1.0, 2.0)]
    assert _merge_speech_regions(regions) == [(0.0, 2.0)]


def test_merge_empty():
    assert _merge_speech_regions([]) == []


def test_merge_unsorted_input():
    regions = [make_region(2.0, 3.0), make_region(0.0, 1.0)]
    result = _merge_speech_regions(regions)
    assert result == [(0.0, 1.0), (2.0, 3.0)]


# ---------------------------------------------------------------------------
# _silence_gaps
# ---------------------------------------------------------------------------

def test_gaps_leading_and_trailing():
    intervals = [(1.0, 3.0)]
    gaps = _silence_gaps(intervals, duration=5.0)
    assert gaps == [(0.0, 1.0), (3.0, 5.0)]


def test_gaps_between_speech():
    intervals = [(0.0, 1.0), (2.0, 3.0)]
    gaps = _silence_gaps(intervals, duration=4.0)
    assert (1.0, 2.0) in gaps


def test_gaps_speech_fills_clip():
    intervals = [(0.0, 5.0)]
    gaps = _silence_gaps(intervals, duration=5.0)
    assert gaps == []


def test_gaps_no_speech():
    gaps = _silence_gaps([], duration=5.0)
    assert gaps == [(0.0, 5.0)]


# ---------------------------------------------------------------------------
# _snap_to_word_boundary
# ---------------------------------------------------------------------------

def test_snap_exact_match():
    words = [{"start": 1.0, "end": 1.5}]
    assert _snap_to_word_boundary(1.0, words) == pytest.approx(1.0)


def test_snap_within_window():
    words = [{"start": 1.0, "end": 1.5}]
    t = _snap_to_word_boundary(1.12, words, window=0.15)
    assert t == pytest.approx(1.0)


def test_snap_outside_window():
    words = [{"start": 1.0, "end": 1.5}]
    t = _snap_to_word_boundary(2.0, words, window=0.15)
    assert t == pytest.approx(2.0)


def test_snap_picks_nearest():
    words = [{"start": 1.0, "end": 1.5}, {"start": 2.0, "end": 2.5}]
    t = _snap_to_word_boundary(1.4, words, window=0.5)
    assert t == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# _speech_prob_for_interval
# ---------------------------------------------------------------------------

def test_speech_prob_no_overlap():
    regions = [make_region(0.0, 1.0, prob=0.9)]
    assert _speech_prob_for_interval(regions, 2.0, 3.0) == pytest.approx(0.0)


def test_speech_prob_full_overlap():
    regions = [make_region(0.0, 2.0, prob=0.8)]
    assert _speech_prob_for_interval(regions, 0.5, 1.5) == pytest.approx(0.8)


def test_speech_prob_mean_of_two():
    regions = [make_region(0.0, 1.0, prob=0.6), make_region(0.8, 2.0, prob=1.0)]
    val = _speech_prob_for_interval(regions, 0.9, 1.5)
    assert val == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# apply_silence_removal — core logic
# ---------------------------------------------------------------------------

def test_no_speech_keeps_full_clip():
    segs = apply_silence_removal([], "c", duration=5.0, cfg=cfg())
    assert len(segs) == 1
    assert segs[0].decision == "keep"
    assert segs[0].start == pytest.approx(0.0)
    assert segs[0].end == pytest.approx(5.0)
    assert "B-roll" in segs[0].reasons[0]


def test_covers_full_duration():
    regions = [make_region(1.0, 3.0)]
    segs = apply_silence_removal(regions, "c", duration=5.0, cfg=cfg())
    assert segs[0].start == pytest.approx(0.0)
    assert segs[-1].end == pytest.approx(5.0)
    # Contiguous
    for i in range(len(segs) - 1):
        assert segs[i].end == pytest.approx(segs[i + 1].start, abs=1e-5)


def test_long_silence_produces_cut():
    # 2s silence in the middle, > min_silence=0.5
    regions = [make_region(0.0, 1.0), make_region(3.0, 4.0)]
    segs = apply_silence_removal(regions, "c", duration=4.0, cfg=cfg(min_silence=0.5, padding=0.1))
    cuts = [s for s in segs if s.decision == "cut"]
    assert len(cuts) == 1
    cut = cuts[0]
    assert cut.start == pytest.approx(1.1)   # 1.0 + padding
    assert cut.end == pytest.approx(2.9)     # 3.0 - padding
    assert "silence" in cut.reasons[0]


def test_short_silence_not_cut():
    # 0.3s silence, < min_silence=0.5
    regions = [make_region(0.0, 1.0), make_region(1.3, 3.0)]
    segs = apply_silence_removal(regions, "c", duration=3.0, cfg=cfg(min_silence=0.5, padding=0.1))
    cuts = [s for s in segs if s.decision == "cut"]
    assert cuts == []


def test_gap_collapses_after_padding_not_cut():
    # Gap is exactly 2*padding — cut window is 0 after shrinking → skip
    regions = [make_region(0.0, 1.0), make_region(1.2, 3.0)]
    segs = apply_silence_removal(regions, "c", duration=3.0, cfg=cfg(min_silence=0.1, padding=0.1))
    cuts = [s for s in segs if s.decision == "cut"]
    assert cuts == []


def test_cut_reason_contains_duration():
    regions = [make_region(0.0, 1.0), make_region(3.0, 4.0)]
    segs = apply_silence_removal(regions, "c", duration=4.0, cfg=cfg())
    cut = next(s for s in segs if s.decision == "cut")
    assert "2.00" in cut.reasons[0]


def test_keep_segments_have_speech_prob():
    regions = [make_region(0.5, 2.0, prob=0.85)]
    segs = apply_silence_removal(regions, "c", duration=4.0, cfg=cfg())
    keeps = [s for s in segs if s.decision == "keep"]
    # At least one keep segment that overlaps the speech region should have prob > 0
    assert any(s.features["speech_prob"] > 0 for s in keeps)


def test_multiple_cuts():
    # Three long silences
    regions = [make_region(1.0, 2.0), make_region(3.0, 4.0), make_region(5.0, 6.0)]
    segs = apply_silence_removal(regions, "c", duration=7.0, cfg=cfg(min_silence=0.5, padding=0.05))
    cuts = [s for s in segs if s.decision == "cut"]
    # Gaps: (0,1), (2,3), (4,5), (6,7) — all >= 0.5s → 4 cuts
    assert len(cuts) == 4


def test_sorted_by_start():
    regions = [make_region(0.0, 1.0), make_region(3.0, 4.0)]
    segs = apply_silence_removal(regions, "c", duration=5.0, cfg=cfg())
    for i in range(len(segs) - 1):
        assert segs[i].start < segs[i + 1].start


def test_clip_id_propagated():
    regions = [make_region(0.0, 1.0)]
    segs = apply_silence_removal(regions, "myclip", duration=2.0, cfg=cfg())
    assert all(s.clip_id == "myclip" for s in segs)


def test_with_word_timestamps_snaps_cut():
    # Speech [0,1], silence [1,2], speech [2,3]
    # Word boundary at 1.05s — cut_start (1.0+0.05=1.05) should snap to 1.05
    regions = [make_region(0.0, 1.0), make_region(2.0, 3.0)]
    words = [{"start": 0.8, "end": 1.05}, {"start": 2.0, "end": 2.5}]
    segs = apply_silence_removal(
        regions, "c", duration=3.0,
        cfg=cfg(min_silence=0.5, padding=0.05),
        word_timestamps=words,
    )
    cut = next(s for s in segs if s.decision == "cut")
    # cut_start was 1.05, nearest boundary is 1.05 → stays
    assert cut.start == pytest.approx(1.05, abs=0.01)
