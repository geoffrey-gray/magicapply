"""Live Anthropic API tests. Opt-in.

Requires:
  MAGICAPPLY_LIVE_TESTS=1
  ANTHROPIC_API_KEY=...

Verifies:
  - the API is reachable and the config's default model is available
  - our cache_control wiring actually produces cache hits on the second call
    (Anthropic's minimum cacheable prefix on sonnet-4-6 is ~2K tokens, so we
    pad the system prompt to comfortably exceed that)
"""

from __future__ import annotations

import pytest

from magicapply.config.models import LLMConfig
from magicapply.infrastructure.llm import AnthropicProvider, LLMMessage, SystemBlock

pytestmark = [pytest.mark.integration, pytest.mark.anthropic]


def _long_system_prefix() -> str:
    """Deterministic ~3K+ token prefix so cache_control is above the min."""
    # Roughly 4 chars per token → aim for ~15K chars.
    seed = (
        "You are an assistant that reviews resumes against job descriptions. "
        "Consider the candidate's experience, skills, and impact. Prefer "
        "quantitative evidence. Do not invent facts. Be terse.\n"
    )
    return seed * 200


def test_live_completion_and_caching() -> None:
    cfg = LLMConfig(provider="anthropic", model="claude-sonnet-4-6", max_tokens=64)
    provider = AnthropicProvider.from_config(cfg)

    system = [SystemBlock(text=_long_system_prefix(), cacheable=True)]

    first = provider.complete(
        system=system,
        messages=[LLMMessage(role="user", content="Say the single word: hello.")],
    )
    assert first.text.strip()
    # First call writes the cache OR finds an existing one from a prior run.
    assert first.cache_creation_tokens + first.cache_read_tokens > 0

    second = provider.complete(
        system=system,
        messages=[LLMMessage(role="user", content="Say the single word: world.")],
    )
    # Second call with same cacheable prefix must read from cache.
    assert second.cache_read_tokens > 0, (
        f"expected cache hit; got creation={second.cache_creation_tokens} "
        f"read={second.cache_read_tokens} input={second.input_tokens}"
    )
