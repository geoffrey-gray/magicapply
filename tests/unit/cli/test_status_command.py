"""Tests for `magicapply status` — in particular the APPLIED real/dry_run split."""

from __future__ import annotations

from pathlib import Path

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

runner = CliRunner()


_PROMPTS_YAML = """\
version: 1
scoring: |
  score.
summary: |
  summary.
cover_letter: |
  cover letter.
answer: |
  answer.
"""


def _write_minimal_repo(root: Path) -> Path:
    (root / "configs").mkdir()
    (root / "configs" / "profiles").mkdir()
    (root / "resumes").mkdir()
    (root / "data").mkdir()
    (root / "resumes" / "swe.yaml").write_text("name: X\n")
    (root / "configs" / "base_config.yaml").write_text(
        """
version: 1
static_answers:
  full_name: X
  email: x@example.com
paths:
  resumes_dir: ../resumes
  data_dir: ../data
sources:
  - type: career_page
    name: s
    urls: []
"""
    )
    (root / "configs" / "prompts.yaml").write_text(_PROMPTS_YAML)
    (root / "configs" / "profiles" / "swe.yaml").write_text(
        "name: swe\nbase_resume: swe.yaml\nsources: [s]\n"
    )
    return root / "configs"


def _seed_applied(
    data_dir: Path,
    *,
    url: str,
    dry_run: bool,
) -> None:
    engine = create_engine_from_url(sqlite_url_for(data_dir / "magicapply.sqlite3"))
    create_db(engine)
    jobs = SqlJobsRepository(engine)
    apps = SqlApplicationsRepository(engine)

    job = Job.new(source_name="s", url=url, title="T", company="C")
    jobs.upsert(job)
    a = Application(job_id=job.id, profile_name="swe")
    a.transition_to(ApplicationState.SCORED)
    a.transition_to(ApplicationState.TAILORED)
    a.transition_to(ApplicationState.APPLYING)
    a.transition_to(ApplicationState.APPLIED)
    a.dry_run = dry_run
    apps.add(a)
    engine.dispose()


class TestStatusApplied:
    def test_split_shows_real_and_dry_run_counts(self, tmp_path: Path) -> None:
        cfg = _write_minimal_repo(tmp_path)
        _seed_applied(tmp_path / "data", url="https://a.example/1", dry_run=False)
        _seed_applied(tmp_path / "data", url="https://a.example/2", dry_run=True)
        _seed_applied(tmp_path / "data", url="https://a.example/3", dry_run=True)

        result = runner.invoke(app, ["status", "--root", str(cfg)])

        assert result.exit_code == 0
        # Table has an "applied" row with count 3 and a note like
        # "real: 1, dry_run: 2".
        assert "applied" in result.stdout
        assert "real: 1" in result.stdout
        assert "dry_run: 2" in result.stdout

    def test_zero_applied_shows_no_split(self, tmp_path: Path) -> None:
        cfg = _write_minimal_repo(tmp_path)
        result = runner.invoke(app, ["status", "--root", str(cfg)])
        assert result.exit_code == 0
        assert "applied" in result.stdout
        # No split note when the applied bucket is empty.
        assert "dry_run:" not in result.stdout
