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


class CreateUploadRequest(BaseModel):
    filename: str
    total_size: int   # bytes


class UploadResponse(BaseModel):
    """Status of a chunked upload — used by GET and POST (create) alike."""
    id: str
    filename: str
    total_size: int
    chunk_size: int
    total_chunks: int
    status: str
    received: list[int]            # chunk indexes already stored
    final_path: str | None = None


class ChunkAck(BaseModel):
    id: str
    chunk_index: int
    received: list[int]
    total_received: int
    status: str


class CompleteResponse(BaseModel):
    id: str
    status: str
    final_path: str
    total_size: int
