"""Workday hermetic E2E: real Chromium against a Workday-shaped fixture form.

Skipped when Chromium is not installed. Reuses the shared FixtureServer +
config tree from test_e2e_local.py's helper wiring — hand-seeded because
the Workday flow does not need to exercise the JSON-LD discovery path
(there is no /careers JSON-LD block pointing at Workday; the fixture's
Workday route is served in isolation).
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.chromium_util import SKIP_NO_CHROMIUM, chromium_installed
import yaml
from typer.testing import CliRunner

from magicapply.cli.main import app
from magicapply.domain.models.application import Application, ApplicationState
from magicapply.domain.models.job import Job
from magicapply.infrastructure.persistence.db import (
    create_db,
    create_engine_from_url,
    sqlite_url_for,
)
from magicapply.infrastructure.persistence.repositories.applications import (
    SqlApplicationsRepository,
)
from magicapply.infrastructure.persistence.repositories.jobs import SqlJobsRepository
from tests.integration.e2e.fixture_server import FixtureServer

runner = CliRunner()

_CHROMIUM_CACHE = Path.home() / ".cache" / "ms-playwright"


def _make_test_docx(path: Path) -> Path:
    from docx import Document

    doc = Document()
    doc.add_paragraph("E2E Workday Applicant")
    doc.save(str(path))
    return path


def _chromium_installed() -> bool:
    return chromium_installed()


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not _chromium_installed(),
        reason=SKIP_NO_CHROMIUM,
    ),
]


@pytest.fixture
def fixture_server() -> Iterator[FixtureServer]:
    server = FixtureServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


_PROMPTS_YAML = """\
version: 1
scoring: |
  Return only a JSON object with score (0-100) and rationale.
summary: |
  Rewrite the resume summary for the job.
cover_letter: |
  Write a cover letter body from the base resume.
answer: |
  Answer the screening question from the resume.
"""


def _write_configs(root: Path) -> Path:
    (root / "configs").mkdir()
    (root / "configs" / "profiles").mkdir()
    (root / "resumes").mkdir()
    (root / "data").mkdir()
    (root / "resumes" / "e2e.yaml").write_text("name: E2E Workday Applicant\n")
    (root / "configs" / "base_config.yaml").write_text(
        """
version: 1
llm:
  provider: mock
static_answers:
  full_name: E2E Applicant
  email: e2e@example.test
  phone: "555-0100"
  workday_apply_password: test123
paths:
  resumes_dir: ../resumes
  data_dir: ../data
sources:
  - type: job_url
    name: fixture
    urls: []
"""
    )
    (root / "configs" / "prompts.yaml").write_text(_PROMPTS_YAML)
    (root / "configs" / "profiles" / "e2e.yaml").write_text(
        "name: e2e\nbase_resume: e2e.yaml\nsources: [fixture]\n"
    )
    return root / "configs"


def _server_url(server: FixtureServer) -> str:
    assert server._httpd is not None
    host, port = server._httpd.server_address[:2]
    if isinstance(host, bytes):
        host = host.decode()
    return f"http://{host}:{port}"


def _seed_tailored(tmp_path: Path, workday_url: str) -> tuple[str, str]:
    """Insert a Job + TAILORED Application pointing at the fixture Workday URL."""
    data_dir = tmp_path / "data"
    engine = create_engine_from_url(sqlite_url_for(data_dir / "magicapply.sqlite3"))
    create_db(engine)
    jobs_repo = SqlJobsRepository(engine)
    apps_repo = SqlApplicationsRepository(engine)

    job = Job.new(
        source_name="fixture",
        url=workday_url,
        title="Senior Backend Engineer",
        company="Acme",
        description="Backend distributed systems with Python.",
    )
    jobs_repo.upsert(job)

    app_row = Application(job_id=job.id, profile_name="e2e")
    app_row.transition_to(ApplicationState.SCORED, reason="test-seed")

    tailored_dir = data_dir / "tailored" / app_row.id
    tailored_dir.mkdir(parents=True, exist_ok=True)
    (tailored_dir / "resume.yaml").write_text(
        yaml.safe_dump(
            {
                "base_name": "E2E Applicant",
                "job_id": job.id,
                "name": "E2E Applicant",
                "email": "e2e@example.test",
                "summary": "Focused backend engineer.",
            }
        )
    )
    (tailored_dir / "cover_letter.md").write_text("I would like to apply.")
    # Copy a valid DOCX so the Greenhouse-style resume upload succeeds.
    _make_test_docx(tailored_dir / "resume.docx")

    app_row.tailored_path = str(tailored_dir)
    app_row.transition_to(ApplicationState.TAILORED, reason="test-seed")
    apps_repo.add(app_row)
    engine.dispose()
    return job.id, app_row.id


class TestWorkdayApply:
    def test_yes_submit_hits_workday_form(
        self, tmp_path: Path, fixture_server: FixtureServer
    ) -> None:
        cfg = _write_configs(tmp_path)
        workday_url = (
            f"{_server_url(fixture_server)}/myworkdayjobs.com/senior-backend/apply"
        )
        job_id, _ = _seed_tailored(tmp_path, workday_url)

        result = runner.invoke(
            app, ["apply", job_id, "--root", str(cfg), "--yes-submit"]
        )
        assert result.exit_code == 0, result.stdout

        # Fixture server recorded exactly one submission with the Workday
        # selectors' backing name attributes populated.
        assert len(fixture_server.submissions) == 1
        form = fixture_server.submissions[0]
        assert form["first_name"] == "E2E"
        assert form["last_name"] == "Applicant"
        assert form["email"] == "e2e@example.test"
        assert form["phone"] == "555-0100"
        # Resume DOCX uploaded via data-automation-id file-upload-input-ref.
        resume_bytes = form["_files"]["resume"]
        assert resume_bytes[:4] == b"PK\x03\x04"
        assert len(resume_bytes) > 1000

    def test_default_no_submit_skips_click(
        self, tmp_path: Path, fixture_server: FixtureServer
    ) -> None:
        cfg = _write_configs(tmp_path)
        workday_url = (
            f"{_server_url(fixture_server)}/myworkdayjobs.com/senior-backend/apply"
        )
        job_id, _ = _seed_tailored(tmp_path, workday_url)

        result = runner.invoke(app, ["apply", job_id, "--root", str(cfg)])
        assert result.exit_code == 0, result.stdout
        assert "dry-run" in result.stdout.lower()
        # Zero form submissions because the dry-run guard fired.
        assert fixture_server.submissions == []
