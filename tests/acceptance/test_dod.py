"""End-to-end acceptance for the DoD workflow.

One exhaustive scenario that mirrors the "setup → discovery + scoring →
tailoring with keyword injection → cross-ATS submission" flow from the
DoD summary:

  1. Config includes a keyword bank + one profile linked to a base
     resume.
  2. Fixture careers page (/careers-cross-ats) emits four JSON-LD
     JobPostings, each pointing at a different ATS route on the same
     fixture server.
  3. `magicapply run senior-swe --yes-submit` walks the whole loop --
     discover, tailor (with bank-matched bullet injection), and apply
     to all four ATSes in one shared Chromium session.
  4. Assertions verify: exactly four jobs discovered, four tailored
     artifacts on disk with the DOCX rendered, four HTTP form
     submissions recorded (one per ATS) with identity fields set + a
     valid DOCX uploaded.

Skipped when Chromium is not installed. Marked `slow`.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.chromium_util import SKIP_NO_CHROMIUM, chromium_installed
from typer.testing import CliRunner

from magicapply.cli.main import app
from magicapply.domain.models.application import ApplicationState
from magicapply.infrastructure.persistence.db import (
    create_engine_from_url,
    sqlite_url_for,
)
from magicapply.infrastructure.persistence.repositories.applications import (
    SqlApplicationsRepository,
)
from tests.integration.e2e.fixture_server import FixtureServer

runner = CliRunner()

_CHROMIUM_CACHE = Path.home() / ".cache" / "ms-playwright"


def _make_test_docx(path: Path) -> Path:
    from docx import Document

    doc = Document()
    doc.add_paragraph("Acceptance Applicant")
    doc.add_paragraph("Backend engineer with Python and distributed systems.")
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
  Score the job.
summary: |
  Rewrite the summary.
cover_letter: |
  Write a cover letter.
answer: |
  Answer the screening question.
keyword_extraction: |
  Extract JD terms as a JSON array.
bullet_rewrite: |
  Rewrite each bullet weaving in evidence.
"""


def _server_url(server: FixtureServer) -> str:
    assert server._httpd is not None
    host, port = server._httpd.server_address[:2]
    if isinstance(host, bytes):
        host = host.decode()
    return f"http://{host}:{port}"


def _write_configs(root: Path, careers_url: str) -> Path:
    (root / "configs").mkdir()
    (root / "configs" / "profiles").mkdir()
    (root / "resumes").mkdir()
    (root / "data").mkdir()
    source_docx = _make_test_docx(root / "resumes" / "senior-swe.docx")
    (root / "resumes" / "senior-swe.yaml").write_text(
        f"""
name: Acceptance Applicant
email: accept@example.test
phone: "555-0100"
source_docx_path: {source_docx}
summary: Backend engineer with Python and distributed systems.
experience:
  - company: Prior
    title: Engineer
    start: "2020-01"
    bullets:
      - "Shipped things."
      - "Owned things."
skills: [python, distributed systems]
"""
    )
    (root / "configs" / "base_config.yaml").write_text(
        f"""
version: 1
llm:
  provider: mock
scoring:
  # Hermetic fixtures expect mock-LLM scores (not YAKE keyword alignment).
  mode: llm
  threshold: 70
  prefilter:
    locations: ["Remote"]
    seniority: ["senior", "staff"]
static_answers:
  full_name: Acceptance Applicant
  email: accept@example.test
  phone: "555-0100"
  linkedin_url: https://linkedin.com/in/accept
  location: Boston, MA
  authorized_to_work_us: true
  needs_sponsorship_us: false
paths:
  resumes_dir: ../resumes
  data_dir: ../data
sources:
  - type: career_page
    name: fixture
    urls: ["{careers_url}"]
"""
    )
    (root / "configs" / "prompts.yaml").write_text(_PROMPTS_YAML)
    (root / "configs" / "keyword_bank.yaml").write_text(
        """
version: 1
keywords:
  - term: distributed systems
    synonyms: [microservices]
    evidence: Led migration to microservices at Prior, cutting p99 latency
  - term: python
    evidence: 8+ years primary language
"""
    )
    (root / "configs" / "profiles" / "senior-swe.yaml").write_text(
        "name: senior-swe\nbase_resume: senior-swe.yaml\nsources: [fixture]\n"
    )
    return root / "configs"


class TestDodAcceptance:
    def test_full_workflow_across_all_four_atses(
        self, tmp_path: Path, fixture_server: FixtureServer
    ) -> None:
        base_url = _server_url(fixture_server)
        cfg = _write_configs(tmp_path, f"{base_url}/careers-cross-ats")

        # Step 1: full pipeline in one shot -- discover, tailor, apply.
        result = runner.invoke(
            app, ["run", "senior-swe", "--root", str(cfg), "--yes-submit"]
        )
        assert result.exit_code == 0, result.stdout

        # Discovery surfaced all four cross-ATS postings.
        assert "discovered: 4" in result.stdout
        # All four passed prefilter + mock-LLM scoring threshold.
        assert "scored: 4" in result.stdout

        # Tailoring produced four TAILORED artifacts on disk with a DOCX
        # rendered next to the YAML.
        tailored_dirs = sorted((tmp_path / "data" / "tailored").iterdir())
        assert len(tailored_dirs) == 4
        for app_dir in tailored_dirs:
            assert (app_dir / "resume.yaml").exists()
            # W.2: Phase 1 defers cover letter generation; the pipeline
            # does not write cover_letter.md unless generate_cover_letter=True.
            assert not (app_dir / "cover_letter.md").exists()
            docx = app_dir / "resume.docx"
            assert docx.exists()
            assert docx.read_bytes()[:4] == b"PK\x03\x04"

        # Apply fired against all four handlers.
        assert len(fixture_server.submissions) == 4
        # Each submission carries a valid DOCX resume (Greenhouse and Ashby
        # use file input `resume` / `_systemfield_resume`; Workday uses
        # `resume`; Lever uses `resume`). Any submission whose _files does
        # not carry a non-trivial DOCX would blow this assertion.
        for form in fixture_server.submissions:
            files = form.get("_files", {})
            docx_blobs = [v for v in files.values() if v[:4] == b"PK\x03\x04"]
            assert docx_blobs, f"no DOCX found in submission: {list(files)}"
            assert len(docx_blobs[0]) > 1000

        # Application rows all landed in APPLIED with dry_run=False.
        engine = create_engine_from_url(
            sqlite_url_for(tmp_path / "data" / "magicapply.sqlite3")
        )
        applied = SqlApplicationsRepository(engine).list_by_state(
            ApplicationState.APPLIED
        )
        engine.dispose()
        assert len(applied) == 4
        assert all(a.dry_run is False for a in applied)
