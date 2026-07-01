"""Build a concrete LLMClient from LLMConfig."""

from __future__ import annotations

from magicapply.config.models import LLMConfig
from magicapply.infrastructure.llm.client import LLMClient
from magicapply.infrastructure.llm.providers.anthropic import AnthropicProvider
from magicapply.infrastructure.llm.providers.ollama import OllamaProvider


def build_client(config: LLMConfig) -> LLMClient:
    """Return the LLMClient implementation for `config.provider`.

    Callers store the returned value against the LLMClient Protocol, not
    the concrete class.
    """
    if config.provider == "anthropic":
        return AnthropicProvider.from_config(config)
    if config.provider == "ollama":
        return OllamaProvider(config)
    raise ValueError(f"unknown LLM provider: {config.provider!r}")
