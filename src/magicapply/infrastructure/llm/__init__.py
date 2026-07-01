"""LLM abstraction — Protocol, providers, and shared types."""

from magicapply.infrastructure.llm.client import (
    LLMClient,
    LLMMessage,
    LLMResult,
    SystemBlock,
)
from magicapply.infrastructure.llm.factory import build_client
from magicapply.infrastructure.llm.providers.anthropic import AnthropicProvider
from magicapply.infrastructure.llm.providers.mock import MockLLMClient
from magicapply.infrastructure.llm.providers.ollama import OllamaProvider

__all__ = [
    "AnthropicProvider",
    "LLMClient",
    "LLMMessage",
    "LLMResult",
    "MockLLMClient",
    "OllamaProvider",
    "SystemBlock",
    "build_client",
]
