"""LLMDriver — narrative resolution for open-ended text fields."""

from __future__ import annotations

import contextlib

from magicapply.domain.models.job import Job
from magicapply.domain.resumes.narrative import NarrativeEngine
from magicapply.infrastructure.browser.ats.answer_router import ResolvedAnswer, _looks_open_ended
from magicapply.infrastructure.browser.ats.base import PageDriver
from magicapply.infrastructure.browser.forms.fields import FormField

_TEXT_VARIANTS = frozenset({"text", "textarea"})


class LLMDriver:
    def __init__(self, narrative: NarrativeEngine) -> None:
        self._narrative = narrative

    def supports(self, field: FormField) -> bool:
        variant = field.variant or field.kind
        return variant in _TEXT_VARIANTS

    def resolve(self, field: FormField, job: Job) -> ResolvedAnswer:
        if not self.supports(field) or not _looks_open_ended(field.label.lower()):
            return ResolvedAnswer("unhandled")
        answer = self._narrative.answer(job, field.label)
        return ResolvedAnswer("narrative", answer)

    def execute(
        self, page: PageDriver, field: FormField, answer: ResolvedAnswer
    ) -> bool:
        if answer.strategy != "narrative":
            return False
        with contextlib.suppress(Exception):
            page.fill(field.selector, answer.value)
            return True
        return False