"""Deterministic in-memory LLMClient for tests and dry runs.

Two modes on the same class:

- **Canned mode** (default; tests): pass `responses=` and the mock yields them
  in order. Every call is recorded on `.calls` for post-hoc inspection.
- **Shape-aware mode** (dry runs): pass `prompts=` (a `PromptsConfig`) and the
  mock detects which of the four prompt shapes the caller is sending by
  substring-matching the first system block against the configured prompt
  strings, then returns a plausible-looking response for that shape. Lets a
  full run of the pipeline (discover → tailor → apply) execute end-to-end
  without an API key while still producing realistic-shaped artifacts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from magicapply.config.models import PromptsConfig
from magicapply.infrastructure.llm.client import LLMMessage, LLMResult, SystemBlock


@dataclass
class _Call:
    system: list[SystemBlock] | None
    messages: list[LLMMessage]
    max_tokens: int | None
    temperature: float | None


class MockLLMClient:
    """LLMClient that returns canned or shape-appropriate responses.

    Pass exactly one of `responses` or `prompts`:
    - `responses="{...}"` or `responses=["a", "b"]` for tests that want to
      control every reply.
    - `prompts=loaded.prompts` for dry-run pipelines that need the mock to
      pick a shape-appropriate reply on its own.
    """

    def __init__(
        self,
        responses: str | list[str] | None = None,
        *,
        input_tokens_each: int = 100,
        prompts: PromptsConfig | None = None,
    ) -> None:
        if responses is None and prompts is None:
            raise ValueError(
                "MockLLMClient needs either explicit responses or a PromptsConfig "
                "for shape-aware detection"
            )
        if responses is None:
            self._responses: list[str] | None = None
        elif isinstance(responses, str):
            self._responses = [responses]
        else:
            self._responses = list(responses)
        self._input_tokens = input_tokens_each
        self._prompts = prompts
        self.calls: list[_Call] = []

    def complete(
        self,
        *,
        system: list[SystemBlock] | None,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        self.calls.append(
            _Call(
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        )
        if self._responses is not None:
            if not self._responses:
                raise RuntimeError("MockLLMClient ran out of canned responses")
            text = self._responses.pop(0)
        else:
            text = self._shape_response(system, messages)
        return LLMResult(
            text=text,
            input_tokens=self._input_tokens,
            output_tokens=max(1, len(text.split())),
        )

    def _shape_response(
        self,
        system: list[SystemBlock] | None,
        messages: list[LLMMessage],
    ) -> str:
        assert self._prompts is not None
        first_system = system[0].text if system else ""
        user_text = messages[-1].content if messages else ""

        if self._prompts.scoring in first_system:
            return '{"score": 82, "rationale": "mock: strong keyword overlap"}'
        if self._prompts.summary in first_system:
            return _mock_summary(user_text)
        if self._prompts.cover_letter in first_system:
            return _mock_cover_letter(user_text)
        if self._prompts.answer in first_system:
            return _mock_answer(user_text)
        if (
            self._prompts.keyword_extraction
            and self._prompts.keyword_extraction in first_system
        ):
            return '["python", "distributed systems", "microservices"]'

        raise RuntimeError(
            "MockLLMClient shape-aware mode saw an unrecognized prompt. "
            f"First system block preview: {first_system[:200]!r}"
        )


_TITLE_RE = re.compile(r"^JOB TITLE:\s*(.+)$", re.MULTILINE)
_COMPANY_RE = re.compile(r"^COMPANY:\s*(.+)$", re.MULTILINE)
_QUESTION_RE = re.compile(r"QUESTION:\s*(.+)", re.DOTALL)


def _title(user_text: str) -> str:
    m = _TITLE_RE.search(user_text)
    return m.group(1).strip() if m else "the role"


def _company(user_text: str) -> str:
    m = _COMPANY_RE.search(user_text)
    return m.group(1).strip() if m else "the company"


def _mock_summary(user_text: str) -> str:
    return (
        f"Engineer with focused experience relevant to {_title(user_text)}. "
        f"Track record delivering measurable outcomes at growth-stage teams."
    )


def _mock_cover_letter(user_text: str) -> str:
    return (
        f"I read the {_title(user_text)} posting at {_company(user_text)} "
        f"and see a strong match with the work I have done recently. "
        f"My background covers the core areas the role emphasises.\n\n"
        f"I would welcome the chance to talk about how I could contribute. "
        f"Thanks for considering my application."
    )


def _mock_answer(user_text: str) -> str:
    m = _QUESTION_RE.search(user_text)
    question_preview = m.group(1).strip().splitlines()[0] if m else "the question"
    return f"Yes — based on my resume, I can address {question_preview.lower()}"
