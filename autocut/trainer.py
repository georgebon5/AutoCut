"""Train a lightweight classifier that replaces the hand-tuned heuristic weights.

The spec calls for LightGBM; this file implements a pure-numpy logistic
regression trainer with the same contract, so switching the inner estimator
is a drop-in change once the LightGBM runtime is available in production.
LogisticModel is deliberately small (JSON-serialisable, no sklearn dep) and
good enough to beat the heuristic baseline on linearly-informative features.

Training data flows in from two sources, both honest:
  * `collect_from_eval` — uses `editor_kept` labels from audio-matching
    against a published cut (ground truth driven by the actual editor).
  * `collect_from_overrides` — uses the user's final keep/cut per segment
    from the SegmentOverride log.

Comparison against the baseline is apples-to-apples on the same held-out
split: both the heuristic (segment.auto_decision) and the trained model
predict the same examples, F1 side-by-side.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from autocut.eval import EvalResult
from autocut.models import EDL


# ---------------------------------------------------------------------------
# Training examples
# ---------------------------------------------------------------------------

@dataclass
class TrainingExample:
    features: dict[str, float]
    label: int                      # 1 = editor/user kept, 0 = cut
    auto_decision: str              # pipeline's prediction ("keep"|"cut")
    source: str                     # "eval" | "user"


def collect_from_eval(edl: EDL, eval_result: EvalResult) -> list[TrainingExample]:
    """Convert per-segment eval outcomes into labelled examples."""
    out: list[TrainingExample] = []
    for s in eval_result.segments:
        if s.outcome == "SKIP":
            continue
        seg = edl.segments[s.index]
        out.append(TrainingExample(
            features=_floats(seg.features),
            label=1 if s.editor_kept else 0,
            # SegmentEval carries the pipeline's keep/cut as auto_decision —
            # Segment itself has only the (possibly user-overridden) decision.
            auto_decision=s.auto_decision,
            source="eval",
        ))
    return out


def collect_from_override_rows(
    overrides: Iterable[Any],       # SegmentOverride-like rows (with features_json)
) -> list[TrainingExample]:
    """Build training examples directly from SegmentOverride DB rows.

    Unlike ``collect_from_overrides``, this does NOT require the EDL — the
    DB row is self-contained: ``features_json`` holds the snapshot of segment
    features taken at override time.  Multiple flips for the same segment are
    resolved to the LAST decision (the one the editor shipped with).
    """
    import json as _json

    rows = sorted(overrides, key=lambda r: (r.created_at, r.id))
    first_auto: dict[tuple, str] = {}
    final_decision: dict[tuple, str] = {}
    last_features: dict[tuple, dict] = {}

    for r in rows:
        key = (r.job_id, r.preset, r.segment_index)
        first_auto.setdefault(key, r.previous_decision)
        final_decision[key] = r.new_decision
        try:
            last_features[key] = _json.loads(r.features_json) if r.features_json else {}
        except Exception:   # noqa: BLE001
            last_features[key] = {}

    out: list[TrainingExample] = []
    for key, decision in final_decision.items():
        features = _floats(last_features.get(key, {}))
        if not features:
            continue
        out.append(TrainingExample(
            features=features,
            label=1 if decision == "keep" else 0,
            auto_decision=first_auto.get(key, decision),
            source="user",
        ))
    return out


def collect_from_overrides(
    edl: EDL,
    overrides: Iterable[Any],       # SegmentOverride-like rows
) -> list[TrainingExample]:
    """Build examples from the SegmentOverride log.

    If the same segment is toggled multiple times, we keep the LAST decision
    (ordered by ``created_at``) — that's what the editor ultimately shipped
    with. ``previous_decision`` of the first row carries the auto baseline.
    """
    rows = sorted(overrides, key=lambda r: (r.created_at, r.id))
    first_auto: dict[int, str] = {}
    final_label: dict[int, str] = {}
    for r in rows:
        first_auto.setdefault(r.segment_index, r.previous_decision)
        final_label[r.segment_index] = r.new_decision

    out: list[TrainingExample] = []
    for idx, decision in final_label.items():
        if idx < 0 or idx >= len(edl.segments):
            continue
        seg = edl.segments[idx]
        out.append(TrainingExample(
            features=_floats(seg.features),
            label=1 if decision == "keep" else 0,
            auto_decision=first_auto.get(idx, seg.decision),
            source="user",
        ))
    return out


def _floats(d: dict) -> dict[str, float]:
    """Coerce feature values to float; skip entries that can't be numeric."""
    out: dict[str, float] = {}
    for k, v in d.items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def build_matrix(
    examples: Sequence[TrainingExample],
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Return (X, y, feature_names). Missing features default to 0.0."""
    if not examples:
        return np.zeros((0, 0)), np.zeros((0,)), []
    names = sorted({k for e in examples for k in e.features})
    X = np.array(
        [[e.features.get(n, 0.0) for n in names] for e in examples],
        dtype=np.float64,
    )
    y = np.array([e.label for e in examples], dtype=np.float64)
    return X, y, names


# ---------------------------------------------------------------------------
# Logistic regression (numerically stable, no scipy)
# ---------------------------------------------------------------------------

def _sigmoid(z: np.ndarray) -> np.ndarray:
    """Element-wise 1/(1+exp(-z)) that doesn't overflow at large |z|."""
    pos = z >= 0
    out = np.empty_like(z, dtype=np.float64)
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


def _standardise(X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Zero-mean / unit-variance columns. Returns (Xz, mean, std)."""
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std = np.where(std < 1e-9, 1.0, std)
    return (X - mean) / std, mean, std


@dataclass
class LogisticModel:
    """Logistic regression — JSON-serialisable, dependency-free at inference."""
    feature_names: list[str]
    # Weights are stored in STANDARDISED space; we also keep the normaliser
    # so `predict_proba` is self-contained.
    weights: np.ndarray = field(default_factory=lambda: np.zeros(0))
    bias: float = 0.0
    feature_mean: np.ndarray = field(default_factory=lambda: np.zeros(0))
    feature_std: np.ndarray = field(default_factory=lambda: np.zeros(0))

    def predict_proba_matrix(self, X: np.ndarray) -> np.ndarray:
        if X.size == 0:
            return np.zeros(0)
        Xz = (X - self.feature_mean) / self.feature_std
        return _sigmoid(Xz @ self.weights + self.bias)

    def predict_proba(self, features: dict[str, float]) -> float:
        x = np.array(
            [[features.get(n, 0.0) for n in self.feature_names]],
            dtype=np.float64,
        )
        return float(self.predict_proba_matrix(x)[0])

    def predict(self, features: dict[str, float], threshold: float = 0.5) -> int:
        return int(self.predict_proba(features) >= threshold)

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps({
            "feature_names": self.feature_names,
            "weights": self.weights.tolist(),
            "bias": self.bias,
            "feature_mean": self.feature_mean.tolist(),
            "feature_std": self.feature_std.tolist(),
        }, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "LogisticModel":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            feature_names=list(data["feature_names"]),
            weights=np.asarray(data["weights"], dtype=np.float64),
            bias=float(data["bias"]),
            feature_mean=np.asarray(data["feature_mean"], dtype=np.float64),
            feature_std=np.asarray(data["feature_std"], dtype=np.float64),
        )


def fit_logistic(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: list[str],
    *,
    lr: float = 0.1,
    epochs: int = 2000,
    l2: float = 0.01,
) -> LogisticModel:
    """Gradient-descent fit; L2 shrinks toward zero, no regularisation on bias."""
    if X.shape[0] == 0:
        return LogisticModel(feature_names=feature_names)
    Xz, mean, std = _standardise(X)
    w = np.zeros(Xz.shape[1])
    b = 0.0
    n = len(y)
    for _ in range(epochs):
        p = _sigmoid(Xz @ w + b)
        err = p - y
        gw = Xz.T @ err / n + l2 * w
        gb = float(err.mean())
        w = w - lr * gw
        b = b - lr * gb
    return LogisticModel(
        feature_names=feature_names,
        weights=w, bias=b,
        feature_mean=mean, feature_std=std,
    )


# ---------------------------------------------------------------------------
# Train + evaluate against the heuristic baseline
# ---------------------------------------------------------------------------

@dataclass
class TrainingResult:
    model: LogisticModel
    heuristic_test_f1: float
    model_test_f1: float
    model_train_f1: float
    beats_baseline: bool
    n_train: int
    n_test: int


def _f1(preds: Sequence[int], labels: Sequence[int]) -> float:
    tp = sum(1 for p, l in zip(preds, labels) if p == 1 and l == 1)
    fp = sum(1 for p, l in zip(preds, labels) if p == 1 and l == 0)
    fn = sum(1 for p, l in zip(preds, labels) if p == 0 and l == 1)
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)


def train_and_evaluate(
    examples: Sequence[TrainingExample],
    *,
    test_fraction: float = 0.25,
    seed: int = 42,
    threshold: float = 0.5,
    lr: float = 0.1,
    epochs: int = 2000,
    l2: float = 0.01,
) -> TrainingResult:
    """Honest comparison: same held-out split for heuristic and model."""
    if len(examples) < 4:
        raise ValueError(
            f"need at least 4 examples to split train/test, got {len(examples)}"
        )

    rng = random.Random(seed)
    shuffled = list(examples)
    rng.shuffle(shuffled)

    n_test = max(1, int(len(shuffled) * test_fraction))
    test = shuffled[:n_test]
    train = shuffled[n_test:]

    X_train, y_train, feat_names = build_matrix(train)
    X_test, y_test, _ = build_matrix(
        # Reuse the training feature names so dimensions line up.
        [TrainingExample(
            features={n: e.features.get(n, 0.0) for n in feat_names},
            label=e.label, auto_decision=e.auto_decision, source=e.source,
        ) for e in test]
    )

    model = fit_logistic(
        X_train, y_train, feat_names, lr=lr, epochs=epochs, l2=l2,
    )
    proba_train = model.predict_proba_matrix(X_train)
    proba_test = model.predict_proba_matrix(X_test)

    model_train_preds = (proba_train >= threshold).astype(int)
    model_test_preds = (proba_test >= threshold).astype(int)
    heuristic_test_preds = [1 if e.auto_decision == "keep" else 0 for e in test]

    model_train_f1 = _f1(model_train_preds.tolist(), y_train.astype(int).tolist())
    model_test_f1 = _f1(model_test_preds.tolist(), y_test.astype(int).tolist())
    heuristic_test_f1 = _f1(heuristic_test_preds, y_test.astype(int).tolist())

    return TrainingResult(
        model=model,
        heuristic_test_f1=heuristic_test_f1,
        model_test_f1=model_test_f1,
        model_train_f1=model_train_f1,
        beats_baseline=model_test_f1 >= heuristic_test_f1,
        n_train=len(train),
        n_test=len(test),
    )
