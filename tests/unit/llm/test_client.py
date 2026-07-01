"""Tests for LLMClient shared types and the mock client."""

from __future__ import annotations

import pytest

from magicapply.infrastructure.llm import (
    LLMMessage,
    LLMResult,
    MockLLMClient,
    SystemBlock,
)


class TestLLMResult:
    def test_cache_hit_ratio_no_tokens(self) -> None:
        r = LLMResult(text="", input_tokens=0, output_tokens=0)
        assert r.cache_hit_ratio == 0.0

    def test_cache_hit_ratio_all_read(self) -> None:
        r = LLMResult(
            text="",
            input_tokens=0,
            output_tokens=1,
            cache_read_tokens=100,
        )
        assert r.cache_hit_ratio == 1.0

    def test_cache_hit_ratio_mixed(self) -> None:
        r = LLMResult(
            text="",
            input_tokens=50,
            output_tokens=1,
            cache_read_tokens=150,
        )
        assert r.cache_hit_ratio == 0.75


class TestMockClient:
    def test_returns_canned_responses_in_order(self) -> None:
        m = MockLLMClient(["first", "second"])
        r1 = m.complete(system=None, messages=[LLMMessage(role="user", content="a")])
        r2 = m.complete(system=None, messages=[LLMMessage(role="user", content="b")])
        assert r1.text == "first"
        assert r2.text == "second"

    def test_records_calls(self) -> None:
        m = MockLLMClient("ok")
        sys_blocks = [SystemBlock(text="you are helpful", cacheable=True)]
        msgs = [LLMMessage(role="user", content="hi")]
        m.complete(system=sys_blocks, messages=msgs, max_tokens=100, temperature=0.5)
        assert len(m.calls) == 1
        assert m.calls[0].system == sys_blocks
        assert m.calls[0].messages == msgs
        assert m.calls[0].max_tokens == 100
        assert m.calls[0].temperature == 0.5

    def test_out_of_responses_raises(self) -> None:
        m = MockLLMClient("only")
        m.complete(system=None, messages=[LLMMessage(role="user", content="x")])
        with pytest.raises(RuntimeError, match="ran out"):
            m.complete(system=None, messages=[LLMMessage(role="user", content="y")])

    def test_single_response_string_shorthand(self) -> None:
        m = MockLLMClient("solo")
        r = m.complete(system=None, messages=[LLMMessage(role="user", content="q")])
        assert r.text == "solo"
        assert r.output_tokens >= 1
