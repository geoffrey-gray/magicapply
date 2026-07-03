"""Tests for `magicapply apply <job-id>`.

Uses monkeypatched PlaywrightSession to avoid needing Chromium; the FakePage
records every interaction so we can assert that the Greenhouse handler
navigated, filled the standard fields, and clicked the submit selector.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from magicapply.cli.main import app
from magicapply.domain.models.application import Application, ApplicationState
from magicapply.domain.models.job import Job
from magicapply.infrastructure.persistence.repositories.applications import (
    SqlApplicationsRepository,
)
from magicapply.infrastructure.persistence.repositories.jobs import SqlJobsRepository

runner = CliRunner()


_PROMPTS_YAML = """\
version: 1
scoring: |
  score the job.
summary: |
  rewrite the summary.
cover_letter: |
  write the cover letter.
answer: |
  answer the question.
"""


def _write_valid_repo(root: Path) -> Path:
    """Create a valid config tree; return the configs/ path."""
    (root / "configs").mkdir()
    (root / "configs" / "profiles").mkdir()
    (root / "resumes").mkdir()
    (root / "data").mkdir()
    (root / "resumes" / "swe.yaml").write_text("name: Test Person\n")
    (root / "configs" / "base_config.yaml").write_text(
        """
version: 1
static_answers:
  full_name: Test Person
  email: test@example.com
  phone: "555-0100"
  linkedin_url: https://linkedin.com/in/test
paths:
  resumes_dir: ../resumes
  data_dir: ../data
sources:
  - type: career_page
    name: acme
    urls: ["https://acme.example"]
"""
    )
    (root / "configs" / "prompts.yaml").write_text(_PROMPTS_YAML)
    (root / "configs" / "profiles" / "swe.yaml").write_text(
        "name: swe\nbase_resume: swe.yaml\nsources: [acme]\n"
    )
    return root / "configs"


def _seed_tailored(
    repo_root: Path,
    *,
    profile: str = "swe",
    url: str = "https://boards.greenhouse.io/acme/jobs/1",
) -> tuple[str, str]:
    """Insert a Job and a TAILORED Application; return (job_id, app_id)."""
    from magicapply.infrastructure.persistence.db import (
        create_db,
        create_engine_from_url,
        sqlite_url_for,
    )

    data_dir = repo_root / "data"
    engine = create_engine_from_url(sqlite_url_for(data_dir / "magicapply.sqlite3"))
    create_db(engine)
    jobs_repo = SqlJobsRepository(engine)
    apps_repo = SqlApplicationsRepository(engine)

    job = Job.new(
        source_name="acme",
        url=url,
        title="Senior SWE",
        company="Acme",
        description="Backend and distributed systems.",
    )
    jobs_repo.upsert(job)

    app = Application(job_id=job.id, profile_name=profile)
    app.transition_to(ApplicationState.SCORED, reason="test")

    # Write tailored artifacts on disk under data/tailored/<app_id>/.
    tailored_dir = data_dir / "tailored" / app.id
    tailored_dir.mkdir(parents=True, exist_ok=True)
    (tailored_dir / "resume.yaml").write_text(
        yaml.safe_dump(
            {
                "base_name": "Test Person",
                "job_id": job.id,
                "name": "Test Person",
                "email": "test@example.com",
                "summary": "Focused backend engineer.",
            }
        )
    )
    (tailored_dir / "cover_letter.md").write_text("I would like to apply to Acme.")
    # Stub docx so build_application_data does not raise on the missing file
    # (the fake page never actually opens it).
    (tailored_dir / "resume.docx").write_bytes(b"PK\x03\x04stub")
    app.tailored_path = str(tailored_dir)
    app.transition_to(ApplicationState.TAILORED, reason="test")
    apps_repo.add(app)

    engine.dispose()
    return job.id, app.id


class _FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.url: str = ""

    def goto(self, url: str) -> None:
        self.url = url
        self.calls.append(("goto", url))

    def fill(self, selector: str, value: str) -> None:
        self.calls.append(("fill", selector, value))

    def click(self, selector: str) -> None:
        self.calls.append(("click", selector))

    def content(self) -> str:
        return "<html><body>ok</body></html>"

    def set_input_files(self, selector: str, files: str) -> None:
        self.calls.append(("set_input_files", selector, files))

    def close(self) -> None:
        pass


class _FakeSession:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.entered = False

    def __enter__(self) -> _FakeSession:
        self.entered = True
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def new_page(self) -> _FakePage:
        return self._page


@pytest.fixture
def patched_session(monkeypatch: pytest.MonkeyPatch) -> _FakePage:
    """Replace PlaywrightSession in the pipeline command with a fake."""
    page = _FakePage()

    def _factory(*args: object, **kwargs: object) -> _FakeSession:
        return _FakeSession(page)

    monkeypatch.setattr(
        "magicapply.cli.commands.pipeline.PlaywrightSession",
        _factory,
    )
    return page


class TestHappyPath:
    def test_yes_submit_clicks_submit(
        self, tmp_path: Path, patched_session: _FakePage
    ) -> None:
        cfg = _write_valid_repo(tmp_path)
        job_id, _ = _seed_tailored(tmp_path)

        # Explicit --yes-submit; default is dry-run now.
        result = runner.invoke(
            app, ["apply", job_id, "--root", str(cfg), "--yes-submit"]
        )

        assert result.exit_code == 0, result.stdout
        assert ("goto", "https://boards.greenhouse.io/acme/jobs/1") in patched_session.calls
        fills = {c[1] for c in patched_session.calls if c[0] == "fill"}
        assert "#first_name" in fills
        assert "#last_name" in fills
        assert "#email" in fills
        # Real submit -> click happens.
        assert ("click", "input[type='submit']") in patched_session.calls
        assert "applied" in result.stdout

    def test_default_no_submit_skips_click_but_marks_applied(
        self, tmp_path: Path, patched_session: _FakePage
    ) -> None:
        from magicapply.domain.models.application import ApplicationState
        from magicapply.infrastructure.persistence.db import (
            create_engine_from_url,
            sqlite_url_for,
        )
        from magicapply.infrastructure.persistence.repositories.applications import (
            SqlApplicationsRepository,
        )

        cfg = _write_valid_repo(tmp_path)
        job_id, app_id = _seed_tailored(tmp_path)

        result = runner.invoke(app, ["apply", job_id, "--root", str(cfg)])

        assert result.exit_code == 0, result.stdout
        # Filled the form but did NOT click submit.
        fills = {c[1] for c in patched_session.calls if c[0] == "fill"}
        assert "#first_name" in fills
        assert ("click", "input[type='submit']") not in patched_session.calls
        assert "dry-run" in result.stdout

        # Application still transitions to APPLIED with dry_run=True.
        engine = create_engine_from_url(
            sqlite_url_for(tmp_path / "data" / "magicapply.sqlite3")
        )
        apps_repo = SqlApplicationsRepository(engine)
        reloaded = apps_repo.get(app_id)
        engine.dispose()
        assert reloaded is not None
        assert reloaded.state is ApplicationState.APPLIED
        assert reloaded.dry_run is True


class TestErrorPaths:
    def test_no_tailored_application_errors(
        self, tmp_path: Path, patched_session: _FakePage
    ) -> None:
        cfg = _write_valid_repo(tmp_path)
        # Deliberately don't seed anything.
        result = runner.invoke(app, ["apply", "abc123", "--root", str(cfg)])
        assert result.exit_code == 1
        assert "no TAILORED application" in result.stdout

    def test_ambiguous_job_across_profiles_errors(
        self, tmp_path: Path, patched_session: _FakePage
    ) -> None:
        cfg = _write_valid_repo(tmp_path)
        # Add a second profile that also has a TAILORED app for the same job.
        (tmp_path / "configs" / "profiles" / "other.yaml").write_text(
            "name: other\nbase_resume: swe.yaml\nsources: [acme]\n"
        )
        job_id, _ = _seed_tailored(tmp_path, profile="swe")
        _seed_tailored(tmp_path, profile="other")

        result = runner.invoke(app, ["apply", job_id, "--root", str(cfg)])
        assert result.exit_code == 1
        assert "ambiguous" in result.stdout

    def test_missing_job_in_db_errors(
        self, tmp_path: Path, patched_session: _FakePage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _write_valid_repo(tmp_path)
        job_id, _ = _seed_tailored(tmp_path)

        # Delete the job row under the app's feet.
        from sqlmodel import Session, delete

        from magicapply.infrastructure.persistence.db import (
            create_engine_from_url,
            sqlite_url_for,
        )
        from magicapply.infrastructure.persistence.tables import JobRow

        engine = create_engine_from_url(
            sqlite_url_for(tmp_path / "data" / "magicapply.sqlite3")
        )
        with Session(engine) as session:
            session.exec(delete(JobRow).where(JobRow.id == job_id))
            session.commit()
        engine.dispose()

        result = runner.invoke(app, ["apply", job_id, "--root", str(cfg)])
        assert result.exit_code == 1
        assert "missing from DB" in result.stdout
