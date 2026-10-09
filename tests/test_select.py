"""Tests for autocut.select — preset-driven segment selection."""

from __future__ import annotations

import pytest

from autocut.config import PacingConfig, PresetConfig
from autocut.models import Segment
from autocut.select import _effective_score, _in_opening, select_segments


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_seg(
    start: float,
    end: float,
    score: float = 0.5,
    decision: str = "keep",
    clip_id: str = "c",
) -> Segment:
    s = Segment(clip_id=clip_id, start=start, end=end, decision=decision)
    s.interest_score = score
    return s


def default_cfg(**overrides) -> PresetConfig:
    cfg = PresetConfig()
    for k, v in overrides.items():
        object.__setattr__(cfg, k, v)
    return cfg


# ---------------------------------------------------------------------------
# Input preservation
# ---------------------------------------------------------------------------

def test_input_segments_not_mutated():
    seg = make_seg(0.0, 10.0, score=0.9)
    cfg = default_cfg(targets={"tight": 5.0}, tolerance=0.0)
    out = select_segments([seg], "tight", cfg)
    assert seg.decision == "keep"           # original untouched
    assert seg.reasons == []
    assert out[0] is not seg                 # new object returned


def test_unknown_preset_raises():
    cfg = default_cfg()
    with pytest.raises(ValueError, match="Unknown preset"):
        select_segments([make_seg(0, 1)], "ultra", cfg)


# ---------------------------------------------------------------------------
# Target already covers all available duration
# ---------------------------------------------------------------------------

def test_target_covers_all_keeps_everything():
    segs = [make_seg(0, 10, score=0.3), make_seg(10, 20, score=0.6)]
    cfg = default_cfg(targets={"loose": 100.0})
    out = select_segments(segs, "loose", cfg)
    assert all(s.decision == "keep" for s in out)
    assert all("selected" in s.reasons[-1] for s in out)


def test_no_keep_candidates_returns_untouched_decisions():
    segs = [make_seg(0, 5, decision="cut"), make_seg(5, 10, decision="cut")]
    cfg = default_cfg(targets={"tight": 5.0})
    out = select_segments(segs, "tight", cfg)
    assert all(s.decision == "cut" for s in out)
    # No "selected"/"dropped" reasons appended — nothing to decide.
    assert all(s.reasons == [] for s in out)


# ---------------------------------------------------------------------------
# Upstream cuts are never resurrected
# ---------------------------------------------------------------------------

def test_cut_segments_never_resurrected():
    segs = [
        make_seg(0, 5, score=0.0, decision="cut"),   # upstream silence cut
        make_seg(5, 10, score=0.9, decision="keep"),
    ]
    cfg = default_cfg(targets={"tight": 100.0})
    out = select_segments(segs, "tight", cfg)
    assert out[0].decision == "cut"
    assert out[1].decision == "keep"


# ---------------------------------------------------------------------------
# Greedy ranking by score
# ---------------------------------------------------------------------------

def test_highest_score_picked_first():
    segs = [
        make_seg(0, 10, score=0.1),
        make_seg(10, 20, score=0.9),
        make_seg(20, 30, score=0.5),
    ]
    cfg = default_cfg(targets={"tight": 10.0}, tolerance=0.0)
    out = select_segments(segs, "tight", cfg)
    kept = [s for s in out if s.decision == "keep"]
    assert len(kept) == 1
    assert kept[0].interest_score == pytest.approx(0.9)


def test_takes_top_two_until_target_met():
    segs = [
        make_seg(0, 10, score=0.1),
        make_seg(10, 20, score=0.9),
        make_seg(20, 30, score=0.8),
        make_seg(30, 40, score=0.2),
    ]
    cfg = default_cfg(targets={"medium": 20.0}, tolerance=0.0)
    out = select_segments(segs, "medium", cfg)
    kept_scores = sorted(s.interest_score for s in out if s.decision == "keep")
    assert kept_scores == pytest.approx([0.8, 0.9])


# ---------------------------------------------------------------------------
# Tolerance / overshoot handling
# ---------------------------------------------------------------------------

def test_tolerance_allows_slight_overshoot():
    # Target 10s, tolerance 0.20 → upper = 12s. Two 6s segments total = 12s ≤ 12.
    segs = [
        make_seg(0, 6, score=0.9),
        make_seg(6, 12, score=0.8),
        make_seg(12, 18, score=0.1),
    ]
    cfg = default_cfg(targets={"t": 10.0}, tolerance=0.20)
    out = select_segments(segs, "t", cfg)
    kept = [s for s in out if s.decision == "keep"]
    assert len(kept) == 2


def test_zero_tolerance_skips_overshoot_segment():
    # Target 10s, tolerance 0 → first 6s picked, second 6s would overshoot.
    segs = [
        make_seg(0, 6, score=0.9),
        make_seg(6, 12, score=0.8),
    ]
    cfg = default_cfg(targets={"t": 10.0}, tolerance=0.0)
    out = select_segments(segs, "t", cfg)
    kept = [s for s in out if s.decision == "keep"]
    assert len(kept) == 1
    assert kept[0].interest_score == pytest.approx(0.9)


def test_single_oversized_segment_still_kept():
    """Edge case: one segment is already longer than target → keep it anyway,
    otherwise we would output nothing."""
    segs = [make_seg(0, 60, score=0.5)]
    cfg = default_cfg(targets={"tight": 10.0}, tolerance=0.0)
    out = select_segments(segs, "tight", cfg)
    assert out[0].decision == "keep"


# ---------------------------------------------------------------------------
# Reasons
# ---------------------------------------------------------------------------

def test_selected_reason_includes_score_and_preset():
    seg = make_seg(0, 5, score=0.73)
    cfg = default_cfg(targets={"tight": 5.0}, tolerance=0.0)
    out = select_segments([seg], "tight", cfg)
    assert any(
        "selected" in r and "0.73" in r and "tight" in r
        for r in out[0].reasons
    )


def test_dropped_reason_includes_score_and_preset():
    segs = [make_seg(0, 10, score=0.9), make_seg(10, 20, score=0.1)]
    cfg = default_cfg(targets={"tight": 10.0}, tolerance=0.0)
    out = select_segments(segs, "tight", cfg)
    dropped = next(s for s in out if s.decision == "cut")
    assert any("dropped" in r and "tight" in r for r in dropped.reasons)


# ---------------------------------------------------------------------------
# Chronological order preserved in the returned list
# ---------------------------------------------------------------------------

def test_output_list_order_matches_input():
    segs = [
        make_seg(0, 5, score=0.1, clip_id="a"),
        make_seg(5, 10, score=0.9, clip_id="a"),
        make_seg(0, 5, score=0.5, clip_id="b"),
    ]
    cfg = default_cfg(targets={"t": 5.0}, tolerance=0.0)
    out = select_segments(segs, "t", cfg)
    # Order of returned list matches input (chronological, by construction).
    assert [(s.clip_id, s.start) for s in out] == [(s.clip_id, s.start) for s in segs]


# ---------------------------------------------------------------------------
# Score ties: shorter segment preferred (packs more variety)
# ---------------------------------------------------------------------------

def test_tie_breaks_prefer_shorter_segment():
    """Equal scores → shorter segment wins (pack more variety at same quality)."""
    segs = [
        make_seg(0, 10, score=0.5),   # 10s
        make_seg(10, 13, score=0.5),  # 3s — should win the tie
    ]
    cfg = default_cfg(targets={"t": 5.0}, tolerance=0.0)
    out = select_segments(segs, "t", cfg)
    kept = [s for s in out if s.decision == "keep"]
    assert len(kept) == 1
    assert kept[0].duration == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# All three default presets produce valid cuts in one input
# ---------------------------------------------------------------------------

def test_default_presets_produce_distinct_cut_lengths():
    # 10 segments × 30s each = 300s total.
    segs = [make_seg(i * 30, (i + 1) * 30, score=1.0 - i * 0.05) for i in range(10)]
    cfg = PresetConfig()  # defaults: tight=45, medium=90, loose=180

    out_tight = select_segments(segs, "tight", cfg)
    out_medium = select_segments(segs, "medium", cfg)
    out_loose = select_segments(segs, "loose", cfg)

    dur_tight = sum(s.duration for s in out_tight if s.decision == "keep")
    dur_medium = sum(s.duration for s in out_medium if s.decision == "keep")
    dur_loose = sum(s.duration for s in out_loose if s.decision == "keep")

    assert dur_tight < dur_medium < dur_loose
    assert dur_tight <= 45 * 1.10
    assert dur_medium <= 90 * 1.10
    assert dur_loose <= 180 * 1.10


# ---------------------------------------------------------------------------
# Pacing bias
# ---------------------------------------------------------------------------

def _pacing(**overrides) -> PacingConfig:
    base = dict(enabled=True, opening_window_s=7.0,
                opening_ideal_duration_s=2.0, opening_bias=0.5)
    base.update(overrides)
    return PacingConfig(**base)


def test_in_opening_true_when_start_within_window():
    assert _in_opening(make_seg(0, 2, score=0.5), _pacing()) is True


def test_in_opening_false_when_start_beyond_window():
    assert _in_opening(make_seg(10, 12, score=0.5), _pacing()) is False


def test_in_opening_false_when_pacing_none():
    assert _in_opening(make_seg(0, 2, score=0.5), None) is False


def test_in_opening_false_when_pacing_disabled():
    assert _in_opening(make_seg(0, 2, score=0.5), _pacing(enabled=False)) is False


def test_effective_score_no_pacing_returns_raw():
    s = make_seg(0, 2, score=0.6)
    assert _effective_score(s, None) == pytest.approx(0.6)


def test_effective_score_opening_segment_at_ideal_duration_unchanged():
    # duration == ideal → factor = 1
    s = make_seg(0, 2, score=0.6)   # 2s, ideal 2s
    assert _effective_score(s, _pacing()) == pytest.approx(0.6)


def test_effective_score_short_opening_segment_boosted():
    # duration 1s < ideal 2s → factor > 1
    s = make_seg(0, 1, score=0.5)
    assert _effective_score(s, _pacing()) > 0.5


def test_effective_score_long_opening_segment_penalised():
    # duration 4s > ideal 2s → factor < 1
    s = make_seg(0, 4, score=0.5)
    assert _effective_score(s, _pacing()) < 0.5


def test_effective_score_outside_window_unchanged_even_if_short():
    # start=10s is past window (7s) → raw score regardless of duration.
    s = make_seg(10, 10.5, score=0.5)
    assert _effective_score(s, _pacing()) == pytest.approx(0.5)


def test_pacing_disabled_matches_baseline_selection():
    """A disabled pacing should not change selection outcome vs no pacing."""
    segs = [
        make_seg(0, 5, score=0.7),
        make_seg(5, 10, score=0.5),
        make_seg(10, 15, score=0.9),
    ]
    cfg = default_cfg(targets={"t": 10.0}, tolerance=0.0)
    base = {s.start for s in select_segments(segs, "t", cfg) if s.decision == "keep"}
    with_off = {
        s.start for s in select_segments(segs, "t", cfg, pacing=_pacing(enabled=False))
        if s.decision == "keep"
    }
    assert base == with_off


def test_pacing_prefers_short_opener_over_long_higher_score_opener():
    """A long opener with slightly higher base score should lose to a short one
    once pacing is applied with a strong bias."""
    segs = [
        make_seg(0, 6, score=0.80),      # long opener, slightly higher score
        make_seg(0, 1, score=0.70, clip_id="c2"),  # short opener, lower score
    ]
    cfg = default_cfg(targets={"t": 1.5}, tolerance=0.0)
    # Baseline (no pacing): the longer 0.8 wins.
    out_base = select_segments(segs, "t", cfg)
    kept_base = {(s.clip_id, s.start) for s in out_base if s.decision == "keep"}
    assert ("c", 0.0) in kept_base

    # With pacing + strong bias: short opener should be preferred.
    out_paced = select_segments(segs, "t", cfg,
                                pacing=_pacing(opening_bias=1.0,
                                               opening_ideal_duration_s=1.0))
    kept_paced = {(s.clip_id, s.start) for s in out_paced if s.decision == "keep"}
    assert ("c2", 0.0) in kept_paced


def test_pacing_reason_tag_appears_on_opening_segment():
    segs = [make_seg(0, 2, score=0.9), make_seg(10, 12, score=0.8)]
    cfg = default_cfg(targets={"t": 2.0}, tolerance=0.0)
    out = select_segments(segs, "t", cfg, pacing=_pacing())
    opener = next(s for s in out if s.start == 0.0)
    assert any("pacing" in r for r in opener.reasons)


def test_pacing_no_tag_outside_opening_window():
    segs = [make_seg(10, 12, score=0.9)]   # starts past window
    cfg = default_cfg(targets={"t": 10.0}, tolerance=0.0)
    out = select_segments(segs, "t", cfg, pacing=_pacing())
    assert not any("pacing" in r for r in out[0].reasons)
