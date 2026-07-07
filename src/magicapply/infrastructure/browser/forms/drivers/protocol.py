"""FormFieldDriver Protocol — resolution + Playwright execution per field."""

from __future__ import annotations

from typing import Protocol

from magicapply.domain.models.job import Job
from magicapply.infrastructure.browser.ats.answer_router import ResolvedAnswer
from magicapply.infrastructure.browser.ats.base import PageDriver
from magicapply.infrastructure.browser.forms.fields import FormField


class FormFieldDriver(Protocol):
    def resolve(self, field: FormField, job: Job) -> ResolvedAnswer: ...

    def execute(
        self, page: PageDriver, field: FormField, answer: ResolvedAnswer
    ) -> bool: ...

    def supports(self, field: FormField) -> bool: ...