"""Narrative generation: cover letters and open-ended screening answers.

Both flows use the same caching-friendly shape: base resume in a cacheable
SystemBlock so an entire discovery run's narratives share the same cache prefix.
Callers pick style (`concise` | `detailed`) matching the profile's
`apply.narrative_style` config.
"""

from __future__ import annotations

from typing import Literal

from magicapply.domain.llm import LLMClient, LLMMessage, SystemBlock
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import BaseResume
from magicapply.domain.resumes.tailor import _serialize_resume

NarrativeStyle = Literal["concise", "detailed"]

_CONCISE_STYLE = "Style: 2-3 short paragraphs. No filler."
_DETAILED_STYLE = "Style: 4-5 substantive paragraphs. Include one concrete example."


class NarrativeEngine:
    """LLM-backed cover letter + screening answer generator."""

    def __init__(
        self,
        llm: LLMClient,
        base: BaseResume,
        style: NarrativeStyle = "concise",
        *,
        cover_letter_prompt: str,
        answer_prompt: str,
    ) -> None:
        self._llm = llm
        self._base_text = _serialize_resume(base)
        self._style = style
        self._cover_letter_prompt = cover_letter_prompt
        self._answer_prompt = answer_prompt

    def cover_letter(self, job: Job) -> str:
        style_note = _CONCISE_STYLE if self._style == "concise" else _DETAILED_STYLE
        system = [
            SystemBlock(text=f"{self._cover_letter_prompt}\n\n{style_note}", cacheable=False),
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
            SystemBlock(text=self._answer_prompt, cacheable=False),
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
