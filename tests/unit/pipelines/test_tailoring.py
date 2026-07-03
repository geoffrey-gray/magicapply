"""Tests for TailoringPipeline end-to-end with in-memory deps + tmp data_dir."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Engine

from magicapply.config.models import PromptsConfig
from magicapply.domain.models.application import Application, ApplicationState
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import BaseResume, TailoredResume
from magicapply.domain.resumes.narrative import NarrativeEngine
from magicapply.domain.resumes.tailor import Tailorer
from magicapply.infrastructure.llm.providers.mock import MockLLMClient
from magicapply.infrastructure.persistence.db import create_db, create_engine_from_url
from magicapply.infrastructure.persistence.repositories.applications import (
    SqlApplicationsRepository,
)
from magicapply.infrastructure.persistence.repositories.jobs import SqlJobsRepository
from magicapply.infrastructure.rendering.docx import DocxResumeRenderer
from magicapply.pipelines.tailoring import TailoringPipeline

_TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "configs" / "resume_template.docx"


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine_from_url("sqlite:///:memory:")
    create_db(eng)
    yield eng
    eng.dispose()


def _prompts() -> PromptsConfig:
    return PromptsConfig(
        scoring="score the job",
        summary="rewrite the resume summary",
        cover_letter="write a cover letter that sounds like the candidate",
        answer="answer the screening question",
    )


def _base() -> BaseResume:
    return BaseResume(
        name="Test Person",
        email="t@example.com",
        summary="Backend engineer.",
    )


def _pipeline(
    engine: Engine,
    tmp_path: Path,
    *,
    profile_name: str = "senior-swe",
) -> tuple[TailoringPipeline, SqlApplicationsRepository, SqlJobsRepository]:
    jobs_repo = SqlJobsRepository(engine)
    apps_repo = SqlApplicationsRepository(engine)
    prompts = _prompts()
    # Same client serves both — shape-aware mode picks the right response
    # per prompt shape (summary vs cover letter).
    llm = MockLLMClient(prompts=prompts)
    tailorer = Tailorer(llm, _base(), summary_prompt=prompts.summary)
    narrative = NarrativeEngine(
        llm,
        _base(),
        cover_letter_prompt=prompts.cover_letter,
        answer_prompt=prompts.answer,
    )
    pipeline = TailoringPipeline(
        apps_repo=apps_repo,
        jobs_repo=jobs_repo,
        tailorer=tailorer,
        narrative=narrative,
        resume_renderer=DocxResumeRenderer(_TEMPLATE_PATH),
        profile_name=profile_name,
        data_dir=tmp_path,
    )
    return pipeline, apps_repo, jobs_repo


def _seed_scored_app(
    jobs_repo: SqlJobsRepository,
    apps_repo: SqlApplicationsRepository,
    *,
    profile: str = "senior-swe",
    title: str = "Senior Backend Engineer",
    company: str = "Acme",
    url: str = "https://acme.com/j/1",
) -> tuple[Job, Application]:
    job = Job.new(
        source_name="s",
        url=url,
        title=title,
        company=company,
        description="Python and distributed systems.",
    )
    jobs_repo.upsert(job)
    app = Application(job_id=job.id, profile_name=profile, score=82)
    app.transition_to(ApplicationState.SCORED, reason="test")
    apps_repo.add(app)
    return job, app


class TestHappyPath:
    def test_scored_app_becomes_tailored_with_artifacts(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        pipeline, apps_repo, jobs_repo = _pipeline(engine, tmp_path)
        _, app = _seed_scored_app(jobs_repo, apps_repo)

        report = pipeline.run()

        assert report.tailored == 1
        assert report.missing_job == 0
        assert report.errors == []

        reloaded = apps_repo.get(app.id)
        assert reloaded is not None
        assert reloaded.state is ApplicationState.TAILORED
        assert reloaded.tailored_path is not None

        app_dir = Path(reloaded.tailored_path)
        assert app_dir.exists()
        assert (app_dir / "resume.yaml").exists()
        assert (app_dir / "cover_letter.md").exists()
        # Rendered DOCX sits alongside the source artifacts for ATS upload.
        docx = app_dir / "resume.docx"
        assert docx.exists()
        assert docx.read_bytes()[:4] == b"PK\x03\x04"

    def test_resume_yaml_parses_as_tailored_resume(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        pipeline, apps_repo, jobs_repo = _pipeline(engine, tmp_path)
        _, app = _seed_scored_app(jobs_repo, apps_repo)

        pipeline.run()
        reloaded = apps_repo.get(app.id)
        assert reloaded is not None
        assert reloaded.tailored_path is not None

        payload = yaml.safe_load(
            (Path(reloaded.tailored_path) / "resume.yaml").read_text()
        )
        tailored = TailoredResume.model_validate(payload)
        assert tailored.name == "Test Person"
        assert tailored.job_id == app.job_id

    def test_cover_letter_has_content(self, engine: Engine, tmp_path: Path) -> None:
        pipeline, apps_repo, jobs_repo = _pipeline(engine, tmp_path)
        _, app = _seed_scored_app(jobs_repo, apps_repo)

        pipeline.run()
        reloaded = apps_repo.get(app.id)
        assert reloaded is not None
        assert reloaded.tailored_path is not None

        cover = (Path(reloaded.tailored_path) / "cover_letter.md").read_text()
        # Shape-aware mock injects the parsed job title + company into the letter.
        assert "Senior Backend Engineer" in cover
        assert "Acme" in cover


class TestIdempotence:
    def test_second_run_does_nothing(self, engine: Engine, tmp_path: Path) -> None:
        pipeline, apps_repo, jobs_repo = _pipeline(engine, tmp_path)
        _seed_scored_app(jobs_repo, apps_repo)

        first = pipeline.run()
        second = pipeline.run()

        assert first.tailored == 1
        assert second.tailored == 0  # already TAILORED, not returned by query


class TestProfileIsolation:
    def test_other_profile_not_touched(self, engine: Engine, tmp_path: Path) -> None:
        pipeline, apps_repo, jobs_repo = _pipeline(engine, tmp_path)  # profile senior-swe
        _seed_scored_app(jobs_repo, apps_repo, profile="senior-swe")
        _, other = _seed_scored_app(
            jobs_repo,
            apps_repo,
            profile="junior-swe",
            url="https://acme.com/j/2",
        )

        report = pipeline.run()

        assert report.tailored == 1
        # The other-profile app is still SCORED, untouched by this pipeline.
        other_reloaded = apps_repo.get(other.id)
        assert other_reloaded is not None
        assert other_reloaded.state is ApplicationState.SCORED
        assert other_reloaded.tailored_path is None


class TestMissingJob:
    def test_missing_job_is_reported_and_skipped(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        from sqlmodel import Session, delete

        from magicapply.infrastructure.persistence.tables import JobRow

        pipeline, apps_repo, jobs_repo = _pipeline(engine, tmp_path)
        # Real one first.
        _seed_scored_app(jobs_repo, apps_repo)
        # Second one, but we delete the job row after adding the application —
        # simulates data corruption / a job pruned from underneath a live app.
        _, ghost = _seed_scored_app(
            jobs_repo,
            apps_repo,
            url="https://acme.com/j/gone",
        )
        with Session(engine) as session:
            session.exec(delete(JobRow).where(JobRow.id == ghost.job_id))
            session.commit()

        report = pipeline.run()

        # Real app tailored; ghost skipped and reported.
        assert report.tailored == 1
        assert report.missing_job == 1
