"""LLM-driven JD keyword extraction.

Takes a Job's description, hands it to the LLM with the operator-configured
``keyword_extraction`` prompt, and expects a JSON array of short terms back.
The parser is deliberately lenient about markdown fences and stray text
because keyword extraction is soft-critical: an empty list on parse failure
means "no matches from the bank this run", not a pipeline crash.
"""

from __future__ import annotations

import json
import logging

from magicapply.domain.models.job import Job
from magicapply.infrastructure.llm.client import LLMClient, LLMMessage, SystemBlock

logger = logging.getLogger(__name__)


class KeywordExtractor:
    def __init__(self, llm: LLMClient, extraction_prompt: str) -> None:
        self._llm = llm
        self._prompt = extraction_prompt

    def extract(self, job: Job) -> list[str]:
        if not self._prompt.strip():
            # No prompt configured -> no extraction. Empty list means the
            # matcher will find no bank entries this run, which is the same
            # outcome as an empty bank -- fine.
            return []

        system = [SystemBlock(text=self._prompt, cacheable=False)]
        user = LLMMessage(
            role="user",
            content=(
                f"JOB TITLE: {job.title}\n"
                f"COMPANY: {job.company}\n"
                f"\nDESCRIPTION:\n{job.description}\n"
            ),
        )
        result = self._llm.complete(
            system=system,
            messages=[user],
            max_tokens=500,
            temperature=0.0,
        )
        return _parse_terms(result.text)


def _parse_terms(raw: str) -> list[str]:
    """Parse a JSON array of strings from the LLM output.

    Tolerates markdown fences (```json ... ```) and stray whitespace.
    Returns [] on any failure -- extraction is best-effort, not gating.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
        text = text.rstrip("`").strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("keyword extraction: JSON parse failed: %s", exc)
        return []

    if not isinstance(data, list):
        return []

    out: list[str] = []
    for item in data:
        term = str(item).strip()
        if term:
            out.append(term)
    return out
