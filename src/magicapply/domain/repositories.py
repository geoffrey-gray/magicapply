"""Repository Protocols — the domain's contract with persistence.

Concrete implementations live in `infrastructure/persistence/`. Domain code
should type-hint against these Protocols, never against SQLModel classes or
the concrete repos. This keeps the domain testable with fakes and the
persistence backend swappable.

Not GoF, but the canonical companion to a layered architecture (Fowler PoEAA).
"""

from __future__ import annotations

from typing import Protocol

from magicapply.domain.models.application import Application, ApplicationState
from magicapply.domain.models.job import Job


class JobsRepository(Protocol):
    def upsert(self, job: Job) -> tuple[Job, bool]:
        """Insert if new (by id) or return existing. Returns (job, was_new)."""

    def get(self, job_id: str) -> Job | None: ...

    def get_by_dedup_key(self, dedup_key: str) -> Job | None:
        """Find a job by cross-URL identity (company + normalized title)."""

    def list_all(self) -> list[Job]: ...


class ApplicationsRepository(Protocol):
    def add(self, app: Application) -> Application:
        """Insert a new Application. Raises if the id already exists."""

    def save(self, app: Application) -> None:
        """Persist mutations to an existing Application."""

    def get(self, app_id: str) -> Application | None: ...

    def by_job_and_profile(self, job_id: str, profile_name: str) -> Application | None:
        """One profile has at most one Application per Job."""

    def list_by_state(self, state: ApplicationState) -> list[Application]: ...
