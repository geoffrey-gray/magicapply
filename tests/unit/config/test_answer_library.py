"""Model + loader tests for the answer library."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from magicapply.config.loader import ConfigError, load_config
from magicapply.config.models import AnswerLibrary, LibraryEntry


class TestLibraryEntry:
    def test_verified_default_and_optional_regex(self) -> None:
        e = LibraryEntry(question="Q?", canonical_answer="A")
        assert e.status == "verified"
        assert e.question_regex is None
        assert e.seen_on == []

    def test_blank_question_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be blank"):
            LibraryEntry(question="   ", canonical_answer="A")

    def test_blank_answer_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be blank"):
            LibraryEntry(question="Q?", canonical_answer="")


class TestAnswerLibrary:
    def test_defaults_are_empty(self) -> None:
        lib = AnswerLibrary()
        assert lib.version == 1
        assert lib.answers == []

    def test_round_trip_from_yaml_dict(self) -> None:
        raw = {
            "version": 1,
            "answers": [
                {
                    "question": "How did you hear about us?",
                    "canonical_answer": "LinkedIn",
                    "seen_on": ["greenhouse:anthropic:2026-07-04"],
                    "status": "verified",
                },
                {
                    "question": "Why here?",
                    "question_regex": "^why",
                    "canonical_answer": "Because.",
                    "status": "proposed",
                },
            ],
        }
        lib = AnswerLibrary.model_validate(raw)
        assert len(lib.answers) == 2
        assert lib.answers[0].status == "verified"
        assert lib.answers[1].status == "proposed"
        assert lib.answers[1].question_regex == "^why"


def _write_valid_config(root: Path) -> None:
    (root / "configs").mkdir()
    (root / "configs" / "profiles").mkdir()
    (root / "resumes").mkdir()
    (root / "resumes" / "swe.yaml").write_text("name: SWE\n")
    (root / "configs" / "base_config.yaml").write_text(
        """
version: 1
static_answers:
  full_name: Test
  email: t@example.com
paths:
  resumes_dir: ../resumes
sources: []
"""
    )
    (root / "configs" / "prompts.yaml").write_text(
        """
version: 1
scoring: |
  s
summary: |
  s
cover_letter: |
  s
answer: |
  s
"""
    )
    (root / "configs" / "profiles" / "swe.yaml").write_text(
        "name: swe\nbase_resume: swe.yaml\nsources: []\n"
    )


class TestLoader:
    def test_missing_file_yields_empty_library(self, tmp_path: Path) -> None:
        _write_valid_config(tmp_path)
        loaded = load_config(tmp_path / "configs")
        assert isinstance(loaded.answer_library, AnswerLibrary)
        assert loaded.answer_library.answers == []

    def test_present_file_loads(self, tmp_path: Path) -> None:
        _write_valid_config(tmp_path)
        (tmp_path / "configs" / "answer_library.yaml").write_text(
            """
version: 1
answers:
  - question: Sample?
    canonical_answer: "Yes"
    status: verified
"""
        )
        loaded = load_config(tmp_path / "configs")
        assert len(loaded.answer_library.answers) == 1
        assert loaded.answer_library.answers[0].canonical_answer == "Yes"

    def test_invalid_file_raises_config_error(self, tmp_path: Path) -> None:
        _write_valid_config(tmp_path)
        (tmp_path / "configs" / "answer_library.yaml").write_text(
            """
version: 1
answers:
  - canonical_answer: missing question
"""
        )
        with pytest.raises(ConfigError, match="invalid answer library"):
            load_config(tmp_path / "configs")
