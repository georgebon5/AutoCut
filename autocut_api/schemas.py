"""Pydantic response schemas returned by the API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    status: str
    version: str


class JobResponse(BaseModel):
    """Serializable view of a Job row — matches the ORM model field-for-field."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    stage: str
    progress: float
    error: str | None
    workspace: str | None
    created_at: datetime
    updated_at: datetime
