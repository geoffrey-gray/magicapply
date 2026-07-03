"""Parse a live ATS form into a structured list of FormField.

The scanner works off ``page.content()`` (already on the PageDriver Protocol)
so it does not force any new browser abstraction. It walks every ``input``,
``select``, and ``textarea`` inside a chosen form container, extracts each
one's label (``<label for>`` link, wrapping ``<label>``, ``aria-label``, or
``name`` as a last resort), and classifies the field by tag + input type.

Anything ``type=hidden`` or ``type=submit`` is skipped.

The output feeds the ``AnswerRouter`` (Phase L.2), which decides how each
field should be filled: static answers, LLM-generated narrative, resume
upload, or "unhandled — log and continue".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from lxml import html as lhtml

from magicapply.infrastructure.browser.ats.base import PageDriver

FieldKind = Literal["text", "textarea", "select", "checkbox", "radio", "file"]


@dataclass
class FormField:
    """One scanned form input.

    ``selector`` is a CSS selector that uniquely (best-effort) targets the
    field on the page — either an ``#id`` or an ``[name=…]`` attribute
    selector. Handlers pass it back to ``page.fill`` / ``page.check`` etc.
    """

    selector: str
    label: str
    kind: FieldKind
    name: str | None = None
    options: list[str] = field(default_factory=list)  # select / radio group
    required: bool = False


def scan_form(page: PageDriver, form_selector: str = "form") -> list[FormField]:
    """Return one FormField per scannable input inside the chosen form.

    ``form_selector`` is a CSS-ish selector used only to trim the DOM to the
    right form when a page has multiple. Under the hood the scanner uses
    lxml + XPath, so ``form_selector`` is normalised to XPath.
    """
    tree = lhtml.fromstring(page.content())
    form_els = tree.xpath(_xpath_from_css(form_selector))
    if not form_els:
        return []
    root = form_els[0]

    fields: list[FormField] = []
    seen_radio_groups: set[str] = set()

    for el in root.xpath(".//input | .//select | .//textarea"):
        tag = el.tag
        if tag == "input":
            input_type = (el.get("type") or "text").lower()
            if input_type in {"hidden", "submit", "button", "reset", "image"}:
                continue
            if input_type == "file":
                fields.append(_build_field(el, root, kind="file"))
            elif input_type == "checkbox":
                fields.append(_build_field(el, root, kind="checkbox"))
            elif input_type == "radio":
                # Collapse a radio group (same name) into one field.
                name = el.get("name") or ""
                if name in seen_radio_groups:
                    continue
                seen_radio_groups.add(name)
                options = [
                    (opt.get("value") or "").strip()
                    for opt in root.xpath(f".//input[@type='radio'][@name='{name}']")
                ]
                fields.append(
                    _build_field(el, root, kind="radio", options=options)
                )
            else:
                # text, email, tel, url, number, password, search, ...
                fields.append(_build_field(el, root, kind="text"))
        elif tag == "textarea":
            fields.append(_build_field(el, root, kind="textarea"))
        elif tag == "select":
            options = []
            for opt in el.xpath(".//option"):
                value = opt.get("value")
                if value is None:
                    value = (opt.text or "").strip()
                options.append(value)
            fields.append(
                _build_field(el, root, kind="select", options=options)
            )

    return fields


def _build_field(
    el,
    root,
    *,
    kind: FieldKind,
    options: list[str] | None = None,
) -> FormField:
    return FormField(
        selector=_selector_for(el),
        label=_label_for(el, root),
        kind=kind,
        name=el.get("name"),
        options=options or [],
        required=bool(el.get("required") is not None or el.get("aria-required") == "true"),
    )


def _selector_for(el) -> str:
    """Prefer #id when available; else [name='…']; else the tag itself."""
    el_id = el.get("id")
    if el_id:
        return f"#{el_id}"
    name = el.get("name")
    if name:
        return f"{el.tag}[name='{name}']"
    return el.tag


def _label_for(el, root) -> str:
    """Best-effort label extraction: <label for>, wrapping <label>, aria-label, name."""
    el_id = el.get("id")
    if el_id:
        matches = root.xpath(f"//label[@for='{el_id}']")
        if matches:
            return _clean_text(matches[0].text_content())

    parent = el.getparent()
    while parent is not None:
        if parent.tag == "label":
            return _clean_text(parent.text_content())
        parent = parent.getparent()

    aria = el.get("aria-label") or el.get("aria-labelledby")
    if aria:
        return aria

    name = el.get("name")
    return name.replace("_", " ") if name else ""


def _clean_text(text: str) -> str:
    return " ".join(text.split())


def _xpath_from_css(selector: str) -> str:
    """Handle just the two shapes we actually need: ``form`` and ``form#id``."""
    if "#" in selector:
        tag, _, ident = selector.partition("#")
        return f"//{tag or '*'}[@id='{ident}']"
    return f"//{selector}"
