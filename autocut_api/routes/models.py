"""Model management endpoints.

Admin workflow: train a LogisticModel offline with ``autocut train``, then
``POST /models`` to deploy it. The server validates the JSON (loads it as a
LogisticModel to make sure the shape is right), writes atomically to
``cfg.resolved_model_path`` via tempfile + rename, and every subsequent
``POST /jobs`` picks it up.

No auth here — local-first spec. If the API ever gets fronted by nginx
for public access, gate ``POST /models`` and ``DELETE`` behind that layer.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from pydantic import BaseModel

from autocut.trainer import LogisticModel
from autocut_api.config import ApiConfig
from autocut_api.dependencies import get_config

router = APIRouter(prefix="/models", tags=["models"])


class ActiveModelInfo(BaseModel):
    path: str
    n_features: int
    bias: float
    updated_at: datetime
    size_bytes: int


def _load_or_400(data: dict) -> LogisticModel:
    """Validate uploaded JSON by instantiating a LogisticModel from it.

    LogisticModel.load reads from a file, so we write to a tempfile first
    and let it do the schema check. Any key/shape mismatch becomes a 400.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8",
    ) as tmp:
        import json as _json
        _json.dump(data, tmp)
        tmp_path = Path(tmp.name)
    try:
        return LogisticModel.load(tmp_path)
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(
            status_code=400,
            detail=f"invalid model: {type(e).__name__}: {e}",
        ) from e
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("", response_model=ActiveModelInfo,
             status_code=status.HTTP_201_CREATED)
def upload_model(
    body: dict = Body(..., description="LogisticModel JSON produced by `autocut train`."),
    cfg: ApiConfig = Depends(get_config),
) -> ActiveModelInfo:
    model = _load_or_400(body)

    target = cfg.resolved_model_path
    target.parent.mkdir(parents=True, exist_ok=True)

    # Atomic swap: write beside the target, then rename. Guarantees any
    # concurrent job either sees the old model or the new one, never a
    # half-written file.
    import json as _json
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tmp", dir=str(target.parent),
        delete=False, encoding="utf-8",
    ) as tmp:
        _json.dump(body, tmp, indent=2)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, target)

    stat = target.stat()
    return ActiveModelInfo(
        path=str(target),
        n_features=len(model.feature_names),
        bias=float(model.bias),
        updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        size_bytes=stat.st_size,
    )


@router.get("/active", response_model=ActiveModelInfo)
def get_active_model(cfg: ApiConfig = Depends(get_config)) -> ActiveModelInfo:
    target = cfg.resolved_model_path
    if not target.exists():
        raise HTTPException(status_code=404, detail="no active model")
    try:
        model = LogisticModel.load(target)
    except Exception as e:   # noqa: BLE001 — file exists but is unloadable
        raise HTTPException(
            status_code=500,
            detail=f"active model file is unreadable: {e}",
        ) from e
    stat = target.stat()
    return ActiveModelInfo(
        path=str(target),
        n_features=len(model.feature_names),
        bias=float(model.bias),
        updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
        size_bytes=stat.st_size,
    )


@router.delete("/active", status_code=status.HTTP_204_NO_CONTENT)
def delete_active_model(cfg: ApiConfig = Depends(get_config)) -> Response:
    target = cfg.resolved_model_path
    if not target.exists():
        raise HTTPException(status_code=404, detail="no active model")
    target.unlink()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
