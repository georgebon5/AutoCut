"""Tests for autocut.trainer — numpy logistic regression + training pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from autocut.eval import EvalResult, SegmentEval
from autocut.models import EDL, ProxyInfo, Segment
from autocut.trainer import (
    LogisticModel,
    TrainingExample,
    _f1,
    _sigmoid,
    build_matrix,
    collect_from_eval,
    collect_from_overrides,
    fit_logistic,
    train_and_evaluate,
)


# ---------------------------------------------------------------------------
# Fake data helpers
# ---------------------------------------------------------------------------

def _proxy(clip_id: str = "c") -> ProxyInfo:
    return ProxyInfo(
        clip_id=clip_id,
        original_path=Path(f"/{clip_id}.mp4"),
        proxy_path=Path(f"/{clip_id}_proxy.mp4"),
        audio_path=Path(f"/{clip_id}.wav"),
        duration=10.0, fps=30.0, width=1920, height=1080,
    )


def _seg(
    start: float, end: float, *, decision: str = "keep",
    features: dict[str, float] | None = None, score: float = 0.5,
) -> Segment:
    s = Segment(clip_id="c", start=start, end=end, decision=decision)
    s.features = features or {}
    s.interest_score = score
    return s


@dataclass
class _FakeOverrideRow:
    """Just the fields collect_from_overrides reads."""
    id: int
    segment_index: int
    previous_decision: str
    new_decision: str
    created_at: datetime


# ---------------------------------------------------------------------------
# _sigmoid stability
# ---------------------------------------------------------------------------

def test_sigmoid_at_zero_is_half():
    assert _sigmoid(np.array([0.0]))[0] == pytest.approx(0.5)


def test_sigmoid_does_not_overflow_at_large_positive():
    out = _sigmoid(np.array([1_000.0, 10_000.0]))
    assert np.all(out > 0.99)
    assert np.all(np.isfinite(out))


def test_sigmoid_does_not_overflow_at_large_negative():
    out = _sigmoid(np.array([-1_000.0, -10_000.0]))
    assert np.all(out < 0.01)
    assert np.all(np.isfinite(out))


# ---------------------------------------------------------------------------
# F1 helper
# ---------------------------------------------------------------------------

def test_f1_perfect_is_one():
    assert _f1([1, 0, 1, 0], [1, 0, 1, 0]) == pytest.approx(1.0)


def test_f1_zero_tp_is_zero():
    assert _f1([0, 0, 0, 0], [1, 1, 0, 0]) == 0.0


def test_f1_handles_fp_and_fn():
    # tp=1, fp=1, fn=1 → precision=0.5, recall=0.5 → F1=0.5
    assert _f1([1, 1, 0, 0], [1, 0, 1, 0]) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# build_matrix
# ---------------------------------------------------------------------------

def test_build_matrix_empty():
    X, y, names = build_matrix([])
    assert X.shape == (0, 0) and y.shape == (0,) and names == []


def test_build_matrix_handles_missing_features():
    exs = [
        TrainingExample(features={"a": 1.0, "b": 2.0}, label=1,
                        auto_decision="keep", source="eval"),
        TrainingExample(features={"a": 3.0}, label=0,
                        auto_decision="cut", source="eval"),
    ]
    X, y, names = build_matrix(exs)
    assert names == ["a", "b"]
    assert X.tolist() == [[1.0, 2.0], [3.0, 0.0]]
    assert y.tolist() == [1.0, 0.0]


# ---------------------------------------------------------------------------
# LogisticModel save / load
# ---------------------------------------------------------------------------

def test_model_predict_proba_before_fit_returns_half():
    """Zero weights + zero bias → sigmoid(0) = 0.5. Prevents division by zero std."""
    m = LogisticModel(feature_names=["a"],
                      weights=np.zeros(1), bias=0.0,
                      feature_mean=np.zeros(1), feature_std=np.ones(1))
    assert m.predict_proba({"a": 123.0}) == pytest.approx(0.5)


def test_model_save_load_round_trip(tmp_path):
    m = LogisticModel(
        feature_names=["a", "b"],
        weights=np.array([0.7, -0.3]),
        bias=0.1,
        feature_mean=np.array([1.0, 2.0]),
        feature_std=np.array([0.5, 1.5]),
    )
    path = tmp_path / "model.json"
    m.save(path)
    loaded = LogisticModel.load(path)
    assert loaded.feature_names == ["a", "b"]
    assert loaded.weights.tolist() == [0.7, -0.3]
    assert loaded.bias == pytest.approx(0.1)
    # Prediction should match exactly.
    before = m.predict_proba({"a": 2.0, "b": 1.0})
    after = loaded.predict_proba({"a": 2.0, "b": 1.0})
    assert before == pytest.approx(after)


# ---------------------------------------------------------------------------
# fit_logistic on linearly-separable data
# ---------------------------------------------------------------------------

def test_fit_logistic_on_separable_data_converges():
    """y = 1 iff feature `x` ≥ 0.5 — logistic regression should learn this."""
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, size=(200, 1))
    y = (X[:, 0] > 0.5).astype(float)
    model = fit_logistic(X, y, feature_names=["x"], epochs=5000, lr=0.5)
    proba = model.predict_proba_matrix(X)
    preds = (proba >= 0.5).astype(int)
    acc = (preds == y.astype(int)).mean()
    assert acc > 0.95


# ---------------------------------------------------------------------------
# collect_from_eval
# ---------------------------------------------------------------------------

def _eval_result(seg_evals: list[SegmentEval]) -> EvalResult:
    return EvalResult(segments=seg_evals)


def test_collect_from_eval_skips_skip_outcomes():
    edl = EDL(
        clips=[_proxy()],
        segments=[
            _seg(0, 1, features={"a": 0.1}, decision="keep"),
            _seg(1, 2, features={"a": 0.9}, decision="cut"),
        ],
    )
    seg_evals = [
        SegmentEval(index=0, clip_id="c", start=0, end=1, duration=1,
                    auto_decision="keep", editor_kept=True,
                    match_peak=0.9, match_position_s=0.0, outcome="TP"),
        SegmentEval(index=1, clip_id="c", start=1, end=2, duration=1,
                    auto_decision="cut", editor_kept=False,
                    match_peak=0.1, match_position_s=0.0, outcome="SKIP"),
    ]
    out = collect_from_eval(edl, _eval_result(seg_evals))
    assert len(out) == 1
    assert out[0].features == {"a": 0.1}
    assert out[0].label == 1
    assert out[0].auto_decision == "keep"


def test_collect_from_eval_copies_features():
    edl = EDL(
        clips=[_proxy()],
        segments=[_seg(0, 1, features={"a": 2.5, "b": -1.0}, decision="cut")],
    )
    seg_evals = [SegmentEval(
        index=0, clip_id="c", start=0, end=1, duration=1,
        auto_decision="cut", editor_kept=True,
        match_peak=0.9, match_position_s=0.0, outcome="FN",
    )]
    out = collect_from_eval(edl, _eval_result(seg_evals))
    assert out[0].label == 1
    assert out[0].auto_decision == "cut"
    assert out[0].features == {"a": 2.5, "b": -1.0}


# ---------------------------------------------------------------------------
# collect_from_overrides
# ---------------------------------------------------------------------------

def test_collect_from_overrides_keeps_last_decision_per_segment():
    edl = EDL(
        clips=[_proxy()],
        segments=[_seg(0, 1, features={"a": 0.5})],
    )
    t0 = datetime(2026, 1, 1, 0, 0, 0)
    rows = [
        _FakeOverrideRow(1, 0, "keep", "cut", t0),
        _FakeOverrideRow(2, 0, "cut", "keep", t0 + timedelta(seconds=1)),
        _FakeOverrideRow(3, 0, "keep", "cut", t0 + timedelta(seconds=2)),
    ]
    out = collect_from_overrides(edl, rows)
    assert len(out) == 1
    assert out[0].label == 0        # final decision was "cut"


def test_collect_from_overrides_first_row_carries_auto_decision():
    edl = EDL(clips=[_proxy()], segments=[_seg(0, 1)])
    t0 = datetime(2026, 1, 1)
    rows = [
        _FakeOverrideRow(1, 0, "keep", "cut", t0),
        _FakeOverrideRow(2, 0, "cut", "keep", t0 + timedelta(seconds=1)),
    ]
    out = collect_from_overrides(edl, rows)
    assert out[0].auto_decision == "keep"   # the pipeline's original


def test_collect_from_overrides_skips_out_of_range():
    edl = EDL(clips=[_proxy()], segments=[_seg(0, 1)])
    rows = [_FakeOverrideRow(1, 99, "keep", "cut", datetime(2026, 1, 1))]
    assert collect_from_overrides(edl, rows) == []


# ---------------------------------------------------------------------------
# train_and_evaluate — honest comparison
# ---------------------------------------------------------------------------

def _toy_examples(n: int = 100) -> list[TrainingExample]:
    """A linearly-informative feature `x`: label = 1 iff x > 0.5."""
    rng = np.random.default_rng(0)
    out: list[TrainingExample] = []
    for _ in range(n):
        x = float(rng.uniform(0, 1))
        label = 1 if x > 0.5 else 0
        # Heuristic auto_decision is a weak baseline — often wrong near 0.5.
        auto = "keep" if x > 0.6 else "cut"
        out.append(TrainingExample(
            features={"x": x}, label=label,
            auto_decision=auto, source="eval",
        ))
    return out


def test_train_and_evaluate_requires_minimum_examples():
    with pytest.raises(ValueError, match="at least 4"):
        train_and_evaluate([_toy_examples(1)[0]])


def test_train_and_evaluate_returns_split_sizes():
    exs = _toy_examples(100)
    tr = train_and_evaluate(exs, test_fraction=0.3, seed=1)
    assert tr.n_test == 30
    assert tr.n_train == 70


def test_train_and_evaluate_beats_biased_baseline():
    """Baseline auto_decision deliberately skewed → model should outperform."""
    exs = _toy_examples(200)
    tr = train_and_evaluate(exs, test_fraction=0.25, seed=0)
    assert tr.model_test_f1 >= tr.heuristic_test_f1
    assert tr.beats_baseline


def test_train_and_evaluate_model_is_saveable(tmp_path):
    exs = _toy_examples(60)
    tr = train_and_evaluate(exs, test_fraction=0.25, seed=0)
    path = tmp_path / "m.json"
    tr.model.save(path)
    loaded = LogisticModel.load(path)
    assert loaded.feature_names == ["x"]
