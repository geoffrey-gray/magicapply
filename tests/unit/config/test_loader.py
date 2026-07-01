"""Unit tests for `load_config` — file I/O, cross-refs, error paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from magicapply.cli.main import app
from magicapply.config import ConfigError, load_config

runner = CliRunner()


def _write_valid_repo(root: Path) -> None:
    """Create a self-consistent config tree under `root`."""
    (root / "configs").mkdir()
    (root / "configs" / "profiles").mkdir()
    (root / "resumes").mkdir()
    (root / "resumes" / "swe.yaml").write_text("name: SWE\n")
    (root / "configs" / "base_config.yaml").write_text(
        """
version: 1
static_answers:
  full_name: Test User
  email: test@example.com
paths:
  resumes_dir: ../resumes
sources:
  - type: career_page
    name: acme
    urls: ["https://acme.example"]
"""
    )
    (root / "configs" / "profiles" / "swe.yaml").write_text(
        """
name: swe
base_resume: swe.yaml
sources: [acme]
"""
    )


class TestLoadConfig:
    def test_loads_valid_tree(self, tmp_path: Path) -> None:
        _write_valid_repo(tmp_path)
        loaded = load_config(tmp_path / "configs")

        assert loaded.base.static_answers.email == "test@example.com"
        assert "swe" in loaded.profiles
        assert loaded.profiles["swe"].sources == ["acme"]
        assert loaded.resumes_dir() == (tmp_path / "resumes").resolve()

    def test_missing_root_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="does not exist"):
            load_config(tmp_path / "nope")

    def test_missing_base_config_raises(self, tmp_path: Path) -> None:
        (tmp_path / "configs").mkdir()
        with pytest.raises(ConfigError, match="missing base config"):
            load_config(tmp_path / "configs")

    def test_yaml_parse_error_raises(self, tmp_path: Path) -> None:
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "base_config.yaml").write_text("this: is: not: yaml:\n  - :\n")
        with pytest.raises(ConfigError, match="YAML parse error"):
            load_config(tmp_path / "configs")

    def test_unknown_source_ref_in_profile_raises(self, tmp_path: Path) -> None:
        _write_valid_repo(tmp_path)
        (tmp_path / "configs" / "profiles" / "swe.yaml").write_text(
            "name: swe\nbase_resume: swe.yaml\nsources: [ghost]\n"
        )
        with pytest.raises(ConfigError, match="unknown sources"):
            load_config(tmp_path / "configs")

    def test_missing_base_resume_raises(self, tmp_path: Path) -> None:
        _write_valid_repo(tmp_path)
        (tmp_path / "resumes" / "swe.yaml").unlink()
        with pytest.raises(ConfigError, match="missing base_resume"):
            load_config(tmp_path / "configs")

    def test_duplicate_profile_names_rejected(self, tmp_path: Path) -> None:
        _write_valid_repo(tmp_path)
        (tmp_path / "configs" / "profiles" / "dup.yaml").write_text(
            "name: swe\nbase_resume: swe.yaml\nsources: [acme]\n"
        )
        with pytest.raises(ConfigError, match="duplicate profile"):
            load_config(tmp_path / "configs")

    def test_empty_yaml_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "base_config.yaml").write_text("")
        with pytest.raises(ConfigError, match="empty YAML"):
            load_config(tmp_path / "configs")


class TestValidateCommand:
    def test_valid_tree_exits_zero(self, tmp_path: Path) -> None:
        _write_valid_repo(tmp_path)
        result = runner.invoke(app, ["config", "validate", str(tmp_path / "configs")])
        assert result.exit_code == 0
        assert "ok" in result.stdout

    def test_invalid_tree_exits_one(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["config", "validate", str(tmp_path / "nope")])
        assert result.exit_code == 1
        assert "invalid" in result.stdout.lower() or "invalid" in result.stderr.lower()

    def test_default_root_uses_cwd_configs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_valid_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["config", "validate"])
        assert result.exit_code == 0
        assert "ok" in result.stdout
