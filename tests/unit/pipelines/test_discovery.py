"""Tests for DiscoveryPipeline end-to-end with in-memory deps."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from magicapply.config.models import ScoringConfig
from magicapply.domain.jobs.scoring import JobScorer, LLMScorer, Prefilter
from magicapply.domain.models.application import ApplicationState
from magicapply.domain.models.job import Job
from magicapply.infrastructure.llm.providers.mock import MockLLMClient
from magicapply.infrastructure.persistence.db import create_db, create_engine_from_url
from magicapply.infrastructure.persistence.repositories.applications import (
    SqlApplicationsRepository,
)
from magicapply.infrastructure.persistence.repositories.jobs import SqlJobsRepository
from magicapply.infrastructure.sources.base import SourceError
from magicapply.pipelines.discovery import DiscoveryPipeline


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine_from_url("sqlite:///:memory:")
    create_db(eng)
    yield eng
    eng.dispose()


class _StubSource:
    def __init__(self, name: str, jobs: list[Job]) -> None:
        self.name = name
        self._jobs = jobs

    def discover(self) -> Iterator[Job]:
        yield from self._jobs


class _ErrorSource:
    name = "broken"

    def discover(self) -> Iterator[Job]:
        raise SourceError("boom")
        yield  # pragma: no cover


def _job(title: str, company: str, url: str) -> Job:
    return Job.new(source_name="s", url=url, title=title, company=company, description="python")


def _pipeline(
    engine: Engine,
    sources,
    *,
    threshold: int = 70,
    response: str = '{"score": 85, "rationale": "ok"}',
) -> DiscoveryPipeline:
    jobs_repo = SqlJobsRepository(engine)
    apps_repo = SqlApplicationsRepository(engine)
    scorer = JobScorer(
        prefilter=Prefilter(ScoringConfig(threshold=threshold)),
        llm_scorer=LLMScorer(MockLLMClient([response] * 10), base_resume_text="R"),
    )
    return DiscoveryPipeline(
        sources=sources,
        jobs_repo=jobs_repo,
        applications_repo=apps_repo,
        scorer=scorer,
        profile_name="test-profile",
        score_threshold=threshold,
    )


class TestHappyPath:
    def test_discovers_and_scores_new_jobs(self, engine: Engine) -> None:
        jobs = [
            _job("Senior SWE", "Acme", "https://acme.com/1"),
            _job("Staff Eng", "Beta", "https://beta.com/2"),
        ]
        src = _StubSource("s", jobs)
        report = _pipeline(engine, [src]).run()

        assert report.discovered == 2
        assert report.scored == 2
        assert report.rejected_by_threshold == 0

    def test_second_run_skips_already_seen(self, engine: Engine) -> None:
        jobs = [_job("SWE", "A", "https://a.com/1")]
        p1 = _pipeline(engine, [_StubSource("s", jobs)])
        p1.run()

        p2 = _pipeline(engine, [_StubSource("s", jobs)])
        report = p2.run()
        assert report.discovered == 0
        assert report.already_seen == 1

    def test_below_threshold_rejected(self, engine: Engine) -> None:
        jobs = [_job("SWE", "A", "https://a.com/1")]
        report = _pipeline(
            engine,
            [_StubSource("s", jobs)],
            threshold=90,
            response='{"score": 40, "rationale": "meh"}',
        ).run()
        assert report.scored == 1
        assert report.rejected_by_threshold == 1

        apps_repo = SqlApplicationsRepository(engine)
        rejected = apps_repo.list_by_state(ApplicationState.REJECTED)
        assert len(rejected) == 1


class TestErrors:
    def test_source_error_recorded_not_fatal(self, engine: Engine) -> None:
        good_jobs = [_job("SWE", "A", "https://a.com/1")]
        report = _pipeline(engine, [_ErrorSource(), _StubSource("good", good_jobs)]).run()

        assert report.discovered == 1
        assert len(report.source_errors) == 1
        assert "boom" in report.source_errors[0]


class TestDedup:
    def test_cross_source_dedup_within_run(self, engine: Engine) -> None:
        # Same job posted at two URLs — dedup_key matches.
        a = _job("Senior SWE", "Acme", "https://acme.com/1")
        b = _job("senior swe", "acme", "https://linkedin.com/jobs/view/9")
        src1 = _StubSource("s1", [a])
        src2 = _StubSource("s2", [b])
        report = _pipeline(engine, [src1, src2]).run()

        assert report.discovered == 1  # only one persisted despite two sources
