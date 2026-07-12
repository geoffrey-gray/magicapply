"""LLM result types — infrastructure-layer return shapes.

The LLMClient Protocol and domain-used types (SystemBlock, LLMMessage) have been
moved to domain/llm.py to enforce the layering rule: domain MUST NOT import from
infrastructure.

This file retains LLMResult, which is infrastructure-only — domain code extracts
the .text field but doesn't need to construct or inspect token metrics.

For prompt caching design notes, see domain/llm.py.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Import domain types that define the Protocol contract
from magicapply.domain.llm import LLMClient, LLMMessage, SystemBlock

__all__ = ["LLMClient", "LLMMessage", "LLMResult", "SystemBlock"]


class LLMResult(BaseModel):
    """Return shape from every LLMClient.complete call.

    Cache-token fields default to 0 so non-Anthropic providers don't have to
    populate them.

    This class lives in infrastructure (not domain) because domain code only
    extracts the .text field. Infrastructure providers and factories construct
    and track token metrics.
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
