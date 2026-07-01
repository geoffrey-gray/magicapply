"""SQLite persistence: engine, tables, repositories."""

from magicapply.infrastructure.persistence.db import create_db, create_engine_from_url
from magicapply.infrastructure.persistence.repositories.applications import (
    SqlApplicationsRepository,
)
from magicapply.infrastructure.persistence.repositories.jobs import SqlJobsRepository

__all__ = [
    "SqlApplicationsRepository",
    "SqlJobsRepository",
    "create_db",
    "create_engine_from_url",
]
