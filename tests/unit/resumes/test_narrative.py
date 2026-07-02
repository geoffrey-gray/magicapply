"""Tests for NarrativeEngine."""

from __future__ import annotations

from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import BaseResume
from magicapply.domain.resumes.narrative import NarrativeEngine
from magicapply.infrastructure.llm.providers.mock import MockLLMClient


def _base() -> BaseResume:
    return BaseResume(name="Test Person", email="t@example.com")


def _job() -> Job:
    return Job.new(source_name="s", url="https://a.com/1", title="SWE", company="Acme")


class TestCoverLetter:
    def test_returns_llm_text(self) -> None:
        llm = MockLLMClient("Dear Acme,\n\nHere is why I am a fit...")
        engine = NarrativeEngine(llm, _base())
        letter = engine.cover_letter(_job())
        assert "Dear Acme" in letter

    def test_style_note_reflected_in_system_prompt(self) -> None:
        llm = MockLLMClient("body")
        engine = NarrativeEngine(llm, _base(), style="detailed")
        engine.cover_letter(_job())
        call = llm.calls[0]
        assert call.system is not None
        assert any("4-5" in b.text for b in call.system)

    def test_resume_in_cacheable_block(self) -> None:
        llm = MockLLMClient("body")
        engine = NarrativeEngine(llm, _base())
        engine.cover_letter(_job())
        call = llm.calls[0]
        assert any(b.cacheable and "BASE RESUME" in b.text for b in call.system)


class TestAnswer:
    def test_forwards_question_to_llm(self) -> None:
        llm = MockLLMClient("5 years in Python.")
        engine = NarrativeEngine(llm, _base())
        answer = engine.answer(_job(), "How many years of Python?")
        assert answer == "5 years in Python."
        call = llm.calls[0]
        assert "QUESTION" in call.messages[0].content
        assert "How many years" in call.messages[0].content
