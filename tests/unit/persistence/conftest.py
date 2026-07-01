"""Shared fixtures for persistence tests — in-memory SQLite per test."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from magicapply.infrastructure.persistence.db import (
    create_db,
    create_engine_from_url,
)


@pytest.fixture()
def engine() -> Iterator[Engine]:
    eng = create_engine_from_url("sqlite:///:memory:")
    create_db(eng)
    yield eng
    eng.dispose()
