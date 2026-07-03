"""Smoke tests for the top-level CLI."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from magicapply import __version__
from magicapply.cli.main import app

runner = CliRunner()


def test_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "magicapply" in result.stdout.lower()


def test_version_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


class TestDoctor:
    def test_reports_environment(self) -> None:
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert __version__ in result.stdout
        assert "python" in result.stdout
        # Chromium line always present (either PRESENT or MISSING).
        assert "chromium" in result.stdout
        assert ("PRESENT" in result.stdout) or ("MISSING" in result.stdout)

    def test_bad_root_does_not_gate(self, tmp_path: Path) -> None:
        # `doctor` is diagnostic, not gating — a broken config root prints
        # a message and still exits zero.
        missing = tmp_path / "no-such-configs"
        result = runner.invoke(app, ["doctor", "--root", str(missing)])
        assert result.exit_code == 0
        assert "invalid" in result.stdout or "missing" in result.stdout

    def test_valid_root_reports_provider_and_db(self, tmp_path: Path) -> None:
        # A minimal-but-valid config tree.
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "profiles").mkdir()
        (tmp_path / "resumes").mkdir()
        (tmp_path / "data").mkdir()
        (tmp_path / "resumes" / "r.yaml").write_text("name: T\n")
        (tmp_path / "configs" / "base_config.yaml").write_text(
            """
version: 1
llm:
  provider: mock
static_answers:
  full_name: T
  email: t@example.com
paths:
  resumes_dir: ../resumes
  data_dir: ../data
sources: []
"""
        )
        (tmp_path / "configs" / "prompts.yaml").write_text(
            "version: 1\nscoring: |\n  s\nsummary: |\n  s\ncover_letter: |\n  c\nanswer: |\n  a\n"
        )
        result = runner.invoke(app, ["doctor", "--root", str(tmp_path / "configs")])
        assert result.exit_code == 0
        assert "llm provider mock" in result.stdout
        assert "data dir" in result.stdout
        # DB not yet created; doctor reports the fact without failing.
        assert "not created yet" in result.stdout
