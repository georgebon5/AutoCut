"""FastAPI application factory.

Running the server:
    uvicorn autocut_api.main:app --reload

In tests, call ``create_app(cfg)`` with an isolated ApiConfig so each test
gets its own tmp data directory and SQLite file.
"""

from __future__ import annotations

from fastapi import FastAPI

from autocut_api import __version__
from autocut_api.config import ApiConfig
from autocut_api.database import Base, make_engine, make_session_factory
from autocut_api.routes import health


def create_app(cfg: ApiConfig | None = None) -> FastAPI:
    cfg = cfg or ApiConfig()
    cfg.ensure_dirs()

    engine = make_engine(cfg.resolved_database_url)
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)

    app = FastAPI(title="AutoCut API", version=__version__)
    app.state.config = cfg
    app.state.engine = engine
    app.state.session_factory = session_factory

    app.include_router(health.router)
    return app


# Default app used by ``uvicorn autocut_api.main:app``.
app = create_app()
