"""SQLModel tables — persistence shape, distinct from domain shape.

Repositories map between these rows and the Pydantic domain models. Keeping
them separate is intentional: it lets the domain evolve independently of
schema, and prevents ORM-specific fields (relationships, joined-load knobs)
from bleeding into pure domain code.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class JobRow(SQLModel, table=True):
    __tablename__ = "jobs"

    id: str = Field(primary_key=True)
    source_name: str = Field(index=True)
    url: str = Field(index=True)
    dedup_key: str = Field(index=True)
    title: str
    company: str
    location: str | None = None
    description: str = ""
    posted_at: datetime | None = None
    discovered_at: datetime
    raw_json: str = "{}"


class ApplicationRow(SQLModel, table=True):
    __tablename__ = "applications"

    id: str = Field(primary_key=True)
    job_id: str = Field(index=True, foreign_key="jobs.id")
    profile_name: str = Field(index=True)
    state: str = Field(index=True)
    score: int | None = None
    score_rationale: str | None = None
    error: str | None = None
    attempts: int = 0
    discovered_at: datetime
    updated_at: datetime
    history_json: str = "[]"
