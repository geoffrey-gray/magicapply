"""SQL implementation of ApplicationsRepository."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from magicapply.domain.models.application import (
    Application,
    ApplicationState,
    StateTransition,
)
from magicapply.infrastructure.persistence.tables import ApplicationRow, JobRow


class DuplicateApplication(Exception):
    """Raised when trying to insert an Application whose id already exists."""


class SqlApplicationsRepository:
    """SQLite-backed ApplicationsRepository."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------- write path -------

    def add(self, app: Application) -> Application:
        with Session(self._engine) as session:
            row = _domain_to_row(app)
            session.add(row)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise DuplicateApplication(app.id) from exc
            session.refresh(row)
            return _row_to_domain(row)

    def save(self, app: Application) -> None:
        with Session(self._engine) as session:
            row = session.get(ApplicationRow, app.id)
            if row is None:
                raise KeyError(f"unknown application: {app.id}")
            _copy_domain_into_row(app, row)
            session.add(row)
            session.commit()

    # ------- read path -------

    def get(self, app_id: str) -> Application | None:
        with Session(self._engine) as session:
            row = session.get(ApplicationRow, app_id)
            return _row_to_domain(row) if row else None

    def by_job_and_profile(self, job_id: str, profile_name: str) -> Application | None:
        with Session(self._engine) as session:
            row = session.exec(
                select(ApplicationRow)
                .where(ApplicationRow.job_id == job_id)
                .where(ApplicationRow.profile_name == profile_name)
                .limit(1)
            ).first()
            return _row_to_domain(row) if row else None

    def list_by_state(self, state: ApplicationState) -> list[Application]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(ApplicationRow).where(ApplicationRow.state == state.value)
            ).all()
            return [_row_to_domain(r) for r in rows]

    def list_by_state_and_profile(
        self,
        state: ApplicationState,
        profile_name: str,
    ) -> list[Application]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(ApplicationRow)
                .where(ApplicationRow.state == state.value)
                .where(ApplicationRow.profile_name == profile_name)
            ).all()
            return [_row_to_domain(r) for r in rows]

    def count_applied_all_in_window(self, since: datetime) -> int:
        """Count APPLIED rows updated after `since`. Used by the global
        throttle. Cheap — indexed state + timestamp scan, no join."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(ApplicationRow.id)
                .where(ApplicationRow.state == ApplicationState.APPLIED.value)
                .where(ApplicationRow.updated_at > since)
            ).all()
            return len(rows)

    def count_applied_by_source_in_window(
        self,
        source_name: str,
        since: datetime,
    ) -> int:
        """Count APPLIED rows updated after `since` whose linked Job's
        `source_name` matches. Used by the load-balanced dedup scorer in
        `DiscoveryPipeline` so future apply attempts distribute across
        LinkedIn / Indeed / Glassdoor / etc."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(ApplicationRow.id)
                .join(JobRow, JobRow.id == ApplicationRow.job_id)
                .where(ApplicationRow.state == ApplicationState.APPLIED.value)
                .where(ApplicationRow.updated_at > since)
                .where(JobRow.source_name == source_name)
            ).all()
            return len(rows)

    def count_applied_in_window(
        self,
        ats_key_fn: Callable[[str], str | None],
        ats: str,
        since: datetime,
    ) -> int:
        """Count APPLIED rows updated after `since` whose linked Job's
        `ats_key_fn(url)` equals `ats`.

        The ATS is not stored on `ApplicationRow` — it's derived at query
        time from the linked Job's `apply_url or url` via the caller-
        supplied `ats_key_fn` (composition wires `ATSHandlerFactory`
        here). This keeps the ATS mapping in one place instead of
        shadowed as a denormalised column.

        Scans in Python because SQLite doesn't carry the ATS regex
        knowledge. Small scale (< N/hour typical) makes this fine."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(ApplicationRow, JobRow)
                .join(JobRow, JobRow.id == ApplicationRow.job_id)
                .where(ApplicationRow.state == ApplicationState.APPLIED.value)
                .where(ApplicationRow.updated_at > since)
            ).all()
        count = 0
        for _app_row, job_row in rows:
            key = ats_key_fn(job_row.apply_url or job_row.url)
            if key == ats:
                count += 1
        return count


def _domain_to_row(app: Application) -> ApplicationRow:
    return ApplicationRow(
        id=app.id,
        job_id=app.job_id,
        profile_name=app.profile_name,
        state=app.state.value,
        score=app.score,
        score_rationale=app.score_rationale,
        error=app.error,
        attempts=app.attempts,
        tailored_path=app.tailored_path,
        dry_run=app.dry_run,
        discovered_at=app.discovered_at,
        updated_at=app.updated_at,
        history_json=_dump_history(app.history),
    )


def _copy_domain_into_row(app: Application, row: ApplicationRow) -> None:
    row.state = app.state.value
    row.score = app.score
    row.score_rationale = app.score_rationale
    row.error = app.error
    row.attempts = app.attempts
    row.tailored_path = app.tailored_path
    row.dry_run = app.dry_run
    row.updated_at = app.updated_at
    row.history_json = _dump_history(app.history)


def _row_to_domain(row: ApplicationRow) -> Application:
    return Application(
        id=row.id,
        job_id=row.job_id,
        profile_name=row.profile_name,
        state=ApplicationState(row.state),
        score=row.score,
        score_rationale=row.score_rationale,
        error=row.error,
        attempts=row.attempts,
        tailored_path=row.tailored_path,
        dry_run=row.dry_run,
        discovered_at=row.discovered_at,
        updated_at=row.updated_at,
        history=_load_history(row.history_json),
    )


def _dump_history(history: list[StateTransition]) -> str:
    return json.dumps([h.model_dump(mode="json") for h in history])


def _load_history(raw: str) -> list[StateTransition]:
    if not raw:
        return []
    return [StateTransition.model_validate(item) for item in json.loads(raw)]
