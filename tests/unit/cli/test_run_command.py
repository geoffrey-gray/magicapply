"""Smoke tests for `magicapply run` — proves the discover → tailor → apply
sequencing wires end-to-end without touching real network or Chromium."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from magicapply.cli.main import app

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


def _write_empty_source_repo(tmp_path: Path) -> Path:
    """A valid config tree whose only source has zero URLs."""
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "profiles").mkdir()
    (tmp_path / "resumes").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "resumes" / "swe.yaml").write_text("name: Test\n")
    (tmp_path / "configs" / "base_config.yaml").write_text(
        """
version: 1
llm:
  provider: mock
static_answers:
  full_name: Test
  email: test@example.com
paths:
  resumes_dir: ../resumes
  data_dir: ../data
sources:
  - type: job_url
    name: watched
    urls: []
"""
    )
    (tmp_path / "configs" / "prompts.yaml").write_text(_PROMPTS_YAML)
    (tmp_path / "configs" / "profiles" / "swe.yaml").write_text(
        "name: swe\nbase_resume: swe.yaml\nsources: [watched]\n"
    )
    return tmp_path / "configs"


class _NoOpSession:
    """PlaywrightSession stand-in; not used because there are no TAILORED apps."""

    def __enter__(self) -> _NoOpSession:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def new_page(self) -> None:
        raise AssertionError("session.new_page should not be called in the empty case")


@pytest.fixture
def patched_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard against the run command opening a real browser."""
    monkeypatch.setattr(
        "magicapply.cli.commands.pipeline.PlaywrightSession",
        lambda *args, **kwargs: _NoOpSession(),
    )


class TestRunSmoke:
    def test_empty_source_run_exits_zero(
        self, tmp_path: Path, patched_session: None
    ) -> None:
        cfg = _write_empty_source_repo(tmp_path)
        result = runner.invoke(app, ["run", "swe", "--root", str(cfg)])
        # discover finds nothing → tailor no-op → apply skipped (no TAILORED apps).
        # No PlaywrightSession is opened because the pipeline short-circuits.
        assert result.exit_code == 0, result.stdout
        assert "discovered: 0" in result.stdout
        assert "tailored: 0" in result.stdout
        assert "skipping apply phase" in result.stdout

    def test_yes_submit_flag_parses(
        self, tmp_path: Path, patched_session: None
    ) -> None:
        # Just verify the flag doesn't reject; the pipeline still short-circuits
        # to no apply because the source is empty.
        cfg = _write_empty_source_repo(tmp_path)
        result = runner.invoke(
            app, ["run", "swe", "--root", str(cfg), "--yes-submit"]
        )
        assert result.exit_code == 0, result.stdout
