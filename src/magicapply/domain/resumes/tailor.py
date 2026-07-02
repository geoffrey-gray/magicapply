"""Resume tailoring — the Builder GoF pattern in practice.

`TailoredResumeBuilder` accumulates steps and produces a `TailoredResume` on
`build()`. Its API is deliberately small in MVP (summary rewrite,
bullet-reorder placeholder) but the shape is right for adding steps like
keyword injection, custom sections, or LLM-driven rewrites without changing
call sites. `Tailorer` is the composition root that wires the LLM into the
Builder — see docs/GOF_PATTERNS.md.

MVP scope discipline: no content invention. The summary can be rewritten but
experience bullets are only reordered — never reworded — to keep the resume
truthful.
"""

from __future__ import annotations

import logging
from typing import Self

from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import (
    BaseResume,
    ExperienceEntry,
    TailoredResume,
)
from magicapply.infrastructure.llm.client import LLMClient, LLMMessage, SystemBlock

logger = logging.getLogger(__name__)

_SUMMARY_INSTRUCTIONS = """\
You rewrite a candidate's resume summary to align with a specific job description.

Rules:
- 1-2 sentences, plain text, no markdown.
- Use ONLY facts stated in the base resume. Do NOT invent skills, tenure, or metrics.
- Prefer phrasing that echoes the job description's own keywords when truthful.
- Return ONLY the rewritten summary. No preamble, no quotes, no explanation."""


class TailoredResumeBuilder:
    """Accumulates tailoring steps; produces TailoredResume on build().

    Chainable methods return Self so callers write:
        TailoredResumeBuilder(base, job)
          .with_summary("new text", "aligned focus")
          .with_reordered_experience(experience, "moved most relevant first")
          .build()
    """

    def __init__(self, base: BaseResume, job: Job) -> None:
        self._base = base
        self._job = job
        self._summary_override: str | None = None
        self._experience_override: list[ExperienceEntry] | None = None
        self._changes: list[str] = []

    def with_summary(self, text: str, reason: str) -> Self:
        text = text.strip()
        if not text:
            return self
        self._summary_override = text
        self._changes.append(f"summary rewritten: {reason}")
        return self

    def with_reordered_experience(self, experience: list[ExperienceEntry], reason: str) -> Self:
        self._experience_override = list(experience)
        self._changes.append(f"experience reordered: {reason}")
        return self

    def build(self) -> TailoredResume:
        return TailoredResume(
            base_name=self._base.name,
            job_id=self._job.id,
            name=self._base.name,
            email=self._base.email,
            phone=self._base.phone,
            location=self._base.location,
            links=dict(self._base.links),
            summary=self._summary_override or self._base.summary,
            experience=self._experience_override or list(self._base.experience),
            education=list(self._base.education),
            skills=list(self._base.skills),
            changes=list(self._changes),
        )


class Tailorer:
    """Uses an LLMClient to drive a TailoredResumeBuilder."""

    def __init__(self, llm: LLMClient, base: BaseResume) -> None:
        self._llm = llm
        self._base = base
        # Serialize base resume once — this is the cacheable payload reused
        # across every job in a discovery run.
        self._base_text = _serialize_resume(base)

    def tailor_for(self, job: Job) -> TailoredResume:
        builder = TailoredResumeBuilder(self._base, job)
        summary = self._rewrite_summary(job)
        if summary and summary != (self._base.summary or ""):
            builder.with_summary(summary, "aligned to JD focus")
        return builder.build()

    def _rewrite_summary(self, job: Job) -> str:
        system = [
            SystemBlock(text=_SUMMARY_INSTRUCTIONS, cacheable=False),
            SystemBlock(text=f"BASE RESUME:\n{self._base_text}", cacheable=True),
        ]
        user = (
            f"JOB TITLE: {job.title}\n"
            f"COMPANY: {job.company}\n"
            f"\nDESCRIPTION:\n{job.description}\n"
            f"\nRewrite the summary."
        )
        result = self._llm.complete(
            system=system,
            messages=[LLMMessage(role="user", content=user)],
            max_tokens=300,
            temperature=0.3,
        )
        return result.text.strip()


def _serialize_resume(base: BaseResume) -> str:
    """Plain-text serialization for LLM prompts (deterministic byte order → good cache)."""
    lines = [f"Name: {base.name}"]
    if base.summary:
        lines.append(f"\nSummary:\n{base.summary}")
    if base.skills:
        lines.append(f"\nSkills: {', '.join(base.skills)}")
    if base.experience:
        lines.append("\nExperience:")
        for e in base.experience:
            end = e.end or "present"
            lines.append(f"- {e.title} at {e.company} ({e.start} to {end})")
            for b in e.bullets:
                lines.append(f"  * {b}")
    if base.education:
        lines.append("\nEducation:")
        for ed in base.education:
            deg = f", {ed.degree}" if ed.degree else ""
            lines.append(f"- {ed.school}{deg}")
    return "\n".join(lines)
