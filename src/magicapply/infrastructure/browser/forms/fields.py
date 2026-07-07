"""Composable form field types — extends the Phase L scanner record."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FieldKind = Literal["text", "textarea", "select", "checkbox", "radio", "file"]

FieldVariant = Literal[
    "text",
    "textarea",
    "select",
    "checkbox",
    "radio",
    "file",
    "workday_listbox",
    "workday_multiselect",
    "workday_date_spin",
]

_KIND_TO_VARIANT: dict[FieldKind, FieldVariant] = {
    "text": "text",
    "textarea": "textarea",
    "select": "select",
    "checkbox": "checkbox",
    "radio": "radio",
    "file": "file",
}


def variant_from_kind(kind: FieldKind) -> FieldVariant:
    """Map a scanned ``FieldKind`` to its default fill variant."""
    return _KIND_TO_VARIANT[kind]


@dataclass
class FormField:
    """One scannable or recipe-defined form input.

    ``selector`` is a CSS selector that uniquely (best-effort) targets the
    field on the page. Handlers pass it back to Playwright fill/check/etc.
    """

    selector: str
    label: str
    kind: FieldKind
    name: str | None = None
    options: list[str] = field(default_factory=list)
    required: bool = False
    variant: FieldVariant | None = None
    step_id: str | None = None
    driver_hint: str | None = None
    widget_id: str | None = None
    group_id: str | None = None
    recipe_labels: tuple[str, ...] = ()
    recipe_value: str | None = None

    def __post_init__(self) -> None:
        if self.variant is None:
            self.variant = variant_from_kind(self.kind)