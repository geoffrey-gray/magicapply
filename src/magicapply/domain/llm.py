"""LLM client Protocol — domain contract for language model interactions.

This module defines the domain's interface for LLM interactions. Domain code
(scoring, tailoring, narrative generation, keyword extraction) depends on this
Protocol, not on concrete implementations.

Concrete implementations live in `infrastructure/llm/providers/` (Anthropic,
Mock, Replay, Ollama). The factory in `infrastructure/llm/factory.py` selects
the provider based on configuration.

**Architectural note:** This file exists to enforce the dependency rule that
domain code MUST NOT import from infrastructure. The Protocol lives in domain,
implementations live in infrastructure, and the dependency arrow points inward
(infrastructure → domain, not domain → infrastructure).

See ARCHITECTURE.md §3 and CLAUDE.md §Architectural Guardrails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    # Import LLMResult for type checking only - it's defined in infrastructure
    # but we need the return type annotation. This doesn't create a runtime
    # dependency (no circular import).
    from magicapply.infrastructure.llm.client import LLMResult


class SystemBlock(BaseModel):
    """One system-prompt block, optionally cache-controlled.

    The `cacheable` flag allows callers to mark reusable prefixes for prompt
    caching (Anthropic's cache_control feature). Put stable content first
    (base resume, static instructions) with `cacheable=True`, and volatile
    content last (specific job description) with `cacheable=False`.

    Cache reads are ~10% of base input cost; misses are ~1.25x. Anthropic's
    minimum cacheable prefix is model-dependent (~2K tokens for sonnet-4-6).
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    cacheable: bool = False


class LLMMessage(BaseModel):
    """One turn in the conversation. First message must be role=user."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str


class LLMClient(Protocol):
    """Domain Protocol for one-shot LLM completion.

    Non-streaming by design for the MVP. Concrete implementations handle:
    - Anthropic API calls with prompt caching
    - Mock responses for testing
    - Replay from golden files
    - Ollama (Phase 2 stub)

    Downstream domain code (JobScorer, Tailorer, NarrativeEngine, KeywordExtractor)
    depends on this Protocol, not on concrete classes.
    """

    def complete(
        self,
        *,
        system: list[SystemBlock] | None,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        """Complete a prompt and return result with token counts.

        Returns LLMResult (defined in infrastructure layer) with text and
        token usage. Domain code extracts the text; infrastructure tracks
        the token metrics.
        """
        ...


# LLMResult is NOT defined here — it lives in infrastructure/llm/client.py
# because domain code doesn't need to construct or inspect it beyond extracting
# the .text field. The Protocol's return type is a forward reference.
