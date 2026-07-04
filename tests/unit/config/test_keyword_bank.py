"""Unit tests for KeywordEntry / KeywordBank + LoadedConfig.effective_bank."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from magicapply.config import (
    KeywordBank,
    KeywordEntry,
    Profile,
    load_config,
)


class TestKeywordEntry:
    def test_minimal_valid(self) -> None:
        e = KeywordEntry(term="python", evidence="5+ years")
        assert e.term == "python"
        assert e.evidence == "5+ years"
        assert e.synonyms == []
        assert e.tags == []

    def test_blank_term_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not be blank"):
            KeywordEntry(term="  ", evidence="ok")

    def test_blank_evidence_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not be blank"):
            KeywordEntry(term="python", evidence="  ")


class TestKeywordBank:
    def test_default_is_empty(self) -> None:
        bank = KeywordBank()
        assert bank.version == 1
        assert bank.keywords == []

    def test_extend_with_replaces_by_term(self) -> None:
        base = KeywordBank(
            keywords=[
                KeywordEntry(term="python", evidence="original"),
                KeywordEntry(term="rust", evidence="baseline"),
            ]
        )
        override = KeywordBank(
            keywords=[
                KeywordEntry(term="python", evidence="new evidence"),
            ]
        )
        merged = base.extend_with(override)
        by_term = {e.term: e for e in merged.keywords}
        # python replaced, rust preserved.
        assert by_term["python"].evidence == "new evidence"
        assert by_term["rust"].evidence == "baseline"

    def test_extend_with_appends_new_terms(self) -> None:
        base = KeywordBank(keywords=[KeywordEntry(term="python", evidence="x")])
        override = KeywordBank(keywords=[KeywordEntry(term="go", evidence="y")])
        merged = base.extend_with(override)
        terms = [e.term for e in merged.keywords]
        # self-order preserved, override-only terms appended at the end.
        assert terms == ["python", "go"]

    def test_extend_with_empty_override_is_identity(self) -> None:
        base = KeywordBank(
            keywords=[KeywordEntry(term="python", evidence="baseline")]
        )
        merged = base.extend_with(KeywordBank())
        assert [e.term for e in merged.keywords] == ["python"]


# --- LoadedConfig.effective_bank ---------------------------------------------


_PROMPTS_YAML = """\
version: 1
scoring: |
  score
summary: |
  summary
cover_letter: |
  cover letter
answer: |
  answer
"""


def _write_valid_repo(root: Path, *, with_bank: bool = False) -> Path:
    (root / "configs").mkdir()
    (root / "configs" / "profiles").mkdir()
    (root / "resumes").mkdir()
    (root / "resumes" / "r.yaml").write_text("name: T\n")
    (root / "configs" / "base_config.yaml").write_text(
        """
version: 1
static_answers:
  full_name: T
  email: t@example.com
paths:
  resumes_dir: ../resumes
sources:
  - type: career_page
    name: s
    urls: []
"""
    )
    (root / "configs" / "prompts.yaml").write_text(_PROMPTS_YAML)
    (root / "configs" / "profiles" / "swe.yaml").write_text(
        "name: swe\nbase_resume: r.yaml\nsources: [s]\n"
    )
    if with_bank:
        (root / "configs" / "keyword_bank.yaml").write_text(
            """
version: 1
keywords:
  - term: python
    evidence: 5+ years, primary language
    synonyms: [py]
  - term: distributed systems
    evidence: Led migration to microservices at Acme
"""
        )
    return root / "configs"


class TestLoadedConfigBank:
    def test_missing_bank_is_empty(self, tmp_path: Path) -> None:
        cfg = _write_valid_repo(tmp_path, with_bank=False)
        loaded = load_config(cfg)
        assert loaded.keyword_bank.keywords == []

    def test_present_bank_loads(self, tmp_path: Path) -> None:
        cfg = _write_valid_repo(tmp_path, with_bank=True)
        loaded = load_config(cfg)
        terms = [e.term for e in loaded.keyword_bank.keywords]
        assert terms == ["python", "distributed systems"]

    def test_effective_bank_without_override_returns_global(
        self, tmp_path: Path
    ) -> None:
        cfg = _write_valid_repo(tmp_path, with_bank=True)
        loaded = load_config(cfg)
        profile = loaded.profile("swe")
        assert loaded.effective_bank(profile) is loaded.keyword_bank

    def test_effective_bank_merges_override(self, tmp_path: Path) -> None:
        cfg = _write_valid_repo(tmp_path, with_bank=True)
        # Point profile at an override file that replaces `python` and adds `go`.
        (tmp_path / "configs" / "swe-keywords.yaml").write_text(
            """
version: 1
keywords:
  - term: python
    evidence: 8 years, principal language at Acme
  - term: go
    evidence: 2 years, secondary language
"""
        )
        (tmp_path / "configs" / "profiles" / "swe.yaml").write_text(
            "name: swe\nbase_resume: r.yaml\nsources: [s]\n"
            "keyword_bank_override: swe-keywords.yaml\n"
        )
        loaded = load_config(cfg)
        bank = loaded.effective_bank(loaded.profile("swe"))
        by_term = {e.term: e for e in bank.keywords}
        assert by_term["python"].evidence == "8 years, principal language at Acme"
        assert "go" in by_term
        assert "distributed systems" in by_term  # unaffected

    def test_invalid_bank_raises_config_error(self, tmp_path: Path) -> None:
        cfg = _write_valid_repo(tmp_path, with_bank=False)
        (tmp_path / "configs" / "keyword_bank.yaml").write_text(
            "version: 1\nkeywords:\n  - term: python\n"  # missing evidence
        )
        from magicapply.config import ConfigError
        with pytest.raises(ConfigError, match="keyword bank"):
            load_config(cfg)


class TestProfileBankOverrideField:
    def test_optional_default_none(self) -> None:
        p = Profile(name="swe", base_resume="r.yaml")
        assert p.keyword_bank_override is None
