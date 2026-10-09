"""Tests for autocut.scoring — interest score computation."""

from __future__ import annotations

import pytest

from autocut.config import ScoringConfig
from autocut.models import Segment
from autocut.scoring import _norm, score_segment, score_segments


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_segment(start: float = 0.0, end: float = 1.0, decision: str = "keep") -> Segment:
    return Segment(clip_id="c", start=start, end=end, decision=decision)


def default_cfg(**overrides) -> ScoringConfig:
    cfg = ScoringConfig()
    for k, v in overrides.items():
        object.__setattr__(cfg, k, v)
    return cfg


def full_features() -> dict[str, float]:
    """Features at exactly feature_max → each normalised to 1.0."""
    return {
        "rms_energy": 0.15,
        "pitch_std": 50.0,
        "voiced_fraction": 1.0,
        "speaking_rate": 6.0,
        "motion_mean": 0.20,
    }


def zero_features() -> dict[str, float]:
    return {
        "rms_energy": 0.0,
        "pitch_std": 0.0,
        "voiced_fraction": 0.0,
        "speaking_rate": 0.0,
        "motion_mean": 0.0,
    }


# ---------------------------------------------------------------------------
# _norm
# ---------------------------------------------------------------------------

def test_norm_zero_value():
    assert _norm(0.0, 1.0) == pytest.approx(0.0)


def test_norm_at_max():
    assert _norm(1.0, 1.0) == pytest.approx(1.0)


def test_norm_half_max():
    assert _norm(0.5, 1.0) == pytest.approx(0.5)


def test_norm_clips_above_max():
    assert _norm(2.0, 1.0) == pytest.approx(1.0)


def test_norm_zero_max_returns_zero():
    assert _norm(5.0, 0.0) == pytest.approx(0.0)


def test_norm_negative_max_returns_zero():
    assert _norm(1.0, -1.0) == pytest.approx(0.0)


def test_norm_scales_correctly():
    assert _norm(0.075, 0.15) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# score_segment — boundary cases
# ---------------------------------------------------------------------------

def test_score_empty_features_returns_zero():
    cfg = default_cfg()
    assert score_segment({}, cfg) == pytest.approx(0.0)


def test_score_zero_features_returns_zero():
    cfg = default_cfg()
    assert score_segment(zero_features(), cfg) == pytest.approx(0.0)


def test_score_full_features_returns_one():
    cfg = default_cfg()
    assert score_segment(full_features(), cfg) == pytest.approx(1.0)


def test_score_result_between_zero_and_one():
    cfg = default_cfg()
    feats = {
        "rms_energy": 0.07,
        "pitch_std": 25.0,
        "voiced_fraction": 0.6,
        "speaking_rate": 3.0,
        "motion_mean": 0.10,
    }
    score = score_segment(feats, cfg)
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# score_segment — weight correctness
# ---------------------------------------------------------------------------

def test_single_feature_score_proportional_to_normalised_value():
    """With one active feature and weight=1.0, score == norm(value)."""
    cfg = ScoringConfig(
        weights={"rms_energy": 1.0},
        feature_max={"rms_energy": 0.15},
    )
    feats = {"rms_energy": 0.075}   # half of max → 0.5
    assert score_segment(feats, cfg) == pytest.approx(0.5)


def test_equal_weights_equal_contribution():
    """Two features at half max with equal weights → score = 0.5."""
    cfg = ScoringConfig(
        weights={"rms_energy": 1.0, "motion_mean": 1.0},
        feature_max={"rms_energy": 1.0, "motion_mean": 1.0},
    )
    feats = {"rms_energy": 0.5, "motion_mean": 0.5}
    assert score_segment(feats, cfg) == pytest.approx(0.5)


def test_higher_weight_feature_dominates():
    """Feature with higher weight should drive score higher."""
    cfg = ScoringConfig(
        weights={"rms_energy": 0.9, "motion_mean": 0.1},
        feature_max={"rms_energy": 1.0, "motion_mean": 1.0},
    )
    feats_a = {"rms_energy": 1.0, "motion_mean": 0.0}   # strong audio, no motion
    feats_b = {"rms_energy": 0.0, "motion_mean": 1.0}   # strong motion, no audio
    assert score_segment(feats_a, cfg) > score_segment(feats_b, cfg)


def test_missing_feature_excluded_from_normalisation():
    """A weight defined in cfg but absent from features must NOT lower the score."""
    cfg = ScoringConfig(
        weights={"rms_energy": 1.0, "motion_mean": 1.0},
        feature_max={"rms_energy": 1.0, "motion_mean": 1.0},
    )
    feats_full = {"rms_energy": 1.0, "motion_mean": 1.0}
    feats_partial = {"rms_energy": 1.0}   # motion missing — should not penalise

    # Both should score 1.0 (full normalised for present features)
    assert score_segment(feats_full, cfg) == pytest.approx(1.0)
    assert score_segment(feats_partial, cfg) == pytest.approx(1.0)


def test_zero_weight_feature_has_no_effect():
    cfg = ScoringConfig(
        weights={"rms_energy": 0.0, "motion_mean": 1.0},
        feature_max={"rms_energy": 1.0, "motion_mean": 1.0},
    )
    feats_a = {"rms_energy": 1.0, "motion_mean": 0.5}
    feats_b = {"rms_energy": 0.0, "motion_mean": 0.5}
    assert score_segment(feats_a, cfg) == pytest.approx(score_segment(feats_b, cfg))


def test_score_above_feature_max_clips_to_one():
    """Feature values above feature_max should not push score above 1.0."""
    cfg = ScoringConfig(
        weights={"rms_energy": 1.0},
        feature_max={"rms_energy": 0.10},
    )
    feats = {"rms_energy": 999.0}   # very large value
    assert score_segment(feats, cfg) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# score_segments — in-place modification
# ---------------------------------------------------------------------------

def test_score_segments_sets_interest_score():
    segs = [make_segment()]
    segs[0].features = full_features()
    score_segments(segs, ScoringConfig())
    assert segs[0].interest_score == pytest.approx(1.0)


def test_score_segments_skips_cut_segments():
    seg = make_segment(decision="cut")
    seg.features = full_features()
    score_segments([seg], ScoringConfig())
    assert seg.interest_score == pytest.approx(0.0)  # default unchanged


def test_score_segments_empty_features_gives_zero():
    seg = make_segment()
    seg.features = {}
    score_segments([seg], ScoringConfig())
    assert seg.interest_score == pytest.approx(0.0)


def test_score_segments_modifies_in_place():
    segs = [make_segment(), make_segment(start=2.0, end=3.0)]
    for s in segs:
        s.features = {
            "rms_energy": 0.075,
            "pitch_std": 25.0,
            "voiced_fraction": 0.5,
            "speaking_rate": 3.0,
            "motion_mean": 0.10,
        }
    score_segments(segs, ScoringConfig())
    assert all(0.0 < s.interest_score <= 1.0 for s in segs)


def test_score_segments_mixed_decisions():
    keep = make_segment(0.0, 1.0, decision="keep")
    cut = make_segment(1.0, 2.0, decision="cut")
    keep.features = full_features()
    cut.features = full_features()
    score_segments([keep, cut], ScoringConfig())
    assert keep.interest_score == pytest.approx(1.0)
    assert cut.interest_score == pytest.approx(0.0)  # skipped


def test_score_segments_monotone_with_feature_values():
    """Higher feature values → higher score (all else equal)."""
    cfg = ScoringConfig()
    seg_low = make_segment(0.0, 1.0)
    seg_high = make_segment(2.0, 3.0)
    seg_low.features = {k: 0.1 * v for k, v in full_features().items()}
    seg_high.features = {k: 0.9 * v for k, v in full_features().items()}
    score_segments([seg_low, seg_high], cfg)
    assert seg_high.interest_score > seg_low.interest_score


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def test_scoring_config_weights_sum_to_one():
    cfg = ScoringConfig()
    assert sum(cfg.weights.values()) == pytest.approx(1.0)


def test_scoring_config_feature_max_covers_all_weights():
    cfg = ScoringConfig()
    for feature in cfg.weights:
        assert feature in cfg.feature_max, f"feature_max missing key: {feature}"
