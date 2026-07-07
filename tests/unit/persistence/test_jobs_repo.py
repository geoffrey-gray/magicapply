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

    def test_apply_url_roundtrip(self, engine: Engine) -> None:
        repo = SqlJobsRepository(engine)
        j = _job(
            url="https://www.linkedin.com/jobs/view/123",
            apply_url="https://homedepot.wd5.myworkdayjobs.com/CareerDepot/job/1",
        )
        repo.upsert(j)
        got = repo.get(j.id)
        assert got is not None
        assert got.apply_url == j.apply_url
        assert got.url == j.url
        assert got.id == j.id

    def test_upsert_backfills_apply_url_on_existing_row(self, engine: Engine) -> None:
        """A row first stored without apply_url (pre-PR1) must accept the
        enriched URL on a later discover run — otherwise routing silently
        falls back to the listing URL forever."""
        repo = SqlJobsRepository(engine)
        # First run: LinkedIn adapter yielded the job before enrichment landed.
        stub = _job(url="https://www.linkedin.com/jobs/view/999", apply_url=None)
        repo.upsert(stub)

        # Second run: same identity, but this time enrichment resolved the
        # external Apply link.
        enriched = _job(
            url="https://www.linkedin.com/jobs/view/999",
            apply_url="https://symetra.eightfold.ai/careers/job/1",
            raw={"platform": "eightfold", "listing_url": stub.url},
        )
        assert enriched.id == stub.id  # identity is URL-hash stable
        stored, was_new = repo.upsert(enriched)
        assert was_new is False
        assert stored.apply_url == "https://symetra.eightfold.ai/careers/job/1"
        assert stored.raw.get("platform") == "eightfold"

    def test_upsert_does_not_clobber_existing_apply_url(self, engine: Engine) -> None:
        """Once apply_url is set, later runs must not overwrite it (URL rot
        should be caught by the operator, not silently corrected)."""
        repo = SqlJobsRepository(engine)
        original = _job(
            url="https://www.linkedin.com/jobs/view/888",
            apply_url="https://careers.paramount.com/job/1394334600",
        )
        repo.upsert(original)

        # A later run resolves to a different URL (e.g. LinkedIn changed the
        # redirect target).
        drifted = _job(
            url="https://www.linkedin.com/jobs/view/888",
            apply_url="https://different.example.com/job/1",
        )
        stored, _ = repo.upsert(drifted)
        assert stored.apply_url == "https://careers.paramount.com/job/1394334600"


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
