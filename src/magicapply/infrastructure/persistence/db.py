"""Engine + schema bootstrap for SQLite.

MVP intentionally has no migrations — schema comes from `SQLModel.metadata`
via `create_db()`. Alembic joins in Phase 2 of the roadmap when the schema
starts changing between releases.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine
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


def create_db(engine: Engine) -> None:
    """Create all known tables. Idempotent."""
    SQLModel.metadata.create_all(engine)


def sqlite_url_for(path: Path) -> str:
    """SQLite URL for a filesystem path."""
    return f"sqlite:///{path}"
