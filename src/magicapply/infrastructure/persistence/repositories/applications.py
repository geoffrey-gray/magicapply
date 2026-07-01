"""SQL implementation of ApplicationsRepository."""

from __future__ import annotations

import json

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from magicapply.domain.models.application import (
    Application,
    ApplicationState,
    StateTransition,
)
from magicapply.infrastructure.persistence.tables import ApplicationRow


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
