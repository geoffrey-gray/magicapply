"""Smoke tests for the top-level CLI."""

from __future__ import annotations

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


def test_doctor_reports_environment() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
    assert "python" in result.stdout
