"""Permanent tests for the RouterRules Pydantic model.

Locks the YAML → model surface: flag folding, dict/string entry shapes,
operator-override behavior, model validation errors. Parity with the
pre-migration constants lives in ``test_router_rules_parity.py`` and gets
deleted after Phase C; this file stays.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from magicapply.config import ConfigError, load_config
from magicapply.config.router_rules import RouterRules


_MINIMAL_YAML = """
version: 1
identity:
  - {pattern: '\\bemail\\b', attr: email}
yes_no:
  - {pattern: 'authori[sz]ed', attr: authorized_to_work_us}
dei: []
consent:
  patterns:
    - {pattern: 'i agree', flags: [IGNORECASE]}
  option_phrases: ["agree", "yes"]
optional_checkbox: []
sms_opt_in: []
handler_owned_widget: []
handler_owned_variants: []
experience_years: {pattern: '(\\d+)\\+? years', flags: [IGNORECASE]}
skill_screening: {pattern: 'expert', flags: [IGNORECASE]}
"""


class TestFlagFolding:
    def test_ignorecase_flag_folds_into_compiled_pattern(self) -> None:
        rules = RouterRules.load_from_yaml(_MINIMAL_YAML)
        assert rules.consent.patterns[0].pattern.flags & re.IGNORECASE

    def test_missing_flags_key_yields_default_compilation(self) -> None:
        rules = RouterRules.load_from_yaml(_MINIMAL_YAML)
        assert rules.identity[0].pattern.flags == re.compile(r"x").flags

    def test_unknown_flag_name_raises(self) -> None:
        bad = _MINIMAL_YAML.replace("[IGNORECASE]", "[NOPE]", 1)
        with pytest.raises((ValueError, Exception)):
            RouterRules.load_from_yaml(bad)


class TestEntryShapes:
    def test_dict_entry_accepted(self) -> None:
        rules = RouterRules.load_from_yaml(_MINIMAL_YAML)
        assert rules.identity[0].attr == "email"
        assert rules.identity[0].pattern.pattern == r"\bemail\b"

    def test_bare_string_entry_accepted_for_no_payload_sections(self) -> None:
        """`sms_opt_in` and `handler_owned_widget` and `consent.patterns`
        accept bare regex strings as well as `{pattern, flags}` maps.
        Convenience shape for operators editing the YAML by hand."""
        with_bare = _MINIMAL_YAML.replace(
            "consent:\n  patterns:\n    - {pattern: 'i agree', flags: [IGNORECASE]}",
            "consent:\n  patterns:\n    - 'i agree'",
        )
        rules = RouterRules.load_from_yaml(with_bare)
        assert rules.consent.patterns[0].pattern.pattern == "i agree"
        # Bare strings compile with no flags — that's the operator's call.
        assert rules.consent.patterns[0].pattern.flags == re.compile(r"x").flags


class TestValidation:
    def test_missing_pattern_key_raises(self) -> None:
        bad = _MINIMAL_YAML.replace(
            "- {pattern: '\\bemail\\b', attr: email}",
            "- {attr: email}",
        )
        with pytest.raises(Exception):  # noqa: PT011 — Pydantic or ValueError
            RouterRules.load_from_yaml(bad)

    def test_extra_field_rejected(self) -> None:
        bad = _MINIMAL_YAML.replace(
            "- {pattern: '\\bemail\\b', attr: email}",
            "- {pattern: '\\bemail\\b', attr: email, bogus: 1}",
        )
        with pytest.raises(Exception):
            RouterRules.load_from_yaml(bad)


class TestLoaderIntegration:
    def _write_configs(self, tmp_path: Path) -> Path:
        cfg = tmp_path / "configs"
        cfg.mkdir()
        (tmp_path / "resumes").mkdir()
        (tmp_path / "data").mkdir()
        (cfg / "profiles").mkdir()
        (tmp_path / "resumes" / "x.yaml").write_text("name: X\n")
        (cfg / "base_config.yaml").write_text(
            "version: 1\n"
            "static_answers:\n  full_name: X\n  email: x@example.com\n"
            "paths:\n  resumes_dir: ../resumes\n  data_dir: ../data\n"
            "sources: []\n"
        )
        (cfg / "prompts.yaml").write_text(
            "version: 1\nscoring: |\n  s\nsummary: |\n  s\n"
            "cover_letter: |\n  s\nanswer: |\n  s\n"
        )
        (cfg / "profiles" / "x.yaml").write_text(
            "name: x\nbase_resume: x.yaml\nsources: []\n"
        )
        return cfg

    def test_loader_uses_operator_override_when_present(self, tmp_path: Path) -> None:
        cfg = self._write_configs(tmp_path)
        (cfg / "router_rules.yaml").write_text(_MINIMAL_YAML)
        loaded = load_config(cfg)
        # Operator override wins — only 1 identity entry, not 22.
        assert len(loaded.router_rules.identity) == 1
        assert loaded.router_rules.identity[0].attr == "email"

    def test_loader_falls_back_to_package_default(self, tmp_path: Path) -> None:
        cfg = self._write_configs(tmp_path)
        # No router_rules.yaml in configs dir.
        loaded = load_config(cfg)
        # Package default identity rows (see router_rules.yaml + parity test).
        assert len(loaded.router_rules.identity) == 23

    def test_loader_raises_on_malformed_override(self, tmp_path: Path) -> None:
        cfg = self._write_configs(tmp_path)
        (cfg / "router_rules.yaml").write_text(
            yaml.safe_dump({"version": 1, "identity": [{"attr": "email"}]})
        )
        with pytest.raises(ConfigError):
            load_config(cfg)
