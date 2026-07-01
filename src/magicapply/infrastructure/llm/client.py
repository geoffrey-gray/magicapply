"""LLM Protocol and shared types.

The `system` field takes a list of `SystemBlock`s rather than a single string so
callers can mark reusable prefixes as `cacheable=True`. The AnthropicProvider
then attaches `cache_control` on those blocks — critical for MagicApply where a
base resume + prompt is reused across every job in a discovery run.

Prompt caching design (see docs/GOF_PATTERNS.md and CLAUDE.md):

- Put stable content first (base resume, static instructions) — `cacheable=True`
- Put volatile content last (the specific job description) — `cacheable=False`

Cache reads are ~10% of base input cost; misses are ~1.25x. Anthropic's
minimum cacheable prefix is model-dependent (~2K tokens for sonnet-4-6).
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class SystemBlock(BaseModel):
    """One system-prompt block, optionally cache-controlled."""

    model_config = ConfigDict(extra="forbid")

    text: str
    cacheable: bool = False


class LLMMessage(BaseModel):
    """One turn in the conversation. First message must be role=user."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str


class LLMResult(BaseModel):
    """Return shape from every LLMClient.complete call.

    Cache-token fields default to 0 so non-Anthropic providers don't have to
    populate them.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_creation_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)

    @property
    def cache_hit_ratio(self) -> float:
        """Cache-read tokens / total prompt tokens. 0.0 if no prompt tokens."""
        total = self.input_tokens + self.cache_read_tokens + self.cache_creation_tokens
        return self.cache_read_tokens / total if total else 0.0


class LLMClient(Protocol):
    """One-shot completion. Non-streaming by design for the MVP.

    Concrete implementations live under `providers/`. Downstream domain code
    depends on this Protocol, not on a concrete class.
    """

    def complete(
        self,
        *,
        system: list[SystemBlock] | None,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult: ...
