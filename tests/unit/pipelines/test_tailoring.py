"""Tests for TailoringPipeline end-to-end with in-memory deps + tmp data_dir."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Engine

from magicapply.config.models import KeywordBank, PromptsConfig
from magicapply.domain.keywords.extractor import KeywordExtractor
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
from magicapply.infrastructure.rendering.docx_inplace import InPlaceDocxTailorer
from magicapply.pipelines.tailoring import TailoringPipeline


def _make_test_docx(path: Path) -> Path:
    """Minimal DOCX with one paragraph so the in-place tailorer has runs
    to walk (or byte-copy when no swaps apply)."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Test Person")
    doc.add_paragraph("Backend engineer building distributed systems.")
    doc.save(str(path))
    return path


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
    generate_cover_letter: bool = True,
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
    source_docx = _make_test_docx(tmp_path / "source.docx")
    pipeline = TailoringPipeline(
        apps_repo=apps_repo,
        jobs_repo=jobs_repo,
        tailorer=tailorer,
        narrative=narrative,
        resume_renderer=InPlaceDocxTailorer(),
        keyword_extractor=KeywordExtractor(llm, extraction_prompt=""),
        keyword_bank=KeywordBank(),
        source_docx_path=source_docx,
        profile_name=profile_name,
        data_dir=tmp_path,
        generate_cover_letter=generate_cover_letter,
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


class TestPhase1DeferCoverLetter:
    """W.2: cover letter defer is kwarg-gated, not deleted.

    Phase 1 default (`generate_cover_letter=False` in composition) skips
    the NarrativeEngine.cover_letter call and does not write
    cover_letter.md. Phase 2 flips the flag and the letter reappears.
    """

    def test_default_false_skips_cover_letter_file(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        pipeline, apps_repo, jobs_repo = _pipeline(
            engine, tmp_path, generate_cover_letter=False
        )
        _, app = _seed_scored_app(jobs_repo, apps_repo)

        report = pipeline.run()
        assert report.tailored == 1

        reloaded = apps_repo.get(app.id)
        assert reloaded is not None
        assert reloaded.tailored_path is not None

        app_dir = Path(reloaded.tailored_path)
        assert (app_dir / "resume.yaml").exists()
        assert (app_dir / "resume.docx").exists()
        assert not (app_dir / "cover_letter.md").exists()

    def test_explicit_true_still_writes_cover_letter(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        # Phase 2 wire-up path: setting the flag True reinstates the
        # NarrativeEngine call end-to-end without any other change.
        pipeline, apps_repo, jobs_repo = _pipeline(
            engine, tmp_path, generate_cover_letter=True
        )
        _, app = _seed_scored_app(jobs_repo, apps_repo)

        report = pipeline.run()
        assert report.tailored == 1

        reloaded = apps_repo.get(app.id)
        assert reloaded is not None
        assert (Path(reloaded.tailored_path) / "cover_letter.md").exists()


class TestSingleJob:
    def test_run_with_job_id_tailors_only_that_app(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        pipeline, apps_repo, jobs_repo = _pipeline(engine, tmp_path)
        _, app_a = _seed_scored_app(jobs_repo, apps_repo, url="https://acme.com/j/1")
        _, app_b = _seed_scored_app(
            jobs_repo, apps_repo, url="https://acme.com/j/2"
        )

        report = pipeline.run(job_id=app_a.job_id)

        assert report.tailored == 1
        reloaded_a = apps_repo.get(app_a.id)
        reloaded_b = apps_repo.get(app_b.id)
        assert reloaded_a is not None and reloaded_a.state is ApplicationState.TAILORED
        assert reloaded_b is not None and reloaded_b.state is ApplicationState.SCORED

    def test_run_with_unknown_job_id_reports_error(
        self, engine: Engine, tmp_path: Path
    ) -> None:
        pipeline, apps_repo, jobs_repo = _pipeline(engine, tmp_path)
        _seed_scored_app(jobs_repo, apps_repo)

        report = pipeline.run(job_id="nonexistent00000")

        assert report.tailored == 0
        assert len(report.errors) == 1
        assert "nonexistent00000" in report.errors[0]


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
