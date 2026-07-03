"""Deterministic fixture-replay LLMClient for golden-file tests.

Keys each request by a SHA256 hash of its concatenated prompt (system blocks
+ user messages). Looks up `<fixtures_dir>/<key>.yaml`. On a hit, returns the
stored response. On a miss, either:

- captures the response from a fallback client and writes a new fixture
  (`record=True` or `MAGICAPPLY_LLM_RECORD=1`), or
- falls through to the fallback if one is provided but not recording, or
- raises `MissingFixture` if there is nowhere to route the request.

Fixture format:
    prompt_preview: "first 200 chars of the concatenated prompt (for humans)"
    response: "canned response body"
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

from magicapply.infrastructure.llm.client import (
    LLMClient,
    LLMMessage,
    LLMResult,
    SystemBlock,
)


class MissingFixture(RuntimeError):
    """Raised when the replay client has no fixture and no fallback for a request."""


class ReplayLLMClient:
    """Reads canned LLM responses from disk, keyed by prompt hash."""

    def __init__(
        self,
        fixtures_dir: Path,
        *,
        fallback: LLMClient | None = None,
        record: bool | None = None,
    ) -> None:
        self._dir = Path(fixtures_dir)
        self._fallback = fallback
        self._record = _resolve_record_flag(record)
        if self._record:
            self._dir.mkdir(parents=True, exist_ok=True)

    def complete(
        self,
        *,
        system: list[SystemBlock] | None,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        key = _prompt_key(system, messages)
        path = self._dir / f"{key}.yaml"

        if path.exists():
            return _result_from_fixture(path)

        if self._record:
            if self._fallback is None:
                raise MissingFixture(
                    f"replay: recording requested but no fallback client for key {key}"
                )
            result = self._fallback.complete(
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            _write_fixture(path, system, messages, result.text)
            return result

        if self._fallback is not None:
            return self._fallback.complete(
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        raise MissingFixture(
            f"replay: no fixture for key {key} and no fallback provided. "
            f"Preview: {_prompt_preview(system, messages)[:200]!r}"
        )


def _resolve_record_flag(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    return os.environ.get("MAGICAPPLY_LLM_RECORD", "0") == "1"


def _prompt_key(system: list[SystemBlock] | None, messages: list[LLMMessage]) -> str:
    concatenated = _prompt_preview(system, messages, limit=None)
    return hashlib.sha256(concatenated.encode("utf-8")).hexdigest()[:16]


def _prompt_preview(
    system: list[SystemBlock] | None,
    messages: list[LLMMessage],
    *,
    limit: int | None = 200,
) -> str:
    parts: list[str] = []
    if system:
        parts.extend(block.text for block in system)
    parts.extend(f"{msg.role}:{msg.content}" for msg in messages)
    joined = "\n---\n".join(parts)
    return joined if limit is None else joined[:limit]


def _write_fixture(
    path: Path,
    system: list[SystemBlock] | None,
    messages: list[LLMMessage],
    response: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "prompt_preview": _prompt_preview(system, messages, limit=200),
        "response": response,
    }
    with path.open("w", encoding="utf-8") as fp:
        yaml.safe_dump(payload, fp, sort_keys=False, allow_unicode=True)


def _result_from_fixture(path: Path) -> LLMResult:
    with path.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp)
    if not isinstance(raw, dict) or "response" not in raw:
        raise MissingFixture(f"replay fixture at {path} is missing a 'response' field")
    text = str(raw["response"])
    return LLMResult(
        text=text,
        input_tokens=len(text) // 4,  # rough token proxy; enough for tests
        output_tokens=max(1, len(text.split())),
    )
