"""Merge BaseResume fields into StaticAnswers for form fill.

Phone and current employer often live on the resume YAML; static_answers in
base_config may omit them. Overlay fills empty static slots only — explicit
static_answers always win (config over code for operator overrides).
"""

from __future__ import annotations

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.resume import BaseResume, ExperienceEntry

_CURRENT_END_MARKERS = frozenset({"", "present", "current", "now", "ongoing"})


def current_employer_from_resume(resume: BaseResume) -> str | None:
    """Best-effort current employer from structured experience entries.

    Prefer a role whose ``end`` is null/empty/present; otherwise the first
    experience company (resume order is usually most-recent-first).
    """
    if not resume.experience:
        return None
    current = _first_current_role(resume.experience)
    if current is not None and current.company.strip():
        return current.company.strip()
    first = resume.experience[0]
    company = (first.company or "").strip()
    return company or None


def _first_current_role(entries: list[ExperienceEntry]) -> ExperienceEntry | None:
    for entry in entries:
        end = (entry.end or "").strip().lower()
        if end in _CURRENT_END_MARKERS or entry.end is None:
            return entry
    return None


def overlay_static_from_resume(
    static: StaticAnswers,
    resume: BaseResume,
) -> StaticAnswers:
    """Return static answers with phone / current_employer filled from resume."""
    updates: dict[str, object] = {}
    if not (static.phone or "").strip() and (resume.phone or "").strip():
        updates["phone"] = resume.phone.strip()  # type: ignore[union-attr]
    if not (static.current_employer or "").strip():
        employer = current_employer_from_resume(resume)
        if employer:
            updates["current_employer"] = employer
    if not updates:
        return static
    return static.model_copy(update=updates)
