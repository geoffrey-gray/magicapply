"""Engine + schema bootstrap for SQLite.

MVP intentionally has no migrations — schema comes from `SQLModel.metadata`
via `create_db()`. Alembic joins in Phase 2 of the roadmap when the schema
starts changing between releases.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, inspect, text
from sqlmodel import SQLModel, create_engine

# Importing tables registers them on SQLModel.metadata so create_all sees them.
from magicapply.infrastructure.persistence import tables  # noqa: F401


def create_engine_from_url(url: str, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy Engine from a URL.

    Uses check_same_thread=False for SQLite so the same engine can be used
    across threads (Playwright will call from a worker later).
    """
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, echo=echo, connect_args=connect_args)


def migrate_schema(engine: Engine) -> None:
    """Apply lightweight, idempotent schema patches (no Alembic yet)."""
    inspector = inspect(engine)
    if not inspector.has_table("jobs"):
        return
    columns = {col["name"] for col in inspector.get_columns("jobs")}
    if "apply_url" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN apply_url TEXT"))


def create_db(engine: Engine) -> None:
    """Create all known tables and apply schema patches. Idempotent."""
    SQLModel.metadata.create_all(engine)
    migrate_schema(engine)


def sqlite_url_for(path: Path) -> str:
    """SQLite URL for a filesystem path."""
    return f"sqlite:///{path}"
