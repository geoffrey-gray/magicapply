"""Hermetic E2E dry-run against promoted W.4 live DOM captures (W.7).

Serves real observed-form HTML from ``tests/fixtures/captured/*`` via the
local fixture server. Asserts the apply pipeline reaches APPLIED (dry-run)
without requiring traditional HTML form POST (React ATS pages).
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
from magicapply.infrastructure.browser.forms.capture_loader import (
    captured_fixtures_root,
    load_capture,
)
from tests.integration.e2e.fixture_server import FixtureServer, live_capture_path

runner = CliRunner()

_CHROMIUM_CACHE = Path.home() / ".cache" / "ms-playwright"

_LIVE_CAPTURES = (
    "greenhouse-reddit-20260707",
    "lever-foodsmart-20260707",
    "ashby-trm-20260707",
    "workday-circle-staff-ds-20260707",
)


def _make_test_docx(path: Path) -> Path:
    from docx import Document

    doc = Document()
    doc.add_paragraph("E2E Live Capture Applicant")
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
    _make_test_docx(root / "resumes" / "e2e.docx")
    (root / "resumes" / "e2e.yaml").write_text(
        """
name: E2E Applicant
email: e2e@example.test
phone: "555-0100"
summary: Staff data scientist with Python.
experience:
  - company: Prior
    title: Staff DS
    start: "2020-01"
    bullets: ["Built models."]
skills: [python]
"""
    )
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
  location: Apollo Beach, FL
  city: Apollo Beach
  state: Florida
  address_line_1: "7425 Victoria Cir"
  postal_code: "33572"
  country: United States
  authorized_to_work_us: true
  needs_sponsorship_us: false
  current_employer: Intrinsic
  years_of_experience: 12
  desired_salary: "$200,000"
  work_authorization: US citizen
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


def _seed_tailored(tmp_path: Path, job_url: str, *, title: str, company: str) -> str:
    data_dir = tmp_path / "data"
    engine = create_engine_from_url(sqlite_url_for(data_dir / "magicapply.sqlite3"))
    create_db(engine)
    jobs_repo = SqlJobsRepository(engine)
    apps_repo = SqlApplicationsRepository(engine)

    job = Job.new(
        source_name="fixture",
        url=job_url,
        title=title,
        company=company,
        description="Staff data science role.",
        location="Remote",
    )
    jobs_repo.upsert(job)

    app_row = Application(job_id=job.id, profile_name="e2e")
    app_row.transition_to(ApplicationState.SCORED, reason="seed")

    tailored_dir = data_dir / "tailored" / app_row.id
    tailored_dir.mkdir(parents=True, exist_ok=True)
    (tailored_dir / "resume.yaml").write_text(
        yaml.safe_dump(
            {
                "base_name": "E2E Applicant",
                "job_id": job.id,
                "name": "E2E Applicant",
                "email": "e2e@example.test",
                "summary": "Staff data scientist.",
            }
        )
    )
    _make_test_docx(tailored_dir / "resume.docx")

    app_row.tailored_path = str(tailored_dir)
    app_row.transition_to(ApplicationState.TAILORED, reason="seed")
    apps_repo.add(app_row)
    engine.dispose()
    return job.id


@pytest.fixture(params=_LIVE_CAPTURES)
def live_capture_id(request: pytest.FixtureRequest) -> str:
    capture_id = request.param
    root = captured_fixtures_root() / capture_id
    if not (root / "dom.html").exists():
        pytest.skip(f"promoted capture missing: {capture_id}")
    bundle = load_capture(root)
    if not bundle.meta.live:
        pytest.skip(f"{capture_id} is not marked live")
    return capture_id


class TestCapturedLiveDryRun:
    def test_apply_dry_run_on_promoted_capture(
        self,
        tmp_path: Path,
        fixture_server: FixtureServer,
        live_capture_id: str,
    ) -> None:
        bundle = load_capture(captured_fixtures_root() / live_capture_id)
        base = _server_url(fixture_server)
        job_url = f"{base}{live_capture_path(live_capture_id)}"
        cfg = _write_configs(tmp_path)
        job_id = _seed_tailored(
            tmp_path,
            job_url,
            title="Staff Data Scientist",
            company="Fixture Co",
        )

        result = runner.invoke(app, ["apply", job_id, "--root", str(cfg)])
        assert result.exit_code == 0, result.stdout
        assert "dry-run" in result.stdout.lower()

        engine = create_engine_from_url(
            sqlite_url_for(tmp_path / "data" / "magicapply.sqlite3")
        )
        apps = SqlApplicationsRepository(engine).list_by_state(ApplicationState.APPLIED)
        engine.dispose()
        assert len(apps) == 1
        assert apps[0].dry_run is True
        assert fixture_server.submissions == []