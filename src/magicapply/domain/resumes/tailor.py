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

import json
import logging
from typing import Self

from magicapply.config.models import KeywordEntry
from magicapply.domain.llm import LLMClient, LLMMessage, SystemBlock
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import (
    BaseResume,
    ExperienceEntry,
    TailoredResume,
)

logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        llm: LLMClient,
        base: BaseResume,
        *,
        summary_prompt: str,
        bullet_prompt: str = "",
    ) -> None:
        self._llm = llm
        self._base = base
        self._prompt = summary_prompt
        self._bullet_prompt = bullet_prompt
        # Serialize base resume once — this is the cacheable payload reused
        # across every job in a discovery run.
        self._base_text = _serialize_resume(base)

    def tailor_for(
        self,
        job: Job,
        matched_bank: list[KeywordEntry] | None = None,
    ) -> TailoredResume:
        builder = TailoredResumeBuilder(self._base, job)
        summary = self._rewrite_summary(job)
        if summary and summary != (self._base.summary or ""):
            builder.with_summary(summary, "aligned to JD focus")

        # Evidence-based bullet injection. Skipped when the prompt is not
        # configured, when there are no matched bank entries, or when the
        # base resume has no experience — any of those means we would
        # spend LLM tokens for nothing.
        if matched_bank and self._bullet_prompt and self._base.experience:
            rewritten = self._rewrite_bullets(job, matched_bank)
            if rewritten != list(self._base.experience):
                builder.with_reordered_experience(
                    rewritten,
                    f"bullets updated with {len(matched_bank)} bank matches",
                )
        return builder.build()

    def _rewrite_bullets(
        self,
        job: Job,
        matched_bank: list[KeywordEntry],
    ) -> list[ExperienceEntry]:
        """Rewrite each experience entry's bullets, injecting bank evidence
        where it naturally fits. Strict: any LLM output that shortens the
        list or fails to parse falls back to the original bullets for that
        entry — we would rather ship the truthful original than a truncated
        rewrite.
        """
        evidence_lines = "\n".join(
            f"- {e.term}: {e.evidence}" for e in matched_bank
        )
        out: list[ExperienceEntry] = []
        for entry in self._base.experience:
            if not entry.bullets:
                out.append(entry)
                continue
            new_bullets = self._rewrite_one_entry(job, entry, evidence_lines)
            if new_bullets is None or len(new_bullets) != len(entry.bullets):
                out.append(entry)
                continue
            out.append(entry.model_copy(update={"bullets": new_bullets}))
        return out

    def _rewrite_one_entry(
        self,
        job: Job,
        entry: ExperienceEntry,
        evidence_lines: str,
    ) -> list[str] | None:
        system = [
            SystemBlock(text=self._bullet_prompt, cacheable=False),
            SystemBlock(text=f"BASE RESUME:\n{self._base_text}", cacheable=True),
        ]
        user = (
            f"JOB TITLE: {job.title}\n"
            f"COMPANY: {job.company}\n"
            f"\nDESCRIPTION:\n{job.description}\n"
            f"\nKEYWORD BANK MATCHES:\n{evidence_lines}\n"
            f"\nROLE: {entry.title} at {entry.company}\n"
            f"BULLETS (JSON array, rewrite each in the same order):\n"
            f"{json.dumps(entry.bullets)}\n"
        )
        result = self._llm.complete(
            system=system,
            messages=[LLMMessage(role="user", content=user)],
            max_tokens=1200,
            temperature=0.3,
        )
        return _parse_bullet_list(result.text)

    def _rewrite_summary(self, job: Job) -> str:
        system = [
            SystemBlock(text=self._prompt, cacheable=False),
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


def _parse_bullet_list(raw: str) -> list[str] | None:
    """Parse a JSON array of strings; be lenient about fences. Returns None
    on any failure so the caller can fall back to the originals cleanly.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
        text = text.rstrip("`").strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("bullet rewrite: JSON parse failed: %r", raw[:200])
        return None
    if not isinstance(data, list):
        return None
    result: list[str] = []
    for item in data:
        if not isinstance(item, str):
            return None
        stripped = item.strip()
        if not stripped:
            return None
        result.append(stripped)
    return result


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
