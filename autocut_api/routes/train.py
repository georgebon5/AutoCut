"""Auto-training endpoints: build a personalised keep/cut model from overrides.

The classifier learns by watching which segments the user flips after the
pipeline's initial decision.  Once enough overrides accumulate the server
trains a LogisticModel, measures whether it beats the hand-tuned heuristic
on a held-out split, and (if it does) deploys it as the active model so
future jobs are scored with the learned weights.

Endpoints
---------
POST /train          Trigger an immediate training run.
GET  /train/status   How many overrides exist, last run metrics, model active?

Auto-trigger
------------
``maybe_auto_train`` is called from ``patch_segment`` after every commit.
It fires a background thread when the override count crosses a multiple of
``AUTO_TRAIN_EVERY`` so the model quietly improves without any user action.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from autocut.trainer import (
    LogisticModel,
    TrainingResult,
    build_matrix,
    collect_from_override_rows,
    fit_logistic,
    train_and_evaluate,
)
from autocut_api.config import ApiConfig
from autocut_api.dependencies import get_config, get_db
from autocut_api.models import SegmentOverride, TrainingRun

log = logging.getLogger("autocut_api.train")

router = APIRouter(prefix="/train", tags=["train"])

# Automatically retrain every time this many NEW overrides are committed.
AUTO_TRAIN_EVERY = 10
# Minimum examples required before attempting a train run.
MIN_EXAMPLES = 4


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TrainStatus(BaseModel):
    n_overrides: int
    model_active: bool
    last_trained_at: datetime | None = None
    model_f1: float | None = None
    heuristic_f1: float | None = None
    beats_baseline: bool | None = None
    n_examples: int | None = None
    auto_train_threshold: int = AUTO_TRAIN_EVERY


class TrainResult(BaseModel):
    n_examples: int
    n_train: int
    n_test: int
    model_f1: float
    heuristic_f1: float
    beats_baseline: bool
    deployed: bool
    message: str


# ---------------------------------------------------------------------------
# Core training logic (runs in caller thread or background thread)
# ---------------------------------------------------------------------------

def _run_training(
    session_factory: sessionmaker,
    cfg: ApiConfig,
) -> TrainResult:
    """Collect overrides, train, evaluate, and deploy if better than baseline."""
    with session_factory() as session:
        rows = session.scalars(
            select(SegmentOverride).order_by(
                SegmentOverride.created_at, SegmentOverride.id,
            )
        ).all()

    examples = collect_from_override_rows(rows)
    if len(examples) < MIN_EXAMPLES:
        raise ValueError(
            f"need at least {MIN_EXAMPLES} override examples to train, "
            f"got {len(examples)}"
        )

    result: TrainingResult = train_and_evaluate(examples)
    deployed = False
    message = (
        f"model F1={result.model_test_f1:.3f} vs heuristic "
        f"F1={result.heuristic_test_f1:.3f}"
    )

    if result.beats_baseline:
        _deploy_model(result.model, cfg)
        deployed = True
        message = "deployed — " + message
    else:
        message = "not deployed (heuristic still wins) — " + message

    with session_factory() as session:
        run = TrainingRun(
            n_examples=len(examples),
            n_train=result.n_train,
            n_test=result.n_test,
            model_f1=result.model_test_f1,
            heuristic_f1=result.heuristic_test_f1,
            beats_baseline=result.beats_baseline,
            deployed=deployed,
        )
        session.add(run)
        session.commit()

    log.info("training complete: %s", message)
    return TrainResult(
        n_examples=len(examples),
        n_train=result.n_train,
        n_test=result.n_test,
        model_f1=result.model_test_f1,
        heuristic_f1=result.heuristic_test_f1,
        beats_baseline=result.beats_baseline,
        deployed=deployed,
        message=message,
    )


def _deploy_model(model: LogisticModel, cfg: ApiConfig) -> None:
    """Atomically write the model to the configured path."""
    target = cfg.resolved_model_path
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tmp", dir=str(target.parent),
        delete=False, encoding="utf-8",
    ) as tmp:
        json.dump(
            {
                "feature_names": model.feature_names,
                "weights": model.weights.tolist(),
                "bias": float(model.bias),
                "feature_mean": model.feature_mean.tolist(),
                "feature_std": model.feature_std.tolist(),
            },
            tmp, indent=2,
        )
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, target)


# ---------------------------------------------------------------------------
# Auto-trigger helper (called from patch_segment after each override commit)
# ---------------------------------------------------------------------------

def maybe_auto_train(
    override_count: int,
    session_factory: sessionmaker,
    cfg: ApiConfig,
) -> None:
    """Fire a background training run when override_count crosses a threshold.

    Runs in a daemon thread so it never blocks the HTTP response.  Errors
    are logged but not surfaced to the caller.
    """
    if override_count < MIN_EXAMPLES:
        return
    if override_count % AUTO_TRAIN_EVERY != 0:
        return

    def _bg() -> None:
        try:
            _run_training(session_factory, cfg)
        except Exception as exc:   # noqa: BLE001
            log.warning("auto-training failed: %s", exc)

    t = threading.Thread(target=_bg, daemon=True, name="autocut-train")
    t.start()
    log.info("auto-training triggered at %d overrides", override_count)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=TrainResult)
def trigger_train(
    request: Request,
    cfg: ApiConfig = Depends(get_config),
    session: Session = Depends(get_db),
) -> TrainResult:
    """Run training immediately and block until done."""
    factory: sessionmaker = request.app.state.session_factory
    try:
        return _run_training(factory, cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:   # noqa: BLE001
        log.exception("training failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/status", response_model=TrainStatus)
def train_status(
    cfg: ApiConfig = Depends(get_config),
    session: Session = Depends(get_db),
) -> TrainStatus:
    """Return override count, last training metrics, and model deployment state."""
    n_overrides = session.scalar(
        select(func.count()).select_from(SegmentOverride)
    ) or 0
    model_active = cfg.resolved_model_path.exists()

    last_run = session.scalar(
        select(TrainingRun)
        .order_by(TrainingRun.created_at.desc())
        .limit(1)
    )

    return TrainStatus(
        n_overrides=n_overrides,
        model_active=model_active,
        last_trained_at=last_run.created_at if last_run else None,
        model_f1=last_run.model_f1 if last_run else None,
        heuristic_f1=last_run.heuristic_f1 if last_run else None,
        beats_baseline=last_run.beats_baseline if last_run else None,
        n_examples=last_run.n_examples if last_run else None,
        auto_train_threshold=AUTO_TRAIN_EVERY,
    )
