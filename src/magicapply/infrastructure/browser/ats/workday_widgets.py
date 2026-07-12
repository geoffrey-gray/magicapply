"""Workday React widget helpers — community patterns on Playwright.

The raghuboosetty/workday Selenium script (widely forked, including
workday_auto) established the stable interaction model for Workday apply
forms:

- **Multiselect** (e.g. How Did You Hear About Us, country phone code):
  click ``div[data-automation-id='multiSelectContainer']`` scoped to the
  field, then click
  ``div[data-automation-id='promptOption'][data-automation-label='…']``
  with an *exact* ``data-automation-label`` match. Some tenants use a
  two-level cascade (e.g. Job Board → LinkedIn).

- **Listbox button** (e.g. State, Phone Device Type):
  click the ``button`` trigger (``#address--countryRegion``), then click
  the ``[role='option']`` whose ``aria-label`` is ``"{value} not checked"``
  or whose visible text equals the value.

These widgets render options in a document-level portal, so selectors must
not be scoped inside ``[data-fkit-id='…']`` when clicking ``promptOption``.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

from magicapply.infrastructure.browser.ats.base import PageDriver

if TYPE_CHECKING:
    from magicapply.config.models import ATSTimeoutsConfig

logger = logging.getLogger(__name__)

_PROMPT_OPTION = "div[data-automation-id='promptOption'][data-automation-label='{label}']"
_MULTI_CONTAINER = "[data-automation-id='multiSelectContainer']"


def fill_multiselect(
    page: PageDriver,
    fkit_id: str,
    label: str,
    *,
    parent_label: str | None = None,
    search: str | None = None,
    timeouts: ATSTimeoutsConfig | None = None,
) -> bool:
    """Select ``label`` in a Workday multiselect (raghuboosetty pattern)."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    del search  # community script opens via container click only
    if not label:
        return False
    scope = f"[data-fkit-id='{fkit_id}']"
    if not _open_multiselect(page, scope, fkit_id, t.wizard_click_timeout_ms):
        return False
    _wait_brief(page, t.page_load_wait_ms)
    if parent_label:
        if not click_prompt_option(page, parent_label, timeouts):
            logger.warning("Workday multiselect: parent option %r not found", parent_label)
            return False
        _wait_brief(page, t.page_load_wait_ms)
    if not click_prompt_option(page, label, timeouts):
        return False
    _wait_brief(page, t.step_transition_wait_ms)
    return _selection_committed(page, scope, label, t.wizard_click_timeout_ms)


def click_prompt_option(page: PageDriver, label: str, timeouts: ATSTimeoutsConfig | None = None) -> bool:
    """Click one ``promptOption`` by exact ``data-automation-label``."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    if not label:
        return False
    escaped = _css_attr(label)
    locator = getattr(page, "locator", None)
    if callable(locator):
        selectors = (
            _PROMPT_OPTION.format(label=escaped),
            f"[data-automation-id='menuItem'][aria-label='{escaped} not checked']",
            f"[role='option'][aria-label='{escaped} not checked']",
        )
        for sel in selectors:
            try:
                opt = locator(sel)
                if opt.count() == 0:
                    continue
                opt.first.click(timeout=t.wizard_click_timeout_ms)
                return True
            except Exception:  # noqa: BLE001
                continue
        try:
            locator.get_by_role("option", name=f"{label} not checked", exact=True).click(
                timeout=t.wizard_click_timeout_ms
            )
            return True
        except Exception:  # noqa: BLE001
            pass
    return _try_click(page, _PROMPT_OPTION.format(label=escaped), timeout_ms=t.wizard_click_timeout_ms)


def _open_multiselect(page: PageDriver, scope: str, fkit_id: str, timeout_ms: int = 8000) -> bool:
    """Open dropdown via scoped ``multiSelectContainer`` (raghuboosetty step 1)."""
    container = f"{scope} {_MULTI_CONTAINER}"
    if _is_visible(page, container):
        return _try_click(page, container, timeout_ms=timeout_ms)
    if _is_visible(page, f"#{fkit_id}"):
        return _try_click(page, f"#{fkit_id}", timeout_ms=timeout_ms)
    return False


def _selection_committed(page: PageDriver, scope: str, label: str, timeout_ms: int = 8000) -> bool:
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return True
    try:
        text = locator(scope).inner_text(timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        return False
    return "1 item selected" in text or label in text


def select_listbox_by_index(page: PageDriver, index: int, label: str, timeouts: ATSTimeoutsConfig | None = None) -> bool:
    """Open the *n*th non-Settings ``aria-haspopup=listbox`` button and pick ``label``."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    buttons = locator("button[aria-haspopup='listbox']")
    try:
        buttons.nth(index).click(timeout=t.wizard_click_timeout_ms)
    except Exception:  # noqa: BLE001
        return False
    _wait_brief(page, t.page_load_wait_ms)
    return _click_listbox_option(page, label, t.wizard_click_timeout_ms)


def fill_questionnaire_fieldsets(
    page: PageDriver,
    scope: str,
    rules: list[tuple[re.Pattern[str], bool | None]],
    *,
    default_false: bool = True,
    timeouts: ATSTimeoutsConfig | None = None,
) -> None:
    """Fill every ``Select One`` listbox under ``scope`` using fieldset label rules."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return
    fieldsets = locator(f"{scope} fieldset")
    try:
        count = fieldsets.count()
    except Exception:  # noqa: BLE001
        return
    for i in range(count):
        fs = fieldsets.nth(i)
        try:
            text = fs.inner_text(timeout=t.step_transition_wait_ms)
        except Exception:  # noqa: BLE001
            continue
        btn = fs.locator("button[aria-haspopup='listbox']")
        try:
            if btn.count() == 0:
                continue
            current = btn.first.inner_text(timeout=t.brief_wait_ms).strip().lower()
        except Exception:  # noqa: BLE001
            continue
        if current and current not in ("select one", ""):
            continue
        value: bool | None = None
        for pattern, rule_value in rules:
            if pattern.search(text):
                value = rule_value
                break
        if value is None and default_false:
            value = False
        if value is None:
            continue
        pick = "Yes" if value else "No"
        try:
            btn.first.click(timeout=t.wizard_click_timeout_ms)
        except Exception:  # noqa: BLE001
            continue
        _wait_brief(page, t.page_load_wait_ms)
        _click_listbox_option(page, pick, t.wizard_click_timeout_ms)


def select_question_listbox(page: PageDriver, question_substring: str, label: str, timeouts: ATSTimeoutsConfig | None = None) -> bool:
    """Pick ``label`` in the listbox beside a questionnaire row (Pluralsight step 3)."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    row_selectors = (
        lambda: locator("li").filter(has_text=question_substring),
        lambda: locator("[data-automation-id^='formField-']").filter(
            has_text=question_substring
        ),
        lambda: locator("fieldset").filter(has_text=question_substring),
    )
    for row_factory in row_selectors:
        row = row_factory()
        try:
            if row.count() == 0:
                continue
            row.first.locator("button[aria-haspopup='listbox']").click(
                timeout=t.wizard_click_timeout_ms
            )
        except Exception:  # noqa: BLE001
            continue
        _wait_brief(page, t.page_load_wait_ms)
        if _click_listbox_option(page, label, t.wizard_click_timeout_ms):
            return True
    return False


def _non_settings_listbox_indices(page: PageDriver) -> list[int]:
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return []
    buttons = locator("button[aria-haspopup='listbox']")
    indices: list[int] = []
    try:
        count = buttons.count()
    except Exception:  # noqa: BLE001
        return []
    for i in range(count):
        try:
            text = buttons.nth(i).inner_text(timeout=1_000).lower()
        except Exception:  # noqa: BLE001
            continue
        if "settings" in text:
            continue
        indices.append(i)
    return indices


def select_formfield_listbox(page: PageDriver, form_field_id: str, label: str, timeouts: ATSTimeoutsConfig | None = None) -> bool:
    """Open a listbox inside ``[data-automation-id='formField-…']`` and pick ``label``."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    trigger = f"[data-automation-id='{form_field_id}'] button"
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return _try_click(page, trigger, timeout_ms=t.wizard_click_timeout_ms) and _click_listbox_option(
            page, label, t.wizard_click_timeout_ms
        )
    try:
        locator(trigger).first.click(timeout=t.wizard_click_timeout_ms)
    except Exception:  # noqa: BLE001
        return False
    _wait_brief(page, t.page_load_wait_ms)
    return _click_listbox_option(page, label, t.wizard_click_timeout_ms)


_LISTBOX_PLACEHOLDER = frozenset({"", "select one"})

_DECLINE_OPTION_MARKERS = (
    "wish to answer",
    "wish to self-identify",
    "self-identify",
    "not a veteran",
    "decline",
    "prefer not",
    "choose not",
)

_VETERAN_STATUS_BUTTON = "personalInfoUS--veteranStatus"
_VETERAN_DECLINE_LABELS = (
    "I DO NOT WISH TO SELF-IDENTIFY",
    "I AM NOT A VETERAN",
)


def listbox_committed(page: PageDriver, button_id: str, timeouts: ATSTimeoutsConfig | None = None) -> bool:
    """True when a listbox trigger shows a committed value (not placeholder)."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    if not _is_visible(page, f"#{button_id}", timeout_ms=t.candidate_timeout_ms):
        return False
    current = _listbox_button_label(page, button_id).lower()
    return current not in _LISTBOX_PLACEHOLDER


def date_spin_filled(page: PageDriver, selector: str, timeouts: ATSTimeoutsConfig | None = None) -> bool:
    """True when a date spinbutton input already holds a non-empty value."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    try:
        value = locator(selector).first.input_value(timeout=t.brief_wait_ms)
    except Exception:  # noqa: BLE001
        return False
    return bool(value.strip())


def fill_date_spin_input(page: PageDriver, selector: str, value: str, timeouts: ATSTimeoutsConfig | None = None) -> bool:
    """Fill one MM/DD/YYYY spinbutton and blur so Workday commits the value."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    if not value:
        return False
    if date_spin_filled(page, selector, timeouts):
        return True
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return _try_fill(page, selector, value, timeout_ms=t.wizard_click_timeout_ms)
    try:
        field = locator(selector).first
        field.click(timeout=t.wizard_click_timeout_ms)
        field.fill(value, timeout=t.wizard_click_timeout_ms)
        field.blur()
        return True
    except Exception:  # noqa: BLE001
        return _try_fill(page, selector, value, timeout_ms=t.wizard_click_timeout_ms)


def click_disability_option(page: PageDriver, label: str, *, fallback: str = "", timeouts: ATSTimeoutsConfig | None = None) -> bool:
    """Click a disability self-identify control by visible label text (radio or checkbox)."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    for text in (label, fallback):
        if not text:
            continue
        try:
            loc = locator("label").filter(has_text=text).first
            loc.scroll_into_view_if_needed(timeout=t.page_load_wait_ms)
            for control_sel in ("input[type='checkbox']", "input[type='radio']"):
                control = loc.locator(control_sel)
                if control.count() > 0:
                    control.first.click(timeout=t.wizard_click_timeout_ms, force=True)
                    return True
            loc.click(timeout=t.wizard_click_timeout_ms, force=True)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def disability_option_selected(page: PageDriver, *labels: str) -> bool:
    """True when any disability label in ``labels`` is checked (radio or checkbox)."""
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    for text in labels:
        if not text:
            continue
        try:
            label = locator("label").filter(has_text=text).first
            for control_sel in ("input[type='checkbox']", "input[type='radio']"):
                control = label.locator(control_sel)
                if control.count() > 0 and control.first.is_checked():
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _listbox_button_label(page: PageDriver, button_id: str) -> str:
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return ""
    try:
        return locator(f"#{button_id}").first.inner_text(timeout=1_000).strip()
    except Exception:  # noqa: BLE001
        return ""


def select_listbox_first_match(page: PageDriver, button_id: str, *labels: str, timeouts: ATSTimeoutsConfig | None = None) -> bool:
    """Open a listbox button and pick the first matching ``labels`` or decline option."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    if not _is_visible(page, f"#{button_id}"):
        return False
    current = _listbox_button_label(page, button_id).lower()
    if current not in _LISTBOX_PLACEHOLDER:
        return True
    _try_click(page, f"#{button_id}", timeout_ms=t.wizard_click_timeout_ms)
    _wait_brief(page, t.page_load_wait_ms)
    for label in labels:
        if label and _click_listbox_option(page, label, t.wizard_click_timeout_ms):
            return True
    return _click_listbox_decline_option(page, t.wizard_click_timeout_ms)


def select_veteran_status_listbox(page: PageDriver, value: str | None, timeouts: ATSTimeoutsConfig | None = None) -> bool:
    """Pick veteran status — Circle uses uppercase decline labels, not DEI wording."""
    labels = (value,) if value else _VETERAN_DECLINE_LABELS
    return select_listbox_first_match(page, _VETERAN_STATUS_BUTTON, *labels, timeouts=timeouts)


def _click_listbox_decline_option(page: PageDriver, timeout_ms: int = 8000) -> bool:
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    try:
        options = locator("[role='listbox'] [role='option']")
        for i in range(options.count()):
            text = options.nth(i).inner_text(timeout=1000)
            lo = text.strip().lower()
            if lo in _LISTBOX_PLACEHOLDER:
                continue
            if any(marker in lo for marker in _DECLINE_OPTION_MARKERS):
                options.nth(i).click(timeout=timeout_ms)
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def select_listbox_button(page: PageDriver, button_id: str, label: str, timeouts: ATSTimeoutsConfig | None = None) -> bool:
    """Open a Workday listbox button and pick ``label`` (raghuboosetty pattern)."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    if not label:
        return False
    trigger = f"#{button_id}"
    if not _is_visible(page, trigger):
        return False
    _try_click(page, trigger, timeout_ms=t.wizard_click_timeout_ms)
    _wait_brief(page, t.page_load_wait_ms)
    return _click_listbox_option(page, label, t.wizard_click_timeout_ms)


def _click_listbox_option(page: PageDriver, label: str, timeout_ms: int = 8000) -> bool:
    get_by_role = getattr(page, "get_by_role", None)
    if callable(get_by_role):
        try:
            get_by_role("option", name=label, exact=True).click(timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001
            pass
    locator = getattr(page, "locator", None)
    if callable(locator):
        for candidate in (
            f"[role='option'][aria-label='{_css_attr(label)} not checked']",
            f"[role='listbox'] [role='option'][aria-label='{_css_attr(label)} not checked']",
        ):
            try:
                opt = locator(candidate)
                if opt.count() > 0:
                    opt.first.click(timeout=timeout_ms)
                    return True
            except Exception:  # noqa: BLE001
                continue
        try:
            locator("[role='listbox'] [role='option']").filter(
                has_text=label
            ).first.click(timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001
            pass
    return _try_click(
        page,
        f"[role='listbox'] [role='option']:has-text('{label}')",
        timeout_ms=timeout_ms,
    )


def fill_search_multiselect(
    page: PageDriver,
    input_selector: str,
    search: str,
    *,
    option_label: str | None = None,
    timeouts: ATSTimeoutsConfig | None = None,
) -> bool:
    """Searchable multiselect (Circle education school): type, Enter, pick option."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    if not search:
        return False
    pick = option_label or search
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    try:
        field = locator(input_selector).first
        field.click(timeout=t.wizard_click_timeout_ms)
        field.fill(search, timeout=t.wizard_click_timeout_ms)
    except Exception:  # noqa: BLE001
        return False
    keyboard = getattr(page, "keyboard", None)
    if keyboard is not None:
        press = getattr(keyboard, "press", None)
        if callable(press):
            try:
                press("Enter")
            except Exception:  # noqa: BLE001
                pass
    _wait_brief(page, t.page_load_wait_ms)
    if click_prompt_option(page, pick, timeouts):
        return True
    # Tenant labels sometimes differ slightly from the search string.
    try:
        locator("[data-automation-id='promptOption']").filter(has_text=search).first.click(
            timeout=t.wizard_click_timeout_ms
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def click_automation_checkbox(page: PageDriver, automation_id: str, timeouts: ATSTimeoutsConfig | None = None) -> bool:
    """Click a Workday custom checkbox identified by ``data-automation-id``."""
    from magicapply.config.models import ATSTimeoutsConfig

    t = timeouts or ATSTimeoutsConfig()
    selector = f"[data-automation-id='{_css_attr(automation_id)}']"
    locator = getattr(page, "locator", None)
    if callable(locator):
        try:
            loc = locator(selector)
            if loc.is_visible(timeout=t.step_transition_wait_ms):
                loc.click(timeout=t.wizard_click_timeout_ms, force=True)
                return True
        except Exception:  # noqa: BLE001
            pass
    return _try_click(page, selector, timeout_ms=t.wizard_click_timeout_ms)


def _css_attr(value: str) -> str:
    """Escape a value for use inside a single-quoted CSS attribute selector."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _is_visible(page: PageDriver, selector: str, *, timeout_ms: int = 500) -> bool:
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    try:
        return locator(selector).first.is_visible(timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        return False


def _try_click(page: PageDriver, selector: str, *, timeout_ms: int = 500) -> bool:
    try:
        page.click(selector, timeout=timeout_ms)  # type: ignore[call-arg]
        return True
    except Exception:  # noqa: BLE001
        return False


def _try_fill(page: PageDriver, selector: str, value: str, *, timeout_ms: int = 500) -> bool:
    try:
        page.fill(selector, value, timeout=timeout_ms)  # type: ignore[call-arg]
        return True
    except Exception:  # noqa: BLE001
        return False


def _wait_brief(page: PageDriver, ms: int) -> None:
    wait = getattr(page, "wait_for_timeout", None)
    if callable(wait):
        wait(ms)
    else:
        time.sleep(ms / 1000)
