"""Ollama provider — stub for Phase 2 of the roadmap.

Kept as a placeholder so the LLMClient factory has both providers wired at the
composition-root level and the config schema doesn't lie about supporting Ollama.
"""

from __future__ import annotations

from magicapply.config.models import LLMConfig
from magicapply.infrastructure.llm.client import LLMMessage, LLMResult, SystemBlock


class OllamaProvider:
    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    def complete(
        self,
        *,
        system: list[SystemBlock] | None,
        messages: list[LLMMessage],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        raise NotImplementedError(
            "OllamaProvider is a stub — implementation ships in Phase 2 of the roadmap."
        )
