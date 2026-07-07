"""FormSchema — ordered, fillable description of one form step."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from magicapply.infrastructure.browser.forms.fields import FormField

SchemaSource = Literal["scanned", "recipe", "hybrid"]


@dataclass
class FormSchema:
    schema_id: str
    ats: str
    fields: list[FormField]
    form_selector: str
    source: SchemaSource
    step_id: str | None = None


@dataclass
class FillReport:
    unhandled: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)