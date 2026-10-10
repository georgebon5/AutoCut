"""Pydantic response schemas returned by the API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str


class JobResponse(BaseModel):
    """Serializable view of a Job row.

    ``config`` is the parsed JSON of ``Job.config_json`` so clients don't have
    to re-decode it; serialisation is done manually in the route handler.
    """

    id: str
    status: str
    stage: str
    progress: float
    error: str | None
    workspace: str | None
    config: dict | None
    created_at: datetime
    updated_at: datetime


class JobCreateRequest(BaseModel):
    """Kicks off a pipeline run against one or more completed uploads."""
    upload_ids: list[str]
    # Pipeline flags — all optional, consumed in Task 23 when the real
    # pipeline replaces the stub.
    preset: str | None = None
    hook: bool = False
    pacing: bool = False
    zoom: bool = False


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


class SegmentUpdate(BaseModel):
    """PATCH body for toggling a single segment's keep/cut decision."""
    decision: str   # "keep" or "cut"


class SegmentResponse(BaseModel):
    """Full segment view returned after a successful PATCH."""
    index: int
    clip_id: str
    start: float
    end: float
    duration: float
    decision: str
    decision_source: str
    interest_score: float
    reasons: list[str]
    features: dict
