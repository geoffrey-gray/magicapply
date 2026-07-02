"""Narrative generation: cover letters and open-ended screening answers.

Both flows use the same caching-friendly shape: base resume in a cacheable
SystemBlock so an entire discovery run's narratives share the same cache prefix.
Callers pick style (`concise` | `detailed`) matching the profile's
`apply.narrative_style` config.
"""

from __future__ import annotations

from typing import Literal

from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import BaseResume
from magicapply.domain.resumes.tailor import _serialize_resume
from magicapply.infrastructure.llm.client import LLMClient, LLMMessage, SystemBlock

NarrativeStyle = Literal["concise", "detailed"]

_CONCISE_STYLE = "Style: 2-3 short paragraphs. No filler."
_DETAILED_STYLE = "Style: 4-5 substantive paragraphs. Include one concrete example."

_COVER_LETTER_INSTRUCTIONS = """\
You write cover letters that sound like the candidate — not like an AI.

Rules:
- Use ONLY facts from the base resume. Do NOT invent employers, dates, or metrics.
- Do NOT use "As an AI", "I'm excited to apply", or other AI-tell openers.
- No em dashes as sentence connectors.
- Return ONLY the cover letter body. No greeting header, no signature block."""

_ANSWER_INSTRUCTIONS = """\
You answer open-ended screening questions on behalf of the candidate.

Rules:
- Use ONLY facts from the base resume.
- Answer the question directly. No preamble.
- Return ONLY the answer text."""


class NarrativeEngine:
    """LLM-backed cover letter + screening answer generator."""

    def __init__(
        self,
        llm: LLMClient,
        base: BaseResume,
        style: NarrativeStyle = "concise",
    ) -> None:
        self._llm = llm
        self._base_text = _serialize_resume(base)
        self._style = style

    def cover_letter(self, job: Job) -> str:
        style_note = _CONCISE_STYLE if self._style == "concise" else _DETAILED_STYLE
        system = [
            SystemBlock(text=f"{_COVER_LETTER_INSTRUCTIONS}\n\n{style_note}", cacheable=False),
            SystemBlock(text=f"BASE RESUME:\n{self._base_text}", cacheable=True),
        ]
        user = (
            f"JOB TITLE: {job.title}\n"
            f"COMPANY: {job.company}\n"
            f"\nDESCRIPTION:\n{job.description}\n"
            f"\nWrite the cover letter."
        )
        result = self._llm.complete(
            system=system,
            messages=[LLMMessage(role="user", content=user)],
            max_tokens=1000,
            temperature=0.5,
        )
        return result.text.strip()

    def answer(self, job: Job, question: str) -> str:
        system = [
            SystemBlock(text=_ANSWER_INSTRUCTIONS, cacheable=False),
            SystemBlock(text=f"BASE RESUME:\n{self._base_text}", cacheable=True),
        ]
        user = f"JOB TITLE: {job.title}\nCOMPANY: {job.company}\n\nQUESTION:\n{question}\n"
        result = self._llm.complete(
            system=system,
            messages=[LLMMessage(role="user", content=user)],
            max_tokens=500,
            temperature=0.3,
        )
        return result.text.strip()
