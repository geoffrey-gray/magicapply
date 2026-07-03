"""Build a concrete LLMClient from LLMConfig."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import LLMConfig, PromptsConfig
from magicapply.infrastructure.llm.client import LLMClient
from magicapply.infrastructure.llm.providers.anthropic import AnthropicProvider
from magicapply.infrastructure.llm.providers.mock import MockLLMClient
from magicapply.infrastructure.llm.providers.ollama import OllamaProvider
from magicapply.infrastructure.llm.providers.replay import ReplayLLMClient


def build_client(
    config: LLMConfig,
    *,
    prompts: PromptsConfig | None = None,
) -> LLMClient:
    """Return the LLMClient implementation for `config.provider`.

    `prompts` is required for `provider=mock` (shape-aware mode) and for
    `provider=replay` (used to build the replay fallback so uncaptured
    prompts still resolve to something). The real providers ignore it.

    Callers store the returned value against the LLMClient Protocol, not
    the concrete class.
    """
    if config.provider == "anthropic":
        return AnthropicProvider.from_config(config)
    if config.provider == "ollama":
        return OllamaProvider(config)
    if config.provider == "mock":
        return MockLLMClient(prompts=prompts)
    if config.provider == "replay":
        fixtures_dir = Path(str(config.options.get("fixtures_dir", "configs/llm-fixtures")))
        fallback = MockLLMClient(prompts=prompts) if prompts is not None else None
        return ReplayLLMClient(fixtures_dir, fallback=fallback)
    raise ValueError(f"unknown LLM provider: {config.provider!r}")
