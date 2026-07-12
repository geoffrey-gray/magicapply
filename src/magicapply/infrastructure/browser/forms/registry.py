"""DriverRegistry — selects a FormFieldDriver per field from config."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from magicapply.config.models import FormDriversConfig, FormDriverName
from magicapply.domain.resumes.narrative import NarrativeEngine
from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.forms.drivers.hybrid import HybridDriver
from magicapply.infrastructure.browser.forms.drivers.llm import LLMDriver
from magicapply.infrastructure.browser.forms.drivers.protocol import FormFieldDriver
from magicapply.infrastructure.browser.forms.drivers.rules import RulesBasedDriver
from magicapply.infrastructure.browser.forms.fields import FieldVariant, FormField

if TYPE_CHECKING:
    from magicapply.config.models import ATSTimeoutsConfig


@dataclass
class DriverRegistry:
    drivers: dict[FormDriverName, FormFieldDriver]
    default: FormDriverName
    by_ats: dict[str, FormDriverName] = field(default_factory=dict)
    by_variant: dict[FieldVariant, FormDriverName] = field(default_factory=dict)
    by_label_regex: list[tuple[re.Pattern[str], FormDriverName]] = field(
        default_factory=list
    )

    def resolve(self, field: FormField, *, ats: str) -> FormFieldDriver:
        name = self._resolve_name(field, ats=ats)
        return self.drivers[name]

    def _resolve_name(self, field: FormField, *, ats: str) -> FormDriverName:
        hint = field.driver_hint
        if hint in self.drivers:
            return hint  # type: ignore[return-value]

        label = field.label
        for pattern, driver_name in self.by_label_regex:
            if pattern.search(label):
                return driver_name

        variant = field.variant
        if variant and variant in self.by_variant:
            return self.by_variant[variant]

        if ats in self.by_ats:
            return self.by_ats[ats]

        return self.default


def build_driver_registry(
    router: AnswerRouter,
    narrative: NarrativeEngine,
    config: FormDriversConfig | None = None,
    timeouts: ATSTimeoutsConfig | None = None,
) -> DriverRegistry:
    cfg = config or FormDriversConfig()
    rules = RulesBasedDriver(router, timeouts)
    llm = LLMDriver(narrative)
    hybrid = HybridDriver(rules, llm)
    drivers: dict[FormDriverName, FormFieldDriver] = {
        "rules": rules,
        "llm": llm,
        "hybrid": hybrid,
    }
    field_patterns = [
        (re.compile(entry.label_regex, re.IGNORECASE), entry.driver)
        for entry in cfg.fields
    ]
    by_variant: dict[FieldVariant, FormDriverName] = {
        k: v for k, v in cfg.variants.items()  # type: ignore[misc]
    }
    return DriverRegistry(
        drivers=drivers,
        default=cfg.default,
        by_ats=dict(cfg.ats),
        by_variant=by_variant,
        by_label_regex=field_patterns,
    )


def build_rules_registry(router: AnswerRouter, timeouts: ATSTimeoutsConfig | None = None) -> DriverRegistry:
    """Backward-compatible rules-only registry for tests without narrative."""
    rules = RulesBasedDriver(router, timeouts)
    return DriverRegistry(drivers={"rules": rules}, default="rules")