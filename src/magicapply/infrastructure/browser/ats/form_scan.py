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

import re
from datetime import date

from lxml import html as lhtml

from magicapply.infrastructure.browser.ats.base import PageDriver
from magicapply.infrastructure.browser.forms.fields import (
    FieldKind,
    FormField,
    variant_from_kind,
)

# Re-export for backward compatibility (answer_router, tests).
__all__ = ["FieldKind", "FormField", "scan_form", "xpath_string_literal"]


def xpath_string_literal(value: str) -> str:
    """Quote ``value`` for safe embedding in an XPath 1.0 string literal.

    lxml/libxml2 raises ``XPathEvalError: Invalid expression`` when a single
    quote appears inside a ``'…'``-quoted predicate. Prefer single quotes when
    the value has none; double quotes when it has singles but no doubles;
    otherwise use ``concat(...)`` (XPath 1.0 has no backslash escapes).
    """
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    # Both quote types present: concat('a', "'", 'b', "'", 'c')
    parts: list[str] = []
    for i, chunk in enumerate(value.split("'")):
        if i:
            parts.append('"\'"')
        if chunk:
            parts.append(f"'{chunk}'")
    if not parts:
        return "''"
    if len(parts) == 1:
        return parts[0]
    return "concat(" + ", ".join(parts) + ")"


def scan_form(page: PageDriver, form_selector: str = "form") -> list[FormField]:
    """Return one FormField per scannable input inside the chosen form.

    ``form_selector`` is a CSS-ish selector used only to trim the DOM to the
    right form when a page has multiple. Under the hood the scanner uses
    lxml + XPath, so ``form_selector`` is normalised to XPath.

    Unknown or untranslatable selectors return ``[]`` (never raise) so a
    single bad recipe entry cannot fail the whole apply.
    """
    try:
        xpath = _xpath_from_css(form_selector)
    except ValueError:
        return []
    tree = lhtml.fromstring(page.content())
    try:
        form_els = tree.xpath(xpath)
    except Exception:  # noqa: BLE001 — lxml XPathEvalError etc.
        return []
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
                options = _radio_options(root, name)
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

    return [f for f in fields if not _is_scan_noise(f)]


def _is_scan_noise(field: FormField) -> bool:
    """Drop phantom inputs from custom widgets (country picker, DEI, etc.)."""
    if field.selector in {"input", "textarea", "select"}:
        return True
    if field.kind == "radio" and not field.selector.strip():
        return True
    label = field.label.strip().lower()
    if label == "search" or "search-input" in field.selector:
        return True
    return False


def _build_field(
    el,
    root,
    *,
    kind: FieldKind,
    options: list[str] | None = None,
) -> FormField:
    field = FormField(
        selector=_selector_for(el),
        label=_label_for(el, root, kind=kind),
        kind=kind,
        name=el.get("name"),
        options=options or [],
        required=_is_required(el),
    )
    return _enrich_workday_variant(field, el)


_SIGNATURE_DATE_PREFIX = "selfIdentifiedDisabilityData--dateSignedOn-dateSection"


def _enrich_workday_variant(field: FormField, el) -> FormField:
    """Assign Workday-specific variants the generic DOM scanner cannot infer."""
    el_id = el.get("id") or ""
    if el_id.endswith("--school") or el_id.endswith("--schoolName"):
        field.step_id = "my_experience"
        return field
    if "education" in el_id and el_id.endswith("dateSectionYear-input"):
        field.variant = "workday_date_spin"
        field.step_id = "my_experience"
        return field
    if not el_id.startswith(_SIGNATURE_DATE_PREFIX) or not el_id.endswith("-input"):
        return field
    part = el_id.removeprefix(_SIGNATURE_DATE_PREFIX).removesuffix("-input")
    if part not in {"Month", "Day", "Year"}:
        return field
    today = date.today()
    values = {
        "Month": str(today.month),
        "Day": str(today.day),
        "Year": str(today.year),
    }
    field.variant = "workday_date_spin"
    field.recipe_value = values[part]
    field.group_id = "selfIdentifiedDisabilityData--dateSignedOn"
    field.step_id = "self_identify"
    return field


def _selector_for(el) -> str:
    """Prefer #id when available; else [name='…']; else the tag itself.

    IDs/names with CSS-special characters use attribute selectors so
    Playwright can resolve them (``#foo'bar`` is not a valid CSS id).
    """
    el_id = el.get("id")
    if el_id:
        if re.search(r"[^A-Za-z0-9_-]", el_id):
            escaped = el_id.replace("\\", "\\\\").replace('"', '\\"')
            return f'[id="{escaped}"]'
        return f"#{el_id}"
    name = el.get("name")
    if name:
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        return f"{el.tag}[name='{escaped}']"
    return el.tag


def _label_for(el, root, *, kind: FieldKind | None = None) -> str:
    """Best-effort label extraction: <label for>, wrapping <label>, aria-label, name."""
    lever_label = _lever_custom_question_label(el)
    if lever_label:
        return lever_label

    if kind == "radio":
        group_label = _radio_group_label(el, root)
        if group_label:
            return group_label

    ashby_label = _ashby_question_label(el)
    if ashby_label:
        return ashby_label

    name = el.get("name") or ""
    if name.startswith("eeo[") and name.endswith("]"):
        return name[4:-1].replace("_", " ").capitalize()

    el_id = el.get("id")
    if el_id:
        matches = root.xpath(f"//label[@for={xpath_string_literal(el_id)}]")
        if matches:
            # Strip required markers ("Phone*") so AnswerRouter identity
            # regexes like ``^phone$`` match.
            return _normalize_label(matches[0].text_content())

    parent = el.getparent()
    while parent is not None:
        if parent.tag == "label":
            return _normalize_label(parent.text_content())
        parent = parent.getparent()

    aria = el.get("aria-label") or el.get("aria-labelledby")
    if aria:
        return _normalize_label(aria)

    name = el.get("name")
    return name.replace("_", " ") if name else ""


def _lever_custom_question_label(el) -> str:
    """Lever ``li.custom-question`` wraps screening radios outside ``<fieldset>``."""
    parent = el.getparent()
    while parent is not None:
        classes = parent.get("class") or ""
        if "custom-question" in classes:
            return _lever_question_text(parent)
        parent = parent.getparent()
    return ""


def _lever_question_text(li) -> str:
    """Question prompt from a Lever custom-question block (exclude Yes/No options)."""
    option_texts: set[str] = set()
    for lab in li.xpath(".//label[input[@type='radio']]"):
        option_texts.add(_clean_text(lab.text_content()))
    chunks: list[str] = []
    for text in li.xpath(".//text()"):
        t = text.strip()
        if not t or t in option_texts or t in {"Yes", "No", "✱", "*"}:
            continue
        if t in chunks:
            continue
        chunks.append(t)
    for c in chunks:
        if "?" in c or len(c) > 40:
            return _normalize_label(c)
    return _normalize_label(chunks[0]) if chunks else ""


def _radio_group_label(el, root) -> str:
    """Prefer fieldset legend / aria-labelledby over the Yes/No option label."""
    lever_label = _lever_custom_question_label(el)
    if lever_label:
        return lever_label

    parent = el.getparent()
    while parent is not None:
        if parent.tag == "fieldset":
            legends = parent.xpath("./legend")
            if legends:
                return _normalize_label(legends[0].text_content())
            prompt = _prompt_text_from_container(parent)
            if prompt:
                return prompt
        parent = parent.getparent()

    parent = el.getparent()
    while parent is not None:
        labelledby = parent.get("aria-labelledby")
        if labelledby:
            for ref in labelledby.split():
                nodes = root.xpath(f"//*[@id={xpath_string_literal(ref)}]")
                if nodes:
                    text = _normalize_label(nodes[0].text_content())
                    if text.lower() not in _GENERIC_ARIA_LABELS:
                        return text
        parent = parent.getparent()
    return ""


_GENERIC_ARIA_LABELS = frozenset({"yes", "no", "application", "overview"})


def _ashby_question_label(el) -> str:
    """Ashby custom questions use UUID field names; prompt lives in a parent block."""
    parent = el.getparent()
    while parent is not None:
        if parent.tag == "fieldset":
            prompt = _prompt_text_from_container(parent)
            if prompt:
                return prompt
        classes = parent.get("class") or ""
        if "_fieldEntry_" in classes:
            prompt = _prompt_text_from_container(parent)
            if prompt:
                return prompt
        parent = parent.getparent()
    return ""


def _prompt_text_from_container(container) -> str:
    """Extract a screening prompt ending in ``?`` from Ashby fieldset/div blocks."""
    text = _clean_text(container.text_content())
    match = re.search(r"(.+?\?)", text)
    if not match:
        return ""
    return _normalize_label(match.group(1))


def _radio_options(root, name: str) -> list[str]:
    options: list[str] = []
    name_lit = xpath_string_literal(name)
    for opt in root.xpath(f".//input[@type='radio'][@name={name_lit}]"):
        value = (opt.get("value") or "").strip()
        if not value:
            value = _radio_option_label(opt, root)
        options.append(value)
    return options


def _radio_option_label(opt, root) -> str:
    el_id = opt.get("id")
    if el_id:
        labels = root.xpath(f".//label[@for={xpath_string_literal(el_id)}]")
        if labels:
            return _normalize_label(labels[0].text_content())
    parent = opt.getparent()
    if parent is not None and parent.tag == "label":
        return _normalize_label(parent.text_content())
    return ""


def _is_required(el) -> bool:
    if el.get("required") is not None or el.get("aria-required") == "true":
        return True
    parent = el.getparent()
    while parent is not None:
        if parent.get("aria-required") == "true":
            return True
        parent = parent.getparent()
    return False


def _normalize_label(text: str) -> str:
    return _clean_text(text).replace("*", "").strip()


def _clean_text(text: str) -> str:
    return " ".join(text.split())


def _xpath_from_css(selector: str) -> str:
    """Translate a small CSS subset used by recipes into XPath 1.0.

    Supported shapes (as used in ``configs/ats_recipes/*.yaml``):

    - ``form`` / bare tag
    - ``form#id`` / ``#id``
    - ``form.class``
    - ``[attr='value']`` exact match
    - ``tag[attr='value']``
    - ``tag[attr*='value']`` / ``^=`` / ``$=`` substring matches
      (e.g. ``form[action*='apply']``)
    - ``[data-automation-id='…']`` / ``[data-testid='…']``
    """
    sel = selector.strip()
    if not sel:
        return "//*"

    # Bare [attr op value] (no tag)
    bare = re.fullmatch(
        r"\[([\w-]+)(\*=|\^=|\$=|=)['\"]([^'\"]+)['\"]\]",
        sel,
    )
    if bare:
        attr, op, value = bare.groups()
        return f"//*[{_attr_predicate(attr, op, value)}]"

    # tag[attr op value]
    tagged = re.fullmatch(
        r"([A-Za-z][\w-]*)\[([\w-]+)(\*=|\^=|\$=|=)['\"]([^'\"]+)['\"]\]",
        sel,
    )
    if tagged:
        tag, attr, op, value = tagged.groups()
        return f"//{tag}[{_attr_predicate(attr, op, value)}]"

    if "data-automation-id=" in sel:
        match = re.search(r"""data-automation-id=['"]([^'"]+)['"]""", sel)
        if match:
            return (
                f"//*[@data-automation-id={xpath_string_literal(match.group(1))}]"
            )
    if "#" in sel:
        tag, _, ident = sel.partition("#")
        return f"//{tag or '*'}[@id={xpath_string_literal(ident)}]"
    if "." in sel and not sel.startswith("["):
        tag, _, class_name = sel.partition(".")
        return (
            f"//{tag or '*'}[contains(concat(' ', normalize-space(@class), ' '), "
            f"' {class_name} ')]"
        )
    # Bare tag name only — reject anything that still looks like CSS sugar
    # so we never emit invalid XPath (which crashes the whole apply).
    if re.fullmatch(r"[A-Za-z][\w-]*", sel):
        return f"//{sel}"
    raise ValueError(f"unsupported form_selector CSS for XPath: {selector!r}")


def _attr_predicate(attr: str, op: str, value: str) -> str:
    lit = xpath_string_literal(value)
    if op == "=":
        return f"@{attr}={lit}"
    if op == "*=":
        return f"contains(@{attr}, {lit})"
    if op == "^=":
        return f"starts-with(@{attr}, {lit})"
    if op == "$=":
        # XPath 1.0 has no ends-with; emulate with substring.
        return (
            f"substring(@{attr}, string-length(@{attr}) - string-length({lit}) + 1)"
            f" = {lit}"
        )
    raise ValueError(f"unsupported attribute operator: {op!r}")
