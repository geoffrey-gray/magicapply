"""Hermetic end-to-end: fixture HTTP server + real Chromium + full pipeline.

Proves the discover → tailor → apply loop against a local Greenhouse-shaped
form. LLM is stubbed via the shape-aware ``MockLLMClient`` so no API key is
needed; the fixture server records every form POST so we can assert the
Greenhouse handler navigated, filled every standard field, and either
clicked Submit (with ``--yes-submit``) or stopped one click short (default
dry-run).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
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

_GOLDENS_ROOT = Path(__file__).resolve().parents[2] / "goldens" / "tailoring"
_REPO_TEMPLATE = Path(__file__).resolve().parents[3] / "configs" / "resume_template.docx"

runner = CliRunner()

_CHROMIUM_CACHE = Path.home() / ".cache" / "ms-playwright"


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
  Return only a JSON object with score (0-100) and rationale.
summary: |
  Rewrite the resume summary for the job.
cover_letter: |
  Write a cover letter body from the base resume.
answer: |
  Answer the screening question from the resume.
"""


def _write_configs(root: Path, careers_url: str) -> Path:
    """Build a self-consistent config tree; return the `configs/` path."""
    (root / "configs").mkdir(exist_ok=True)
    (root / "configs" / "profiles").mkdir(exist_ok=True)
    (root / "resumes").mkdir(exist_ok=True)
    (root / "data").mkdir(exist_ok=True)
    (root / "resumes" / "e2e.yaml").write_text(
        """
name: E2E Applicant
email: e2e@example.test
phone: "555-0100"
summary: Backend engineer with Python.
experience:
  - company: Prior
    title: Engineer
    start: "2020-01"
    bullets: ["Built things."]
skills: [python, distributed systems]
"""
    )
    (root / "configs" / "base_config.yaml").write_text(
        f"""
version: 1
llm:
  provider: mock
scoring:
  threshold: 70
  prefilter:
    locations: ["Remote"]
    seniority: ["senior", "staff", "director"]
static_answers:
  full_name: E2E Applicant
  email: e2e@example.test
  phone: "555-0100"
  linkedin_url: https://linkedin.com/in/e2e
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
    (root / "configs" / "profiles" / "e2e.yaml").write_text(
        "name: e2e\nbase_resume: e2e.yaml\nsources: [fixture]\n"
    )
    shutil.copy(_REPO_TEMPLATE, root / "configs" / "resume_template.docx")
    return root / "configs"


def _server_url(server: FixtureServer) -> str:
    assert server._httpd is not None
    host, port = server._httpd.server_address[:2]
    if isinstance(host, bytes):
        host = host.decode()
    return f"http://{host}:{port}"


def _load_applied(tmp_path: Path) -> list:
    engine = create_engine_from_url(
        sqlite_url_for(tmp_path / "data" / "magicapply.sqlite3")
    )
    apps_repo = SqlApplicationsRepository(engine)
    applied = apps_repo.list_by_state(ApplicationState.APPLIED)
    engine.dispose()
    return applied


# --- goldens ------------------------------------------------------------------


_UPDATE_GOLDENS = os.environ.get("MAGICAPPLY_UPDATE_GOLDENS") == "1"

# Cover letter -> slug lookup. The shape-aware mock echoes the parsed job
# title into the letter, so a simple substring is enough.
_TITLE_TO_SLUG = {
    "Senior Backend Engineer": "senior-backend",
    "Director of Engineering": "director",
}


def _slug_from_cover_letter(cover: str) -> str:
    for title, slug in _TITLE_TO_SLUG.items():
        if title in cover:
            return slug
    raise AssertionError(f"cover letter matches no known fixture job: {cover[:120]!r}")


def _redact_job_id(resume_dict: dict) -> dict:
    """Return a copy with the volatile job_id normalized."""
    out = dict(resume_dict)
    out["job_id"] = "<REDACTED>"
    return out


def _check_tailored_goldens(tmp_path: Path) -> None:
    for app_dir in sorted((tmp_path / "data" / "tailored").iterdir()):
        actual_resume = _redact_job_id(
            yaml.safe_load((app_dir / "resume.yaml").read_text())
        )
        actual_cover = (app_dir / "cover_letter.md").read_text()
        slug = _slug_from_cover_letter(actual_cover)
        golden_dir = _GOLDENS_ROOT / slug

        if _UPDATE_GOLDENS:
            golden_dir.mkdir(parents=True, exist_ok=True)
            (golden_dir / "resume.yaml").write_text(
                yaml.safe_dump(actual_resume, sort_keys=False, allow_unicode=True)
            )
            (golden_dir / "cover_letter.md").write_text(actual_cover)
            continue

        golden_resume = yaml.safe_load(
            (golden_dir / "resume.yaml").read_text()
        )
        assert actual_resume == golden_resume, (
            f"tailored resume drift for {slug}; MAGICAPPLY_UPDATE_GOLDENS=1 to regen"
        )
        golden_cover = (golden_dir / "cover_letter.md").read_text()
        assert actual_cover == golden_cover, (
            f"cover letter drift for {slug}; MAGICAPPLY_UPDATE_GOLDENS=1 to regen"
        )


class TestE2EDryRun:
    def test_full_pipeline_dry_run_default(
        self, tmp_path: Path, fixture_server: FixtureServer
    ) -> None:
        cfg = _write_configs(tmp_path, f"{_server_url(fixture_server)}/careers")

        # Step 1: discover — the fixture careers page yields 3 JobPostings.
        # iOS Designer (San Francisco) fails the "Remote" prefilter, so scoring
        # returns 0 for it and it lands in REJECTED.
        result = runner.invoke(app, ["discover", "e2e", "--root", str(cfg)])
        assert result.exit_code == 0, result.stdout
        assert "discovered: 3" in result.stdout
        assert "scored: 3" in result.stdout
        assert "rejected: 1" in result.stdout

        # Step 2: tailor — 2 SCORED apps become TAILORED, artifacts on disk.
        result = runner.invoke(app, ["tailor", "e2e", "--root", str(cfg)])
        assert result.exit_code == 0, result.stdout
        assert "tailored: 2" in result.stdout

        artifacts = sorted((tmp_path / "data" / "tailored").glob("*/resume.yaml"))
        assert len(artifacts) == 2

        # Golden-file assertions for the two tailored artifacts. The mock
        # LLM is deterministic given identical prompts, so both the summary
        # and the cover letter are stable byte-for-byte. Job_id is a hash of
        # the fixture URL and therefore changes every run — redact it before
        # comparing.
        _check_tailored_goldens(tmp_path)

        # Step 3: run — real Chromium drives the fixture form, stops one
        # click short of Submit (default --no-submit).
        result = runner.invoke(app, ["run", "e2e", "--root", str(cfg)])
        assert result.exit_code == 0, result.stdout
        assert "applied: 2" in result.stdout
        assert "dry-run" in result.stdout.lower()

        # Fixture server never saw a submission.
        assert fixture_server.submissions == []

        # Both apps in APPLIED with dry_run=True.
        applied = _load_applied(tmp_path)
        assert len(applied) == 2
        assert all(a.dry_run for a in applied)


class TestE2EYesSubmit:
    def test_full_pipeline_yes_submit_hits_fixture(
        self, tmp_path: Path, fixture_server: FixtureServer
    ) -> None:
        cfg = _write_configs(tmp_path, f"{_server_url(fixture_server)}/careers")

        result = runner.invoke(
            app, ["run", "e2e", "--root", str(cfg), "--yes-submit"]
        )
        assert result.exit_code == 0, result.stdout
        assert "applied: 2" in result.stdout

        # Two real submissions.
        assert len(fixture_server.submissions) == 2

        for form in fixture_server.submissions:
            assert form["first_name"] == "E2E"
            assert form["last_name"] == "Applicant"
            assert form["email"] == "e2e@example.test"
            assert form["phone"] == "555-0100"
            assert form["linkedin_url"].startswith("https://linkedin.com/")
            assert form["cover_letter_text"]
            # Yes/no select — router picked Yes for authorized_to_work_us=True.
            assert form["authorized"] == "Yes"
            # Open-ended textarea — router dispatched to NarrativeEngine.answer.
            assert form["why_acme"], "expected an answer from the narrative engine"
            # Resume DOCX was uploaded via multipart/form-data.
            resume_bytes = form["_files"]["resume"]
            assert resume_bytes[:4] == b"PK\x03\x04"
            assert len(resume_bytes) > 1000  # non-trivial docx

        # The shape-aware mock echoes the parsed job title into the cover
        # letter, so we get one per surviving posting.
        letters = " || ".join(f["cover_letter_text"] for f in fixture_server.submissions)
        assert "Senior Backend Engineer" in letters
        assert "Director of Engineering" in letters

        # Rows APPLIED with dry_run=False.
        applied = _load_applied(tmp_path)
        assert len(applied) == 2
        assert not any(a.dry_run for a in applied)
