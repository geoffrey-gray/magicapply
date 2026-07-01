"""Anthropic-backed LLMClient with prompt caching from day one.

Cacheable SystemBlocks get `cache_control: {type: "ephemeral"}`. The tokens
Anthropic reports for cache creation vs read propagate onto LLMResult so the
pipeline can log cache hit ratio.

Non-streaming intentionally — MVP does one-shot completions where the whole
response is used at once (bullet rewrite, cover letter). Streaming lands in a
later phase if narrative UX ever needs it.
"""

from __future__ import annotations

import os
from typing import Any

import anthropic

from magicapply.config.models import LLMConfig
from magicapply.infrastructure.llm.client import LLMMessage, LLMResult, SystemBlock


class AnthropicProvider:
    """LLMClient impl. `client` may be injected for tests."""

    def __init__(
        self,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        client: anthropic.Anthropic | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = client or anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
        )
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature

    @classmethod
    def from_config(
        cls,
        config: LLMConfig,
        *,
        client: anthropic.Anthropic | None = None,
    ) -> AnthropicProvider:
        return cls(
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            client=client,
        )

    def complete(
        self,
        *,
        system: list[SystemBlock] | None,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        system_param = _build_system(system) if system else anthropic.NOT_GIVEN
        message_param = [{"role": m.role, "content": m.content} for m in messages]

        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
            temperature=temperature if temperature is not None else self._temperature,
            system=system_param,
            messages=message_param,
        )

        text = "".join(b.text for b in response.content if b.type == "text")
        usage = response.usage
        return LLMResult(
            text=text,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )


def _build_system(blocks: list[SystemBlock]) -> list[dict[str, Any]]:
    """Convert SystemBlocks to Anthropic's text-block param, marking cacheables."""
    out: list[dict[str, Any]] = []
    for b in blocks:
        block: dict[str, Any] = {"type": "text", "text": b.text}
        if b.cacheable:
            block["cache_control"] = {"type": "ephemeral"}
        out.append(block)
    return out
