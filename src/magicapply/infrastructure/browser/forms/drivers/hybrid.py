"""HybridDriver — rules first, LLM fallback for open-ended fields."""

from __future__ import annotations

from magicapply.domain.models.job import Job
from magicapply.infrastructure.browser.ats.answer_router import ResolvedAnswer
from magicapply.infrastructure.browser.ats.base import PageDriver
from magicapply.infrastructure.browser.forms.drivers.llm import LLMDriver
from magicapply.infrastructure.browser.forms.drivers.rules import RulesBasedDriver
from magicapply.infrastructure.browser.forms.fields import FormField


class HybridDriver:
    def __init__(self, rules: RulesBasedDriver, llm: LLMDriver) -> None:
        self._rules = rules
        self._llm = llm

    def supports(self, field: FormField) -> bool:
        return True

    def resolve(self, field: FormField, job: Job) -> ResolvedAnswer:
        resolved = self._rules.resolve(field, job)
        if resolved.strategy != "unhandled":
            return resolved
        if self._llm.supports(field):
            llm_resolved = self._llm.resolve(field, job)
            if llm_resolved.strategy != "unhandled":
                return llm_resolved
        return resolved

    def execute(
        self, page: PageDriver, field: FormField, answer: ResolvedAnswer
    ) -> bool:
        if answer.strategy == "narrative" and self._llm.supports(field):
            if self._llm.execute(page, field, answer):
                return True
        return self._rules.execute(page, field, answer)