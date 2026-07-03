"""Unit tests for ReplayLLMClient — fixture lookup, record, fallback, misses."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from magicapply.infrastructure.llm.client import LLMMessage, SystemBlock
from magicapply.infrastructure.llm.providers.mock import MockLLMClient
from magicapply.infrastructure.llm.providers.replay import (
    MissingFixture,
    ReplayLLMClient,
)


def _msg(text: str) -> list[LLMMessage]:
    return [LLMMessage(role="user", content=text)]


def _sys(text: str) -> list[SystemBlock]:
    return [SystemBlock(text=text)]


class TestKeyStability:
    def test_same_prompt_produces_same_key(self, tmp_path: Path) -> None:
        # Record from a canned fallback, then read back — proves key stability
        # (the recorded fixture is looked up under the same key on the next call).
        client = ReplayLLMClient(
            tmp_path, fallback=MockLLMClient(["recorded"]), record=True
        )
        r1 = client.complete(system=_sys("s"), messages=_msg("hello"))
        # Now a fresh client (no record, no fallback) should still find it.
        client2 = ReplayLLMClient(tmp_path)
        r2 = client2.complete(system=_sys("s"), messages=_msg("hello"))
        assert r1.text == r2.text == "recorded"

    def test_different_prompt_produces_different_key(self, tmp_path: Path) -> None:
        rec = ReplayLLMClient(
            tmp_path, fallback=MockLLMClient(["a", "b"]), record=True
        )
        rec.complete(system=_sys("sys"), messages=_msg("prompt one"))
        rec.complete(system=_sys("sys"), messages=_msg("prompt two"))
        yaml_files = list(tmp_path.glob("*.yaml"))
        assert len(yaml_files) == 2  # two distinct keys, two files


class TestRecording:
    def test_records_fixture_when_missing(self, tmp_path: Path) -> None:
        client = ReplayLLMClient(
            tmp_path, fallback=MockLLMClient(["captured"]), record=True
        )
        client.complete(system=_sys("s"), messages=_msg("q"))
        yaml_files = list(tmp_path.glob("*.yaml"))
        assert len(yaml_files) == 1
        with yaml_files[0].open() as fp:
            data = yaml.safe_load(fp)
        assert data["response"] == "captured"
        assert "prompt_preview" in data

    def test_record_env_var_triggers_record_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGICAPPLY_LLM_RECORD", "1")
        client = ReplayLLMClient(tmp_path, fallback=MockLLMClient(["env"]))
        client.complete(system=_sys("s"), messages=_msg("q"))
        assert list(tmp_path.glob("*.yaml"))

    def test_record_without_fallback_raises(self, tmp_path: Path) -> None:
        client = ReplayLLMClient(tmp_path, record=True)
        with pytest.raises(MissingFixture, match="no fallback"):
            client.complete(system=_sys("s"), messages=_msg("q"))


class TestFallthrough:
    def test_missing_fixture_uses_fallback_when_not_recording(
        self, tmp_path: Path
    ) -> None:
        client = ReplayLLMClient(
            tmp_path, fallback=MockLLMClient(["from-fallback"]), record=False
        )
        result = client.complete(system=_sys("s"), messages=_msg("q"))
        assert result.text == "from-fallback"
        # Not recorded — no yaml files written.
        assert not list(tmp_path.glob("*.yaml"))

    def test_missing_fixture_no_fallback_raises(self, tmp_path: Path) -> None:
        client = ReplayLLMClient(tmp_path)
        with pytest.raises(MissingFixture, match="no fixture"):
            client.complete(system=_sys("s"), messages=_msg("q"))


class TestFixtureIntegrity:
    def test_malformed_fixture_raises(self, tmp_path: Path) -> None:
        # Exercise ReplayLLMClient once to write a fixture, corrupt it, then
        # verify a reader detects the missing 'response' field.
        recorder = ReplayLLMClient(
            tmp_path, fallback=MockLLMClient(["ok"]), record=True
        )
        recorder.complete(system=_sys("s"), messages=_msg("q"))
        [fixture] = list(tmp_path.glob("*.yaml"))
        fixture.write_text("prompt_preview: preview\n")  # no response field
        reader = ReplayLLMClient(tmp_path)
        with pytest.raises(MissingFixture, match="missing a 'response' field"):
            reader.complete(system=_sys("s"), messages=_msg("q"))
