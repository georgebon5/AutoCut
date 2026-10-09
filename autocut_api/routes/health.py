"""Liveness probe — returns version so clients can detect API upgrades."""

from __future__ import annotations

from fastapi import APIRouter

from autocut_api import __version__
from autocut_api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
