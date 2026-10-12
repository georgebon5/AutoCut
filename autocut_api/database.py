"""SQLAlchemy setup: engine factory, declarative base, session factory.

SQLite is configured in WAL mode so the UI can poll job status without
blocking background writers.
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

log = logging.getLogger("autocut_api.database")


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    """Create a SQLAlchemy engine with SQLite-friendly defaults."""
    connect_args = {}
    if url.startswith("sqlite"):
        # Needed so sessions can be used across FastAPI's threadpool workers.
        connect_args["check_same_thread"] = False

    engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def reconcile_schema(engine: Engine, base: type[DeclarativeBase]) -> None:
    """Add columns declared in the ORM but missing from the live DB.

    ``Base.metadata.create_all`` only creates missing tables, never alters
    existing ones; that silently breaks every time we add a column (we hit
    this with ``config_json``). This walks each ORM table, compares against
    the live schema, and runs ``ALTER TABLE ADD COLUMN`` for any nullable
    additions. Non-nullable additions are refused with a clear error so the
    operator wipes the DB rather than silently writing garbage.

    Scope: nullable columns + columns with a Python default. Drops, renames,
    and type changes are out of scope; a real migration tool would own those.
    """
    insp = inspect(engine)
    live_tables = set(insp.get_table_names())
    for table in base.metadata.sorted_tables:
        if table.name not in live_tables:
            continue  # create_all will handle it
        live_cols = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in live_cols:
                continue
            if not col.nullable and col.default is None and col.server_default is None:
                raise RuntimeError(
                    f"schema drift: {table.name}.{col.name} is NOT NULL with no "
                    "default — delete the DB file or run a real migration."
                )
            col_type = col.type.compile(dialect=engine.dialect)
            null_sql = "" if col.nullable else " NOT NULL"
            # Column.default may be a scalar or a callable (e.g. _utcnow_naive).
            # SQL defaults need literals; skip the callable case and let rows
            # created after the migration carry Python-side defaults.
            default_sql = ""
            if col.default is not None and getattr(col.default, "is_scalar", False):
                default_sql = f" DEFAULT {col.default.arg!r}"
            stmt = text(
                f"ALTER TABLE {table.name} ADD COLUMN {col.name} "
                f"{col_type}{null_sql}{default_sql}"
            )
            with engine.begin() as conn:
                conn.execute(stmt)
            log.warning(
                "auto-migrated: added column %s.%s (%s)",
                table.name, col.name, col_type,
            )
