"""FastAPI application factory.

Running the server:
    uvicorn autocut_api.main:app --reload

In tests, call ``create_app(cfg, pipeline_fn=...)`` with an isolated
ApiConfig and (optionally) a fake pipeline function so each test gets its
own tmp data directory, SQLite file, and controlled job behaviour.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from autocut_api import __version__
from autocut_api.config import ApiConfig
from autocut_api.database import Base, make_engine, make_session_factory
from autocut_api.jobs import PipelineFn, run_stub_pipeline
from autocut_api.routes import health, jobs, uploads
from autocut_api.runner import JobRunner


def create_app(
    cfg: ApiConfig | None = None,
    pipeline_fn: PipelineFn = run_stub_pipeline,
    max_workers: int = 2,
) -> FastAPI:
    cfg = cfg or ApiConfig()
    cfg.ensure_dirs()

    engine = make_engine(cfg.resolved_database_url)
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    runner = JobRunner(max_workers=max_workers)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        runner.shutdown(wait=False)

    app = FastAPI(title="AutoCut API", version=__version__, lifespan=lifespan)
    app.state.config = cfg
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.runner = runner
    app.state.pipeline_fn = pipeline_fn

    app.include_router(health.router)
    app.include_router(uploads.router)
    app.include_router(jobs.router)
    return app


# Default app used by ``uvicorn autocut_api.main:app``.
app = create_app()
