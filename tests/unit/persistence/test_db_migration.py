"""Schema migration patches for existing SQLite databases."""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text

from magicapply.infrastructure.persistence.db import create_engine_from_url, migrate_schema


def test_migrate_schema_adds_apply_url_to_legacy_jobs_table() -> None:
    engine = create_engine_from_url("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    dedup_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    location TEXT,
                    description TEXT NOT NULL DEFAULT '',
                    posted_at TEXT,
                    discovered_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
        )

    migrate_schema(engine)
    columns = {col["name"] for col in inspect(engine).get_columns("jobs")}
    assert "apply_url" in columns

    migrate_schema(engine)
    assert {col["name"] for col in inspect(engine).get_columns("jobs")} == columns

    engine.dispose()


def test_create_db_includes_apply_url_on_fresh_database() -> None:
    from magicapply.infrastructure.persistence.db import create_db

    engine = create_engine_from_url("sqlite:///:memory:")
    create_db(engine)
    columns = {col["name"] for col in inspect(engine).get_columns("jobs")}
    assert "apply_url" in columns
    engine.dispose()