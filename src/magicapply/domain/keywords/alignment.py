"""ATS-style keyword alignment between a resume text blob and a job description.

Uses the operator KeywordBank as the skill vocabulary. For each bank entry
whose term (or a synonym) appears in the JD, we require the **JD form**
(the longest matching phrase) to also appear on the resume. That makes
pre- vs post-tailor scores meaningful: synonym→term DOCX swaps raise the
score when the resume previously only had a synonym.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from magicapply.config.models import KeywordBank, KeywordEntry
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import BaseResume, TailoredResume


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    """Structured keyword-alignment outcome (score 0–100 + diagnostic lists)."""

    value: int
    rationale: str
    jd_terms: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def phrase_in_text(phrase: str, haystack: str) -> bool:
    """Case-insensitive whole-phrase match with word boundaries.

    Multi-word phrases use ``\\b`` on each edge so ``sql`` does not match
    inside ``postgresql``, while ``distributed systems`` matches that pair.
    """
    phrase = phrase.strip()
    if not phrase:
        return False
    pattern = re.compile(
        r"\b" + re.escape(phrase.strip()) + r"\b",
        re.IGNORECASE,
    )
    return pattern.search(haystack) is not None


def jd_form_for_entry(entry: KeywordEntry, jd_text: str) -> str | None:
    """Return the longest term/synonym form present in the JD, or None."""
    forms = [entry.term, *entry.synonyms]
    present = [f.strip() for f in forms if f and phrase_in_text(f, jd_text)]
    if not present:
        return None
    # Prefer longer phrases so "deep reinforcement learning" wins over "rl".
    return max(present, key=lambda s: (len(s), s.lower()))


def score_keyword_alignment(
    resume_text: str,
    job: Job,
    bank: KeywordBank,
) -> AlignmentResult:
    """Compute ATS-style keyword coverage of JD bank terms on the resume."""
    jd_text = f"{job.title}\n{job.description}"
    if not bank.keywords:
        return AlignmentResult(
            value=0,
            rationale="keyword alignment: empty keyword bank",
        )

    jd_terms: list[str] = []
    for entry in bank.keywords:
        form = jd_form_for_entry(entry, jd_text)
        if form is not None:
            # Deduplicate by lowercase form so overlapping bank rows don't
            # inflate the denominator.
            if form.lower() not in {t.lower() for t in jd_terms}:
                jd_terms.append(form)

    if not jd_terms:
        return AlignmentResult(
            value=0,
            rationale="keyword alignment: no bank keywords found in JD",
        )

    matched: list[str] = []
    missing: list[str] = []
    for term in jd_terms:
        if phrase_in_text(term, resume_text):
            matched.append(term)
        else:
            missing.append(term)

    value = int(round(100 * len(matched) / len(jd_terms)))
    value = max(0, min(100, value))

    matched_s = ", ".join(matched) if matched else "—"
    missing_s = ", ".join(missing) if missing else "—"
    rationale = (
        f"keyword alignment: {len(matched)}/{len(jd_terms)} JD terms in resume "
        f"(matched: {matched_s}; missing: {missing_s})"
    )
    return AlignmentResult(
        value=value,
        rationale=rationale,
        jd_terms=jd_terms,
        matched=matched,
        missing=missing,
    )


def serialize_resume_text(resume: BaseResume | TailoredResume) -> str:
    """Plain-text blob used for keyword matching (deterministic order)."""
    lines = [f"Name: {resume.name}"]
    if resume.summary:
        lines.append(f"\nSummary:\n{resume.summary}")
    if resume.skills:
        lines.append(f"\nSkills: {', '.join(resume.skills)}")
    if resume.experience:
        lines.append("\nExperience:")
        for e in resume.experience:
            end = e.end or "present"
            lines.append(f"- {e.title} at {e.company} ({e.start} to {end})")
            for b in e.bullets:
                lines.append(f"  * {b}")
    if resume.education:
        lines.append("\nEducation:")
        for ed in resume.education:
            deg = f", {ed.degree}" if ed.degree else ""
            lines.append(f"- {ed.school}{deg}")
    return "\n".join(lines)


def docx_plain_text(path: Path) -> str:
    """Extract body + table cell text from a DOCX for post-tailor scoring."""
    from docx import Document

    doc = Document(str(path))
    parts: list[str] = []
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if text:
            parts.append(text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    text = paragraph.text.strip()
                    if text:
                        parts.append(text)
    return "\n".join(parts)
