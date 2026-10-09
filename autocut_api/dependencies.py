"""FastAPI dependencies: database session and config access."""

from __future__ import annotations

from typing import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from autocut_api.config import ApiConfig


def get_db(request: Request) -> Iterator[Session]:
    """Yield a SQLAlchemy session bound to the app-level factory.

    The session is closed when the request ends; writes are committed by the
    route handler explicitly (standard Unit-of-Work pattern).
    """
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


def get_config(request: Request) -> ApiConfig:
    return request.app.state.config
