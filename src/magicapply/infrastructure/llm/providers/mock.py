"""Deterministic in-memory LLMClient for tests and dry runs."""

from __future__ import annotations

from dataclasses import dataclass, field

from magicapply.infrastructure.llm.client import LLMMessage, LLMResult, SystemBlock


@dataclass
class _Call:
    system: list[SystemBlock] | None
    messages: list[LLMMessage]
    max_tokens: int | None
    temperature: float | None


class MockLLMClient:
    """LLMClient that returns canned responses in order and records every call."""

    def __init__(self, responses: str | list[str], *, input_tokens_each: int = 100) -> None:
        self._responses: list[str] = [responses] if isinstance(responses, str) else list(responses)
        self._input_tokens = input_tokens_each
        self.calls: list[_Call] = field(default_factory=list) if False else []

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
        if not self._responses:
            raise RuntimeError("MockLLMClient ran out of canned responses")
        text = self._responses.pop(0)
        return LLMResult(
            text=text,
            input_tokens=self._input_tokens,
            output_tokens=max(1, len(text.split())),
        )
