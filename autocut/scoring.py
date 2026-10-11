"""Interest score computation.

Two interchangeable backends share the same side effect (setting
``segment.interest_score``):

* ``score_segments`` — weighted normalised feature combination driven by
  the hand-tuned ``ScoringConfig`` weights.
* ``score_segments_with_model`` — predictions from a trained LogisticModel.
  Lets the Phase-5 classifier replace the heuristic without touching any
  downstream selection logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from autocut.config import ScoringConfig
from autocut.models import Segment

if TYPE_CHECKING:
    from autocut.trainer import LogisticModel


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


def score_segments_with_model(
    segments: list[Segment], model: "LogisticModel",
) -> None:
    """Set interest_score on every keep segment using a trained classifier.

    The model's ``predict_proba`` already returns a value in [0, 1]; we clamp
    defensively so downstream selection code can assume the invariant even if
    the model is swapped for something less well-behaved later.
    """
    for seg in segments:
        if seg.decision != "keep":
            continue
        p = float(model.predict_proba(seg.features))
        seg.interest_score = max(0.0, min(1.0, p))
