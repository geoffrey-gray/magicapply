"""Tests for the LLM client factory."""

from __future__ import annotations

import pytest

from magicapply.config.models import LLMConfig
from magicapply.infrastructure.llm import build_client
from magicapply.infrastructure.llm.providers.anthropic import AnthropicProvider
from magicapply.infrastructure.llm.providers.ollama import OllamaProvider


class TestFactory:
    def test_anthropic_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
        cfg = LLMConfig(provider="anthropic", model="claude-sonnet-4-6")
        client = build_client(cfg)
        assert isinstance(client, AnthropicProvider)

    def test_ollama_provider(self) -> None:
        cfg = LLMConfig(provider="ollama", model="llama3")
        client = build_client(cfg)
        assert isinstance(client, OllamaProvider)
