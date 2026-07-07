"""Resume-backed field resolution for composable fill (Workday education/experience)."""

from __future__ import annotations

import re

from magicapply.infrastructure.browser.ats.answer_router import ResolvedAnswer
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.forms.fields import FormField


def resolve_from_resume(field: FormField, data: ApplicationData) -> ResolvedAnswer | None:
    """Map scanned Workday fields to ``TailoredResume`` values when the router misses."""
    resume = data.tailored_resume
    label = field.label.strip().lower()
    sel = field.selector.lower()

    if _is_school_field(label, sel):
        if resume.education and resume.education[0].school:
            return ResolvedAnswer("static", resume.education[0].school)
        return None

    if "field of study" in label and resume.education and resume.education[0].field:
        return ResolvedAnswer("static", resume.education[0].field)

    if field.kind == "checkbox" and "currently work" in label:
        if resume.experience:
            end = (resume.experience[0].end or "").strip().lower()
            is_current = end in {"", "present", "current"}
            return ResolvedAnswer("check", check=is_current)
        return ResolvedAnswer("check", check=False)

    if "year" in label and "education" in sel and "datesectionyear" in sel.replace("-", ""):
        year = _education_year(resume, sel)
        if year:
            return ResolvedAnswer("static", year)
        return None

    if "primaryquestionnaire" in sel and field.kind == "textarea":
        return None

    return None


def _is_school_field(label: str, selector: str) -> bool:
    if "school" in label or "university" in label:
        return True
    return bool(re.search(r"--school(name)?$", selector))


def _education_year(resume, selector: str) -> str | None:
    if not resume.education:
        return None
    graduated = resume.education[0].graduated
    if not graduated:
        return None
    year = graduated[:4] if len(graduated) >= 4 else graduated
    if "firstyear" in selector:
        return year
    if "lastyear" in selector:
        return year
    return year