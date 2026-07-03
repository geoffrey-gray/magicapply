"""Tests for the LLM client factory."""

from __future__ import annotations

from pathlib import Path

import pytest

from magicapply.config.models import LLMConfig, PromptsConfig
from magicapply.infrastructure.llm import build_client
from magicapply.infrastructure.llm.providers.anthropic import AnthropicProvider
from magicapply.infrastructure.llm.providers.mock import MockLLMClient
from magicapply.infrastructure.llm.providers.ollama import OllamaProvider
from magicapply.infrastructure.llm.providers.replay import ReplayLLMClient


def _prompts() -> PromptsConfig:
    return PromptsConfig(
        scoring="score",
        summary="summary",
        cover_letter="cover letter",
        answer="answer",
    )


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

    def test_mock_provider_with_prompts(self) -> None:
        cfg = LLMConfig(provider="mock", model="whatever")
        client = build_client(cfg, prompts=_prompts())
        assert isinstance(client, MockLLMClient)

    def test_mock_provider_without_prompts_raises(self) -> None:
        cfg = LLMConfig(provider="mock", model="whatever")
        with pytest.raises(ValueError, match="responses or a PromptsConfig"):
            build_client(cfg)

    def test_replay_provider_uses_options_fixtures_dir(self, tmp_path: Path) -> None:
        cfg = LLMConfig(
            provider="replay",
            model="whatever",
            options={"fixtures_dir": str(tmp_path / "my-fixtures")},
        )
        client = build_client(cfg, prompts=_prompts())
        assert isinstance(client, ReplayLLMClient)

    def test_replay_provider_default_fixtures_dir(self) -> None:
        cfg = LLMConfig(provider="replay", model="whatever")
        client = build_client(cfg, prompts=_prompts())
        assert isinstance(client, ReplayLLMClient)

    def test_unknown_provider_raises(self) -> None:
        # The Literal only allows valid names; bypass Pydantic to hit factory's guard.
        cfg = LLMConfig.model_construct(provider="banana", model="x")
        with pytest.raises(ValueError, match="unknown LLM provider"):
            build_client(cfg)
