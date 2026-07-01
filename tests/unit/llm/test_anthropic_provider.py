"""Tests for AnthropicProvider request shape (esp. prompt caching)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from magicapply.config.models import LLMConfig
from magicapply.infrastructure.llm.client import LLMMessage, SystemBlock
from magicapply.infrastructure.llm.providers.anthropic import AnthropicProvider


def _make_response(text: str = "ok", **usage_over: int) -> SimpleNamespace:
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        cache_creation_input_tokens=usage_over.get("cache_creation_input_tokens", 0),
        cache_read_input_tokens=usage_over.get("cache_read_input_tokens", 0),
    )
    content = [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=content, usage=usage)


class TestSystemPromptCaching:
    def test_cacheable_blocks_get_cache_control(self) -> None:
        fake = MagicMock()
        fake.messages.create.return_value = _make_response()
        provider = AnthropicProvider(
            model="claude-sonnet-4-6", max_tokens=1024, temperature=0.3, client=fake
        )

        provider.complete(
            system=[
                SystemBlock(text="stable base resume", cacheable=True),
                SystemBlock(text="volatile per-request bit", cacheable=False),
            ],
            messages=[LLMMessage(role="user", content="score this JD")],
        )

        (_, kwargs) = fake.messages.create.call_args
        system_arg = kwargs["system"]
        assert system_arg[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in system_arg[1]

    def test_no_system_passes_not_given(self) -> None:
        import anthropic

        fake = MagicMock()
        fake.messages.create.return_value = _make_response()
        provider = AnthropicProvider(
            model="claude-sonnet-4-6", max_tokens=1024, temperature=0.3, client=fake
        )

        provider.complete(system=None, messages=[LLMMessage(role="user", content="hi")])
        (_, kwargs) = fake.messages.create.call_args
        assert kwargs["system"] is anthropic.NOT_GIVEN


class TestUsageMapping:
    def test_cache_tokens_propagate(self) -> None:
        fake = MagicMock()
        fake.messages.create.return_value = _make_response(
            cache_creation_input_tokens=200,
            cache_read_input_tokens=1000,
        )
        provider = AnthropicProvider(
            model="claude-sonnet-4-6", max_tokens=1024, temperature=0.3, client=fake
        )

        result = provider.complete(
            system=[SystemBlock(text="x", cacheable=True)],
            messages=[LLMMessage(role="user", content="y")],
        )
        assert result.cache_creation_tokens == 200
        assert result.cache_read_tokens == 1000
        assert result.input_tokens == 100
        assert result.output_tokens == 20

    def test_missing_cache_fields_default_to_zero(self) -> None:
        fake = MagicMock()
        # Response with no cache fields at all.
        usage = SimpleNamespace(input_tokens=100, output_tokens=20)
        content = [SimpleNamespace(type="text", text="ok")]
        fake.messages.create.return_value = SimpleNamespace(content=content, usage=usage)

        provider = AnthropicProvider(
            model="claude-sonnet-4-6", max_tokens=1024, temperature=0.3, client=fake
        )
        result = provider.complete(system=None, messages=[LLMMessage(role="user", content="y")])
        assert result.cache_creation_tokens == 0
        assert result.cache_read_tokens == 0


class TestRequestParams:
    def test_temperature_and_max_tokens_from_defaults(self) -> None:
        fake = MagicMock()
        fake.messages.create.return_value = _make_response()
        provider = AnthropicProvider(
            model="claude-sonnet-4-6", max_tokens=4096, temperature=0.3, client=fake
        )

        provider.complete(system=None, messages=[LLMMessage(role="user", content="hi")])
        (_, kwargs) = fake.messages.create.call_args
        assert kwargs["model"] == "claude-sonnet-4-6"
        assert kwargs["max_tokens"] == 4096
        assert kwargs["temperature"] == 0.3

    def test_per_call_overrides_win(self) -> None:
        fake = MagicMock()
        fake.messages.create.return_value = _make_response()
        provider = AnthropicProvider(
            model="claude-sonnet-4-6", max_tokens=4096, temperature=0.3, client=fake
        )

        provider.complete(
            system=None,
            messages=[LLMMessage(role="user", content="hi")],
            max_tokens=200,
            temperature=0.0,
        )
        (_, kwargs) = fake.messages.create.call_args
        assert kwargs["max_tokens"] == 200
        assert kwargs["temperature"] == 0.0


class TestFromConfig:
    def test_builds_from_llmconfig(self) -> None:
        cfg = LLMConfig(provider="anthropic", model="claude-sonnet-4-6", max_tokens=2048)
        provider = AnthropicProvider.from_config(cfg, client=MagicMock())
        assert provider._model == "claude-sonnet-4-6"
        assert provider._max_tokens == 2048
