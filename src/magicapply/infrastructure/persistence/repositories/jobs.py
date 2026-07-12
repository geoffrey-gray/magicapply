"""SQL implementation of JobsRepository."""

from __future__ import annotations

import json

from sqlalchemy import Engine
from sqlmodel import Session, select

from magicapply.domain.models.job import Job
from magicapply.infrastructure.persistence.tables import JobRow


class SqlJobsRepository:
    """SQLite-backed JobsRepository. Satisfies the domain Protocol structurally."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------- write path -------

    def upsert(self, job: Job) -> tuple[Job, bool]:
        with Session(self._engine) as session:
            existing = session.get(JobRow, job.id)
            if existing is not None:
                # Backfill enrichment fields that were nullable when the row
                # was first written but have since been populated (e.g. an
                # older discover run stored a LinkedIn listing with no
                # apply_url; today's run resolved the external Apply link).
                # Never clobber existing non-null values — the row is
                # identity-stable by design, only enrichment can fill in.
                if existing.apply_url is None and job.apply_url:
                    existing.apply_url = job.apply_url
                    if job.raw:
                        existing.raw_json = json.dumps(job.raw)
                    session.add(existing)
                    session.commit()
                    session.refresh(existing)
                return _row_to_domain(existing), False
            row = _domain_to_row(job)
            session.add(row)
            session.commit()
            session.refresh(row)
            return _row_to_domain(row), True

    def save(self, job: Job) -> Job:
        """Overwrite enrichment fields on an existing row (apply-time resolve)."""
        with Session(self._engine) as session:
            existing = session.get(JobRow, job.id)
            if existing is None:
                row = _domain_to_row(job)
                session.add(row)
                session.commit()
                session.refresh(row)
                return _row_to_domain(row)
            existing.apply_url = job.apply_url
            existing.description = job.description
            existing.raw_json = json.dumps(job.raw or {})
            if job.location is not None:
                existing.location = job.location
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return _row_to_domain(existing)

    # ------- read path -------

    def get(self, job_id: str) -> Job | None:
        with Session(self._engine) as session:
            row = session.get(JobRow, job_id)
            return _row_to_domain(row) if row else None

    def get_by_dedup_key(self, dedup_key: str) -> Job | None:
        with Session(self._engine) as session:
            row = session.exec(select(JobRow).where(JobRow.dedup_key == dedup_key).limit(1)).first()
            return _row_to_domain(row) if row else None

    def list_all(self) -> list[Job]:
        with Session(self._engine) as session:
            rows = session.exec(select(JobRow)).all()
            return [_row_to_domain(r) for r in rows]


def _domain_to_row(job: Job) -> JobRow:
    return JobRow(
        id=job.id,
        source_name=job.source_name,
        url=job.url,
        apply_url=job.apply_url,
        dedup_key=job.dedup_key,
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
        posted_at=job.posted_at,
        discovered_at=job.discovered_at,
        raw_json=json.dumps(job.raw),
    )


def _row_to_domain(row: JobRow) -> Job:
    return Job(
        id=row.id,
        source_name=row.source_name,
        url=row.url,
        apply_url=row.apply_url,
        title=row.title,
        company=row.company,
        location=row.location,
        description=row.description,
        posted_at=row.posted_at,
        discovered_at=row.discovered_at,
        raw=json.loads(row.raw_json) if row.raw_json else {},
    )
