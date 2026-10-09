"""Database models — Job tracks a single pipeline run from upload to render."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from autocut_api.database import Base


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow_naive() -> datetime:
    """UTC now as a naive datetime — SQLite strips tz on read anyway, so we
    store naive-UTC and treat all persisted datetimes as UTC by convention."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Valid status transitions: pending → running → done | failed
JOB_STATUSES = ("pending", "running", "done", "failed")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)

    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    stage: Mapped[str] = mapped_column(String(32), default="created", nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Human-readable error if the job entered the failed state.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Workspace directory for this job's proxies, EDLs and renders. Set once
    # by the API when the job is created.
    workspace: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow_naive, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow_naive, onupdate=_utcnow_naive, nullable=False
    )
