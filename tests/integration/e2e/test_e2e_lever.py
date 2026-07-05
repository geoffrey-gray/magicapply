"""Lever hermetic E2E: real Chromium against a Lever-shaped fixture form."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
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
    doc.add_paragraph("E2E Lever Applicant")
    doc.save(str(path))
    return path


def _chromium_installed() -> bool:
    return _CHROMIUM_CACHE.exists() and (
        any(_CHROMIUM_CACHE.glob("chromium-*"))
        or any(_CHROMIUM_CACHE.glob("chromium_headless_shell-*"))
    )


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not _chromium_installed(),
        reason="Chromium not installed; run: uv run playwright install chromium",
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
  score
summary: |
  summary
cover_letter: |
  cover
answer: |
  answer
"""


def _write_configs(root: Path) -> Path:
    (root / "configs").mkdir()
    (root / "configs" / "profiles").mkdir()
    (root / "resumes").mkdir()
    (root / "data").mkdir()
    (root / "resumes" / "e2e.yaml").write_text("name: E2E Lever Applicant\n")
    (root / "configs" / "base_config.yaml").write_text(
        """
version: 1
llm:
  provider: mock
static_answers:
  full_name: E2E Applicant
  email: e2e@example.test
  phone: "555-0100"
  linkedin_url: https://linkedin.com/in/e2e
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


def _seed_tailored(tmp_path: Path, lever_url: str) -> str:
    data_dir = tmp_path / "data"
    engine = create_engine_from_url(sqlite_url_for(data_dir / "magicapply.sqlite3"))
    create_db(engine)
    jobs = SqlJobsRepository(engine)
    apps = SqlApplicationsRepository(engine)

    job = Job.new(
        source_name="fixture", url=lever_url, title="Senior Engineer", company="Acme"
    )
    jobs.upsert(job)

    a = Application(job_id=job.id, profile_name="e2e")
    a.transition_to(ApplicationState.SCORED, reason="seed")

    tailored_dir = data_dir / "tailored" / a.id
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
    (tailored_dir / "cover_letter.md").write_text("Applying.")
    _make_test_docx(tailored_dir / "resume.docx")

    a.tailored_path = str(tailored_dir)
    a.transition_to(ApplicationState.TAILORED, reason="seed")
    apps.add(a)
    engine.dispose()
    return job.id


class TestLeverApply:
    def test_yes_submit_hits_lever_form(
        self, tmp_path: Path, fixture_server: FixtureServer
    ) -> None:
        cfg = _write_configs(tmp_path)
        lever_url = f"{_server_url(fixture_server)}/jobs.lever.co/senior-eng/apply"
        job_id = _seed_tailored(tmp_path, lever_url)

        result = runner.invoke(
            app, ["apply", job_id, "--root", str(cfg), "--yes-submit"]
        )
        assert result.exit_code == 0, result.stdout

        assert len(fixture_server.submissions) == 1
        form = fixture_server.submissions[0]
        # Lever puts the full name in one input.
        assert form["name"] == "E2E Applicant"
        assert form["email"] == "e2e@example.test"
        assert form["phone"] == "555-0100"
        assert form["urls[LinkedIn]"].startswith("https://linkedin.com/")
        assert form["comments"] == "Applying."
        resume_bytes = form["_files"]["resume"]
        assert resume_bytes[:4] == b"PK\x03\x04"
        assert len(resume_bytes) > 1000
