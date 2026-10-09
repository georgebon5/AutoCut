"""Interest score computation: weighted normalised feature combination."""

from __future__ import annotations

from autocut.config import ScoringConfig
from autocut.models import Segment


def _norm(value: float, max_val: float) -> float:
    """Linearly normalise value to [0, 1], clipping at max_val."""
    if max_val <= 0:
        return 0.0
    return min(value / max_val, 1.0)


def score_segment(features: dict[str, float], cfg: ScoringConfig) -> float:
    """Return an interest score in [0, 1] for a single feature dict.

    Only features present in both `features` and `cfg.weights` contribute.
    The score is normalised by the sum of the weights of present features,
    so missing features do not unfairly penalise the result.
    """
    weighted_sum = 0.0
    active_weight = 0.0
    for feature, weight in cfg.weights.items():
        if feature not in features:
            continue
        max_val = cfg.feature_max.get(feature, 1.0)
        weighted_sum += weight * _norm(features[feature], max_val)
        active_weight += weight
    if active_weight == 0.0:
        return 0.0
    return weighted_sum / active_weight


def score_segments(segments: list[Segment], cfg: ScoringConfig) -> None:
    """Set interest_score on every keep segment in-place.

    Cut segments are skipped (their interest_score stays at 0.0).
    Keep segments with no extracted features receive 0.0.
    """
    for seg in segments:
        if seg.decision != "keep":
            continue
        seg.interest_score = score_segment(seg.features, cfg)
