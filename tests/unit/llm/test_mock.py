"""Unit tests for MockLLMClient — canned and shape-aware modes."""

from __future__ import annotations

import json

import pytest

from magicapply.config.models import PromptsConfig
from magicapply.infrastructure.llm.client import LLMMessage, SystemBlock
from magicapply.infrastructure.llm.providers.mock import MockLLMClient


def _prompts() -> PromptsConfig:
    return PromptsConfig(
        scoring="You are a strict but fair evaluator scoring how well a job fits.",
        summary="You rewrite a resume summary to align with a specific job.",
        cover_letter="You write cover letters that sound like the candidate.",
        answer="You answer open-ended screening questions on behalf of the candidate.",
    )


def _sys(text: str) -> list[SystemBlock]:
    return [SystemBlock(text=text)]


def _msg(text: str) -> list[LLMMessage]:
    return [LLMMessage(role="user", content=text)]


class TestCannedMode:
    def test_returns_str_response(self) -> None:
        client = MockLLMClient("canned")
        result = client.complete(system=None, messages=_msg("hi"))
        assert result.text == "canned"

    def test_returns_list_in_order(self) -> None:
        client = MockLLMClient(["a", "b"])
        assert client.complete(system=None, messages=_msg("q")).text == "a"
        assert client.complete(system=None, messages=_msg("q")).text == "b"

    def test_exhaustion_raises(self) -> None:
        client = MockLLMClient(["only"])
        client.complete(system=None, messages=_msg("q"))
        with pytest.raises(RuntimeError, match="ran out"):
            client.complete(system=None, messages=_msg("q"))

    def test_records_calls(self) -> None:
        client = MockLLMClient("x")
        client.complete(system=_sys("sys"), messages=_msg("hello"), max_tokens=50)
        assert len(client.calls) == 1
        assert client.calls[0].max_tokens == 50


class TestConstructionValidation:
    def test_requires_responses_or_prompts(self) -> None:
        with pytest.raises(ValueError, match="responses or a PromptsConfig"):
            MockLLMClient()


class TestShapeAwareMode:
    def test_scoring_prompt_returns_valid_json(self) -> None:
        prompts = _prompts()
        client = MockLLMClient(prompts=prompts)
        result = client.complete(
            system=_sys(prompts.scoring),
            messages=_msg("JOB TITLE: Senior SWE\nCOMPANY: Acme\n"),
        )
        parsed = json.loads(result.text)
        assert isinstance(parsed["score"], int)
        assert 0 <= parsed["score"] <= 100
        assert isinstance(parsed["rationale"], str)

    def test_summary_prompt_returns_short_text(self) -> None:
        prompts = _prompts()
        client = MockLLMClient(prompts=prompts)
        result = client.complete(
            system=_sys(prompts.summary),
            messages=_msg("JOB TITLE: Backend Engineer\nCOMPANY: Acme\n"),
        )
        # Templated summary uses the parsed job title.
        assert "Backend Engineer" in result.text
        # No markdown, one-to-two sentences.
        assert "```" not in result.text

    def test_cover_letter_prompt_returns_paragraphs(self) -> None:
        prompts = _prompts()
        client = MockLLMClient(prompts=prompts)
        result = client.complete(
            system=_sys(prompts.cover_letter),
            messages=_msg("JOB TITLE: Data Eng\nCOMPANY: Acme\n"),
        )
        assert "Data Eng" in result.text
        assert "Acme" in result.text
        assert "\n\n" in result.text  # at least two paragraphs

    def test_answer_prompt_returns_direct_answer(self) -> None:
        prompts = _prompts()
        client = MockLLMClient(prompts=prompts)
        result = client.complete(
            system=_sys(prompts.answer),
            messages=_msg("JOB TITLE: SRE\nCOMPANY: Acme\n\nQUESTION:\nHow many years of Python?"),
        )
        # Answer echoes the question keyword.
        assert "python" in result.text.lower()

    def test_unknown_shape_raises(self) -> None:
        client = MockLLMClient(prompts=_prompts())
        with pytest.raises(RuntimeError, match="unrecognized prompt"):
            client.complete(system=_sys("some other instructions"), messages=_msg("hi"))
