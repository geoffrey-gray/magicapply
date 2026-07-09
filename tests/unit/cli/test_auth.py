"""CLI tests for magicapply auth (no live browser)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from magicapply.cli.main import app

runner = CliRunner()


def test_auth_sites_lists_registry() -> None:
    result = runner.invoke(app, ["auth", "sites"])
    assert result.exit_code == 0
    assert "linkedin" in result.stdout
    assert "indeed" in result.stdout
    assert "glassdoor" in result.stdout


def test_auth_status_with_config(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "base_config.yaml").write_text(
        """
version: 1
llm:
  provider: mock
static_answers:
  full_name: T
  email: t@example.com
paths:
  data_dir: ../data
  resumes_dir: ../resumes
sources: []
"""
    )
    (configs / "prompts.yaml").write_text(
        "version: 1\nscoring: |\n  s\nsummary: |\n  s\ncover_letter: |\n  c\nanswer: |\n  a\n"
    )
    (configs / "profiles").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "resumes").mkdir()
    result = runner.invoke(app, ["auth", "status", "--root", str(configs)])
    assert result.exit_code == 0
    assert "linkedin" in result.stdout
    assert "resolved" in result.stdout or "none" in result.stdout


def test_auth_clear_unknown_site() -> None:
    result = runner.invoke(app, ["auth", "clear", "notasite"])
    assert result.exit_code == 1
