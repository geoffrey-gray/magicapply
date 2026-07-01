"""Tests for SqlJobsRepository."""

from __future__ import annotations

from sqlalchemy import Engine

from magicapply.domain.models.job import Job
from magicapply.infrastructure.persistence.repositories.jobs import SqlJobsRepository


def _job(**over: object) -> Job:
    defaults: dict[str, object] = {
        "source_name": "src",
        "url": "https://acme.com/j/1",
        "title": "Senior SWE",
        "company": "Acme",
        "description": "Build things.",
    }
    defaults.update(over)
    return Job.new(**defaults)  # type: ignore[arg-type]


class TestUpsert:
    def test_new_job_inserted(self, engine: Engine) -> None:
        repo = SqlJobsRepository(engine)
        j = _job()
        stored, was_new = repo.upsert(j)
        assert was_new is True
        assert stored.id == j.id

    def test_same_id_second_call_returns_existing(self, engine: Engine) -> None:
        repo = SqlJobsRepository(engine)
        first, was_new_1 = repo.upsert(_job())
        second, was_new_2 = repo.upsert(_job())
        assert was_new_1 is True
        assert was_new_2 is False
        assert first.id == second.id

    def test_raw_json_roundtrip(self, engine: Engine) -> None:
        repo = SqlJobsRepository(engine)
        j = _job(raw={"posted_by": "bot", "tags": ["remote", "python"]})
        repo.upsert(j)
        got = repo.get(j.id)
        assert got is not None
        assert got.raw == {"posted_by": "bot", "tags": ["remote", "python"]}


class TestLookup:
    def test_get_missing_returns_none(self, engine: Engine) -> None:
        repo = SqlJobsRepository(engine)
        assert repo.get("missing") is None

    def test_get_by_dedup_key_finds_across_urls(self, engine: Engine) -> None:
        repo = SqlJobsRepository(engine)
        a = Job.new(
            source_name="s1",
            url="https://acme.com/j/1",
            title="Senior SWE",
            company="Acme",
        )
        b = Job.new(
            source_name="s2",
            url="https://linkedin.com/jobs/view/9",
            title="senior swe",
            company="acme",
        )
        repo.upsert(a)
        # Same dedup_key by design, but stored under `a.id`.
        found = repo.get_by_dedup_key(b.dedup_key)
        assert found is not None
        assert found.id == a.id

    def test_list_all(self, engine: Engine) -> None:
        repo = SqlJobsRepository(engine)
        repo.upsert(_job(url="https://acme.com/j/1", title="A"))
        repo.upsert(_job(url="https://acme.com/j/2", title="B"))
        assert len({j.id for j in repo.list_all()}) == 2
