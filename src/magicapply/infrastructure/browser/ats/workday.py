"""Workday application handler.

Workday's forms are consistent across employers because every field carries
a ``data-automation-id`` attribute keyed on a Workday-internal id. That id
is stable enough to hard-code the identity + resume-upload selectors here.

The real flow is a multi-step wizard (My Information → My Experience →
Application Questions → Voluntary Disclosures → Review & Submit). Each
``Next`` button lands the user on the next step; the handler clicks
through them until it reaches the Review step and then clicks Submit.

Step 1 is always Create Account / Sign In on real tenants. Credentials are
persisted per tenant in ``data/workday_accounts.yaml`` via
``WorkdayAccountStore`` so later runs sign in instead of re-creating.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

from magicapply.infrastructure.browser.ats import workday_widgets
from magicapply.infrastructure.browser.ats.base import (
    ApplicationData,
    BaseATSHandler,
    PageDriver,
)
from magicapply.infrastructure.browser.ats.router_dispatch import fill_dynamic_fields
from magicapply.infrastructure.browser.ats.workday_accounts import WorkdayAccountStore

if TYPE_CHECKING:
    from magicapply.config.models import ATSTimeoutsConfig

logger = logging.getLogger(__name__)

_MATCH_HOSTS = ("myworkdayjobs.com", ".myworkday.com")

_FIRST_NAME_SELECTORS = (
    "[data-automation-id='legalNameSection_firstName']",
    "input[data-automation-id='firstName']",
    "input[name='firstName']",
)
_LAST_NAME_SELECTORS = (
    "[data-automation-id='legalNameSection_lastName']",
    "input[data-automation-id='lastName']",
    "input[name='lastName']",
)
_EMAIL_SELECTORS = (
    "[data-automation-id='email']",
    "input[data-automation-id='emailAddress']",
    "input[type='email']",
)
_PHONE_SELECTORS = (
    "[data-automation-id='phone-number']",
    "[data-automation-id='phoneNumber']",
    "input[data-automation-id='phone']",
)
_RESUME_FILE_SELECTORS = (
    "[data-automation-id='file-upload-input-ref']",
    "input[data-automation-id='resume-upload']",
    "input[type='file']",
)
_NEXT_BUTTONS = (
    "[data-automation-id='pageFooterNextButton']",
    "[data-automation-id='bottom-navigation-next-button']",
    "button[data-automation-id='wd-CommandButton_uic_next']",
    "button[data-automation-id='next']",
)
_SUBMIT_BUTTONS = (
    "[data-automation-id='submitApplication']",
    "[data-automation-id='submit']",
    "button[data-automation-id='wd-CommandButton_uic_submit']",
    "button:has-text('Submit')",
)

# Community-consensus auth selectors (see raghuboosetty/workday, workday_auto).
_AUTH_EMAIL = "input[type='text'][data-automation-id='email']"
_AUTH_PASSWORD = "input[type='password'][data-automation-id='password']"
_AUTH_VERIFY_PASSWORD = "input[type='password'][data-automation-id='verifyPassword']"
_CREATE_ACCOUNT = (
    "div[role='button'][aria-label='Create Account'][data-automation-id='click_filter']"
)
_SIGN_IN = "div[role='button'][aria-label='Sign In'][data-automation-id='click_filter']"
_CONSENT_CHECKBOX = "input[type='checkbox'][data-automation-id='createAccountCheckbox']"

_WIZARD_LANDMARKS = (
    *_FIRST_NAME_SELECTORS,
    _AUTH_EMAIL,
    "input[type='password']",
)

_WORKDAY_FORM_ROOT = "[data-automation-id='applyFlowPage']"
_WORKDAY_REVIEW_ROOT = "[data-automation-id='applyFlowReviewPage']"
_REVIEW_FORM_SELECTORS = (
    _WORKDAY_REVIEW_ROOT,
    _WORKDAY_FORM_ROOT,
    "#root",
)
_MAX_STEPS = 10


class WorkdayHandler(BaseATSHandler):
    @classmethod
    def matches(cls, url: str) -> bool:
        u = url.lower()
        return any(host in u for host in _MATCH_HOSTS)

    def _navigate(self, page: PageDriver, data: ApplicationData) -> None:
        timeouts = _get_timeouts(data)
        _workday_goto(page, data.job_url)
        _wait_brief(page, timeouts.page_load_wait_ms)
        _accept_legal_notice(page, timeouts)
        _ensure_english_locale(page, data.job_url, timeouts)
        _click_any(
            page,
            (
                "a[data-automation-id='adventureButton']",
                "[data-automation-id='adventureButton']",
            ),
            timeout_ms=timeouts.wizard_click_timeout_ms,
        )
        _wait_brief(page, timeouts.step_transition_wait_ms)
        _click_any(
            page,
            (
                "[data-automation-id='applyManually']",
                "[data-automation-id='autofillWithResume']",
            ),
            timeout_ms=timeouts.wizard_click_timeout_ms,
        )
        _wait_brief(page, timeouts.step_transition_wait_ms)
        _ensure_english_locale(page, data.job_url, timeouts)

    def _fill_static(self, page: PageDriver, data: ApplicationData) -> None:
        answers = data.static_answers
        _try_fill(page, _FIRST_NAME_SELECTORS, _first_name(answers.full_name))
        _try_fill(page, _LAST_NAME_SELECTORS, _last_name(answers.full_name))
        _try_fill(page, _EMAIL_SELECTORS, answers.email)
        if answers.phone:
            _try_fill(page, _PHONE_SELECTORS, answers.phone)

    def _fill_dynamic(self, page: PageDriver, data: ApplicationData) -> None:
        timeouts = _get_timeouts(data)
        _wait_for_any(page, _WIZARD_LANDMARKS, timeout_ms=15_000)
        _ensure_authenticated(page, data)
        _ensure_english_locale(page, data.job_url, timeouts)

        for _ in range(_MAX_STEPS):
            _ensure_english_locale(page, data.job_url, timeouts)
            if _already_applied(page):
                logger.info("Workday: job already applied — stopping wizard")
                break
            if _on_login_page(page):
                _workday_sign_in(page, data)
                _ensure_english_locale(page, data.job_url, timeouts)
                if _on_login_page(page):
                    break
            if _at_review_step(page):
                data.resolutions_log.clear()
                _fill_workday_voluntary_disclosures(page, data)
                _fill_workday_self_identify(page, data)
                _fill_workday_experience(page, data)
                _fill_workday_review_education(page, data)
                fill_dynamic_fields(
                    page,
                    data,
                    ats="workday",
                    form_selectors=_REVIEW_FORM_SELECTORS,
                    schema_id="workday_review",
                    handler_label="Workday",
                )
                break
            _wait_brief(page, timeouts.brief_wait_ms)
            _try_resume_upload(page, data)
            _fill_workday_widgets(page, data)
            _fill_workday_experience(page, data)
            _fill_workday_application_questions(page, data)
            _fill_workday_voluntary_disclosures(page, data)
            _fill_workday_self_identify(page, data)
            fill_dynamic_fields(
                page,
                data,
                ats="workday",
                form_selectors=(_WORKDAY_FORM_ROOT, "form"),
                schema_id="workday_wizard",
                handler_label="Workday",
            )
            if not _click_any(page, _NEXT_BUTTONS, timeout_ms=timeouts.wizard_click_timeout_ms):
                break
            _wait_brief(page, timeouts.step_transition_wait_ms)

    def _submit(self, page: PageDriver, data: ApplicationData) -> None:
        timeouts = _get_timeouts(data)
        for selector in _SUBMIT_BUTTONS:
            if _try_click(page, selector, timeout_ms=timeouts.wizard_click_timeout_ms):
                return
        raise RuntimeError("Workday: no submit button found")


def _get_timeouts(data: ApplicationData) -> ATSTimeoutsConfig:
    """Extract timeout config from ApplicationData, with fallback defaults."""
    if data.ats_timeouts is not None:
        return data.ats_timeouts
    # Fallback for tests or old code paths
    from magicapply.config.models import ATSTimeoutsConfig
    return ATSTimeoutsConfig()


def _ensure_authenticated(page: PageDriver, data: ApplicationData) -> None:
    timeouts = _get_timeouts(data)
    tenant, email, password, has_stored = _credentials(data)
    if not password:
        raise RuntimeError(
            f"Workday: no apply password configured for tenant {tenant}. "
            f"Set static_answers.workday_apply_password or ensure account exists in "
            f"data/workday_accounts.yaml"
        )

    if _on_login_page(page):
        if _workday_sign_in(page, data):
            _persist_account(data, tenant, email, password, created=not has_stored)
            _advance_past_auth_landing(page, timeouts)
            _ensure_english_locale(page, data.job_url, timeouts)
        return

    if _wizard_authenticated(page):
        _ensure_english_locale(page, data.job_url, timeouts)
        return

    if has_stored:
        _goto_login_and_sign_in(page, data, tenant, email, password)
        _ensure_english_locale(page, data.job_url, timeouts)
        return

    if not _on_account_step(page):
        return

    if _workday_create_account(page, data, email=email, password=password):
        if _on_login_page(page):
            if _workday_sign_in(page, data):
                _persist_account(data, tenant, email, password, created=True)
                _advance_past_auth_landing(page, timeouts)
                _ensure_english_locale(page, data.job_url, timeouts)
        elif not _on_account_step(page):
            _persist_account(data, tenant, email, password, created=True)
            _advance_past_auth_landing(page, timeouts)
            _ensure_english_locale(page, data.job_url, timeouts)


def _goto_login_and_sign_in(
    page: PageDriver,
    data: ApplicationData,
    tenant: str,
    email: str,
    password: str,
) -> None:
    timeouts = _get_timeouts(data)
    login_url = WorkdayAccountStore.careers_login_url(getattr(page, "url", "") or "")
    if not login_url:
        return
    _workday_goto(page, login_url)
    _wait_brief(page, timeouts.page_load_wait_ms)
    _accept_legal_notice(page, timeouts)
    if _workday_sign_in(page, data):
        store = _store(data)
        if store is not None:
            store.touch(tenant)
        _advance_past_auth_landing(page, timeouts)
        _ensure_english_locale(page, data.job_url, timeouts)


def _wizard_authenticated(page: PageDriver) -> bool:
    return (
        _selector_visible(page, _FIRST_NAME_SELECTORS[0], timeout_ms=2_000)
        or _selector_visible(page, _NEXT_BUTTONS[0], timeout_ms=2_000)
        or _at_review_step(page)
    )


def _workday_create_account(
    page: PageDriver,
    data: ApplicationData,
    *,
    email: str,
    password: str,
) -> bool:
    timeouts = _get_timeouts(data)
    _accept_legal_notice(page, timeouts)
    if not _wait_for_selector(page, _AUTH_EMAIL, timeout_ms=10_000):
        fill_dynamic_fields(
            page,
            data,
            ats="workday",
            form_selectors=(_WORKDAY_FORM_ROOT, "form"),
            schema_id="workday_auth",
            handler_label="Workday",
        )
    _try_fill(page, (_AUTH_EMAIL,), email, timeout_ms=timeouts.auth_fill_timeout_ms)
    _try_fill(page, (_AUTH_PASSWORD,), password, timeout_ms=timeouts.auth_fill_timeout_ms)
    if _selector_visible(page, _AUTH_VERIFY_PASSWORD, timeout_ms=1_000):
        _try_fill(
            page,
            (_AUTH_VERIFY_PASSWORD,),
            password,
            timeout_ms=timeouts.auth_fill_timeout_ms,
        )
    _try_check_consent(page, data)
    clicked = _try_click(page, _CREATE_ACCOUNT, timeout_ms=timeouts.wizard_click_timeout_ms)
    if clicked:
        _wait_brief(page, timeouts.page_load_wait_ms)
    return clicked


def _advance_past_auth_landing(page: PageDriver, timeouts: ATSTimeoutsConfig) -> None:
    """After sign-in Workday often lands on applyManually before step 2."""
    _workday_force_en_us_url(page, timeouts)
    _wait_for_any(
        page,
        (*_NEXT_BUTTONS, *_FIRST_NAME_SELECTORS),
        timeout_ms=15_000,
    )
    if _selector_visible(page, _FIRST_NAME_SELECTORS[0], timeout_ms=2_000):
        return
    if _click_any(page, _NEXT_BUTTONS, timeout_ms=timeouts.wizard_click_timeout_ms):
        _wait_brief(page, timeouts.step_transition_wait_ms)


def _workday_sign_in(page: PageDriver, data: ApplicationData) -> bool:
    timeouts = _get_timeouts(data)
    _wait_brief(page, timeouts.step_transition_wait_ms)
    _accept_legal_notice(page, timeouts)
    tenant, email, password, _ = _credentials(data)
    if not password:
        return False
    if not _wait_for_selector(page, _AUTH_EMAIL, timeout_ms=10_000):
        return False
    _try_fill(page, (_AUTH_EMAIL,), email, timeout_ms=timeouts.auth_fill_timeout_ms)
    _try_fill(page, (_AUTH_PASSWORD,), password, timeout_ms=timeouts.auth_fill_timeout_ms)
    if not _wait_for_selector(page, _SIGN_IN, timeout_ms=5_000):
        return False
    _wait_brief(page, timeouts.brief_wait_ms)
    clicked = _try_click(page, _SIGN_IN, timeout_ms=timeouts.wizard_click_timeout_ms)
    if not clicked:
        return False
    _wait_until_not_login(page, timeout_ms=15_000)
    if _on_login_page(page):
        return False
    _wait_for_any(page, (*_NEXT_BUTTONS, *_FIRST_NAME_SELECTORS), timeout_ms=15_000)
    store = _store(data)
    if store is not None:
        store.touch(tenant)
    return True


def _try_check_consent(page: PageDriver, data: ApplicationData) -> None:
    if _try_check(page, _CONSENT_CHECKBOX):
        return
    fill_dynamic_fields(
        page,
        data,
        ats="workday",
        form_selectors=(_WORKDAY_FORM_ROOT, "form"),
        schema_id="workday_consent",
        handler_label="Workday",
    )


def _credentials(data: ApplicationData) -> tuple[str, str, str, bool]:
    tenant = WorkdayAccountStore.tenant_from_url(data.job_url)
    email = data.static_answers.email
    store = _store(data)
    if store is not None and (account := store.get(tenant)):
        return tenant, account.email, account.password, True
    password = data.static_answers.workday_apply_password or ""
    return tenant, email, password, False


def _persist_account(
    data: ApplicationData,
    tenant: str,
    email: str,
    password: str,
    *,
    created: bool,
) -> None:
    store = _store(data)
    if store is None:
        return
    store.upsert(tenant, email=email, password=password)
    if created:
        logger.info("Workday: saved new apply account for tenant %s", tenant)


def _store(data: ApplicationData) -> WorkdayAccountStore | None:
    store = data.workday_account_store
    return store if isinstance(store, WorkdayAccountStore) else None


def _on_login_page(page: PageDriver) -> bool:
    return "/login" in (getattr(page, "url", "") or "").lower()


def _on_account_step(page: PageDriver) -> bool:
    if _on_login_page(page):
        return True
    if _selector_visible(page, _FIRST_NAME_SELECTORS[0], timeout_ms=500):
        return False
    return _selector_visible(page, _AUTH_EMAIL, timeout_ms=1_000)


def _at_review_step(page: PageDriver) -> bool:
    """Review step — active progress bar step or a visible Submit control."""
    if _on_account_step(page):
        return False
    for selector in _SUBMIT_BUTTONS:
        if _selector_visible(page, selector, timeout_ms=500):
            return True
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    try:
        active = locator("[data-automation-id='progressBarActiveStep']").first
        if active.is_visible(timeout=500):
            if "review" in active.inner_text(timeout=500).lower():
                return True
    except Exception:  # noqa: BLE001
        pass
    try:
        for heading in locator("h2").all_inner_texts():
            if heading.strip().lower() == "review":
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _already_applied(page: PageDriver) -> bool:
    try:
        content = page.content().lower()
    except Exception:  # noqa: BLE001
        return False
    return any(
        marker in content
        for marker in (
            "data-automation-id='alreadyapplied'",
            "application submitted",
            "you have already applied",
            "my applications",
        )
    )


def _try_fill(
    page: PageDriver,
    selectors: tuple[str, ...],
    value: str,
    *,
    timeout_ms: int = 500,
) -> bool:
    for selector in selectors:
        try:
            page.fill(selector, value, timeout=timeout_ms)  # type: ignore[call-arg]
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _try_click(
    page: PageDriver, selector: str, *, timeout_ms: int = 500
) -> bool:
    try:
        page.click(selector, timeout=timeout_ms)  # type: ignore[call-arg]
        return True
    except Exception:  # noqa: BLE001
        return False


def _try_check(page: PageDriver, selector: str, timeout_ms: int = 500) -> bool:
    try:
        page.check(selector, timeout=timeout_ms)  # type: ignore[call-arg]
        return True
    except Exception:  # noqa: BLE001
        return False


def _click_any(
    page: PageDriver,
    selectors: tuple[str, ...],
    *,
    timeout_ms: int = 500,
) -> bool:
    for selector in selectors:
        if _try_click(page, selector, timeout_ms=timeout_ms):
            return True
    return False


def _wait_brief(page: PageDriver, ms: int) -> None:
    wait = getattr(page, "wait_for_timeout", None)
    if callable(wait):
        wait(ms)
    else:
        time.sleep(ms / 1000)


def _wait_for_any(
    page: PageDriver, selectors: tuple[str, ...], *, timeout_ms: int
) -> bool:
    per = max(timeout_ms // max(len(selectors), 1), 500)
    for selector in selectors:
        if _wait_for_selector(page, selector, timeout_ms=per):
            return True
    return False


def _wait_for_selector(page: PageDriver, selector: str, *, timeout_ms: int) -> bool:
    wait_for = getattr(page, "wait_for_selector", None)
    if not callable(wait_for):
        return _selector_visible(page, selector, timeout_ms=timeout_ms)
    try:
        wait_for(selector, timeout=timeout_ms, state="visible")
        return True
    except Exception:  # noqa: BLE001
        return False


def _wait_until_not_login(page: PageDriver, *, timeout_ms: int) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if not _on_login_page(page):
            return
        _wait_brief(page, 500)


def _workday_goto(page: PageDriver, url: str) -> None:
    """Navigate to a Workday URL, always rewriting the path locale to ``en-US``."""
    page.goto(WorkdayAccountStore.normalize_en_us_url(url))


def _ensure_english_locale(page: PageDriver, job_url: str, timeouts: "ATSTimeoutsConfig") -> None:
    """Force en-US before any form work — language-agnostic, not reactive."""
    _workday_force_en_us_url(page, timeouts)
    _workday_force_english_language(page, timeouts)
    _workday_force_en_us_url(page, timeouts)


def _workday_force_en_us_url(page: PageDriver, timeouts: "ATSTimeoutsConfig") -> None:
    """Rewrite the current Workday URL onto ``en-US`` when the locale segment differs."""
    current = getattr(page, "url", "") or ""
    if not current or "myworkdayjobs.com" not in current:
        return
    if not WorkdayAccountStore.url_needs_en_us(current):
        return
    try:
        page.goto(WorkdayAccountStore.normalize_en_us_url(current))
        _wait_brief(page, timeouts.step_transition_wait_ms)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Workday: en-US URL rewrite failed: %s", exc)


def _is_english_language_label(label: str) -> bool:
    """True only when the header language control already shows an English locale."""
    lo = label.strip().lower()
    return lo == "english" or lo.startswith("english ") or lo.startswith("english(")


def _workday_force_english_language(page: PageDriver, timeouts: "ATSTimeoutsConfig") -> None:
    """Open the globe menu and select English unless English is already active."""
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return
    try:
        button = locator("#languageSelectorButton")
        if not button.is_visible(timeout=1_000):
            return
        if _is_english_language_label(button.inner_text(timeout=500)):
            return
        button.click(timeout=timeouts.wizard_click_timeout_ms)
        _wait_brief(page, timeouts.brief_wait_ms)
        for english_label in (
            "English (United States)",
            "English (US)",
            "English (United States of America)",
            "English",
        ):
            try:
                locator("[role='option']").filter(has_text=english_label).first.click(
                    timeout=timeouts.wizard_click_timeout_ms
                )
                _wait_brief(page, timeouts.step_transition_wait_ms)
                return
            except Exception:  # noqa: BLE001
                continue
    except Exception as exc:  # noqa: BLE001
        logger.debug("Workday: language selector failed: %s", exc)


def _accept_legal_notice(page: PageDriver, timeouts: "ATSTimeoutsConfig") -> None:
    selector = "[data-automation-id='legalNoticeAcceptButton']"
    if _selector_visible(page, selector, timeout_ms=3_000):
        _try_click(page, selector, timeout_ms=timeouts.wizard_click_timeout_ms)
        _wait_brief(page, timeouts.brief_wait_ms)


def _fill_workday_widgets(page: PageDriver, data: ApplicationData) -> None:
    """Workday listbox / multiselect widgets (raghuboosetty/workday patterns)."""
    if not _selector_visible(page, "[data-fkit-id='source--source']", timeout_ms=1_000):
        return
    timeouts = _get_timeouts(data)
    answers = data.static_answers
    workday_widgets.select_listbox_button(page, "address--countryRegion", answers.state or "", timeouts)
    workday_widgets.select_listbox_button(page, "phoneNumber--phoneType", answers.phone_device_type or "", timeouts)
    if answers.country_phone_code:
        phone_search = answers.country_phone_code.split("(")[0].strip()
        workday_widgets.fill_multiselect(
            page,
            "phoneNumber--countryPhoneCode",
            answers.country_phone_code,
            search=phone_search or answers.country,
            timeouts=timeouts,
        )
    if answers.how_did_you_hear:
        # Listbox tenants (Circle) must be tried first — multiselect open
        # clicks the same trigger and leaves the dropdown closed for the
        # listbox fallback.
        heard = workday_widgets.select_listbox_button(
            page, "source--source", answers.how_did_you_hear, timeouts
        )
        if not heard:
            workday_widgets.fill_multiselect(
                page,
                "source--source",
                answers.how_did_you_hear,
                parent_label=answers.how_did_you_hear_parent,
                timeouts=timeouts,
            )
    if answers.city:
        _try_fill(page, ("#address--city",), answers.city)
    if answers.workday_sms_opt_in:
        workday_widgets.click_automation_checkbox(page, "phone-sms-opt-in", timeouts)


_MY_EXP_PAGE = "[data-automation-id='applyFlowMyExpPage']"
_WORK_EXP_ADD = "[aria-labelledby='Work-Experience-section'] [data-automation-id='add-button']"
_SCHOOL_INPUTS = (
    "input[id*='--school']",
    "input[id*='--schoolName']",
    "[data-automation-id='formField-school'] input",
    "[data-automation-id='formField-schoolName'] input",
)
_EDUCATION_ADD = (
    "[aria-labelledby*='Education'] [data-automation-id='add-button']",
    "[data-automation-id='applyFlowMyExpPage'] [data-automation-id='add-button']",
)

_DEGREE_LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "phd": ("PhD", "Doctorate", "Doctor of Philosophy", "Doctor of Philosophy (PhD)"),
    "ms": ("Master's", "Masters", "Master of Science", "MS"),
    "bs": ("Bachelor's", "Bachelors", "Bachelor of Science", "BS"),
}


def _fill_workday_experience(page: PageDriver, data: ApplicationData) -> None:
    """My Experience step — first job + education from tailored resume (raghuboosetty page 2)."""
    timeouts = _get_timeouts(data)
    on_exp_page = _selector_visible(page, _MY_EXP_PAGE, timeout_ms=2_000)
    has_school_input = any(
        _selector_visible(page, sel, timeout_ms=500) for sel in _SCHOOL_INPUTS
    )
    if not on_exp_page and not has_school_input:
        return
    resume = data.tailored_resume
    if resume.experience:
        if not _selector_visible(page, "[data-automation-id='formField-jobTitle']", timeout_ms=500):
            _try_click(page, _WORK_EXP_ADD, timeout_ms=timeouts.wizard_click_timeout_ms)
            _wait_brief(page, timeouts.step_transition_wait_ms)
        exp = resume.experience[0]
        _try_fill(page, ("[data-automation-id='formField-jobTitle'] input",), exp.title)
        _try_fill(page, ("[data-automation-id='formField-companyName'] input",), exp.company)
        _fill_workday_month_year(page, "formField-startDate", exp.start)
        end = (exp.end or "").strip().lower()
        if end in {"", "present", "current"}:
            _try_check(page, "[data-automation-id='formField-currentlyWorkHere'] input[type='checkbox']")
        else:
            _fill_workday_month_year(page, "formField-endDate", end)
        if exp.bullets:
            _try_fill(
                page,
                ("[data-automation-id='formField-roleDescription'] textarea",),
                exp.bullets[0],
            )
    if resume.education:
        edu = resume.education[0]
        if edu.school:
            _fill_education_school(page, edu.school, timeouts)
            _wait_brief(page, timeouts.brief_wait_ms)
        if edu.degree:
            for label in _degree_option_labels(edu.degree):
                if workday_widgets.select_formfield_listbox(page, "formField-degree", label, timeouts):
                    break
            _wait_brief(page, timeouts.brief_wait_ms)
        if edu.field:
            _try_fill(
                page,
                ("[data-automation-id='formField-fieldOfStudy'] input",),
                edu.field,
            )


def _fill_education_school(page: PageDriver, school: str, timeouts: ATSTimeoutsConfig) -> bool:
    """Circle uses ``#education-N--school``; Pluralsight uses ``--schoolName``."""
    for selector in _SCHOOL_INPUTS:
        if workday_widgets.fill_search_multiselect(page, selector, school, timeouts=timeouts):
            return True
    for selector in _SCHOOL_INPUTS:
        if _try_fill(page, (selector,), school):
            return True
    return False


def _fill_workday_review_education(page: PageDriver, data: ApplicationData) -> None:
    """Fill editable education gaps on the Review step (Circle read/edit panels)."""
    timeouts = _get_timeouts(data)
    if not _at_review_step(page):
        return
    resume = data.tailored_resume
    if not resume.education:
        return
    edu = resume.education[0]
    if not edu.school:
        return
    for selector in _SCHOOL_INPUTS:
        if not _selector_visible(page, selector, timeout_ms=500):
            continue
        if _input_value(page, selector):
            return
        if _fill_education_school(page, edu.school, timeouts):
            return
    for add_sel in _EDUCATION_ADD:
        if _try_click(page, add_sel, timeout_ms=timeouts.wizard_click_timeout_ms):
            _wait_brief(page, timeouts.step_transition_wait_ms)
            if _fill_education_school(page, edu.school, timeouts):
                return


def _input_value(page: PageDriver, selector: str) -> str:
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return ""
    try:
        return locator(selector).first.input_value(timeout=1_000).strip()
    except Exception:  # noqa: BLE001
        return ""


def _degree_option_labels(degree: str) -> tuple[str, ...]:
    key = degree.strip().lower()
    aliases = _DEGREE_LABEL_ALIASES.get(key, ())
    return (degree.strip(), *aliases)


def _fill_workday_voluntary_disclosures(page: PageDriver, data: ApplicationData) -> None:
    """Voluntary Disclosures — composable recipe only."""
    _fill_workday_recipe(page, data, "voluntary_disclosures")


def _fill_workday_self_identify(page: PageDriver, data: ApplicationData) -> None:
    """Disability self-identification — composable recipe only."""
    _fill_workday_recipe(page, data, "self_identify")


def _fill_workday_recipe(
    page: PageDriver,
    data: ApplicationData,
    step_id: str,
) -> bool:
    """Fill a widget step via ``FormComposer`` recipe. Returns True when handled."""
    from magicapply.infrastructure.browser.ats.workday_recipes import (
        self_identify_schema,
        voluntary_disclosures_schema,
    )
    from magicapply.infrastructure.browser.forms.composer import FormComposer

    composer = data.form_composer
    if not isinstance(composer, FormComposer):
        logger.error("Workday: form_composer required for recipe fill (%s)", step_id)
        return False

    if step_id == "voluntary_disclosures":
        recipe = voluntary_disclosures_schema(data.static_answers)
    elif step_id == "self_identify":
        recipe = self_identify_schema(data.static_answers)
    else:
        return False

    if not _selector_visible(page, recipe.form_selector, timeout_ms=2_000):
        return False

    report = composer.fill_recipe(page, recipe)
    if report.unhandled:
        logger.warning("Workday %s unhandled fields: %s", step_id, report.unhandled)
    if report.errors:
        logger.warning("Workday %s fill errors: %s", step_id, report.errors)
    return True


_PRIMARY_QUESTIONS_PAGE = "[data-automation-id='applyFlowPrimaryQuestionsPage']"


def _fill_workday_application_questions(page: PageDriver, data: ApplicationData) -> None:
    """Application Questions — yes/no listboxes (Pluralsight + Circle tenants)."""
    on_page = _selector_visible(page, _PRIMARY_QUESTIONS_PAGE, timeout_ms=2_000) or (
        _selector_visible(page, "button:has-text('authorized to work')", timeout_ms=1_000)
    )
    if not on_page:
        return
    timeouts = _get_timeouts(data)
    answers = data.static_answers
    rules: list[tuple[re.Pattern[str], bool | None]] = [
        (re.compile(r"authori[sz]ed\s+to\s+work", re.IGNORECASE), answers.authorized_to_work_us),
        (
            re.compile(
                r"sponsor\s+work\s+authorization|someday\s+require.*sponsor|require\s+visa",
                re.IGNORECASE,
            ),
            answers.needs_sponsorship_us,
        ),
        (re.compile(r"agreement with your employer", re.IGNORECASE), False),
        (re.compile(r"public official refer", re.IGNORECASE), False),
        (re.compile(r"government agency", re.IGNORECASE), False),
    ]
    workday_widgets.fill_questionnaire_fieldsets(
        page, _PRIMARY_QUESTIONS_PAGE, rules, default_false=True, timeouts=timeouts
    )


def _fill_workday_month_year(page: PageDriver, form_field_id: str, ym: str) -> None:
    """Fill Workday MM/YYYY spinbutton groups (``2025-10`` → month 10, year 2025)."""
    if not ym or "-" not in ym:
        return
    year, month = ym.split("-", 1)
    scope = f"[data-automation-id='{form_field_id}']"
    month_val = str(int(month)) if month.isdigit() else month
    _try_fill(page, (f"{scope} input[aria-label='Month']",), month_val)
    _try_fill(page, (f"{scope} input[aria-label='Year']",), year)


def _try_resume_upload(page: PageDriver, data: ApplicationData) -> None:
    for selector in _RESUME_FILE_SELECTORS:
        try:
            page.set_input_files(selector, str(data.resume_docx_path))
            return
        except Exception:  # noqa: BLE001
            continue


def _selector_visible(
    page: PageDriver, selector: str, *, timeout_ms: int = 500
) -> bool:
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    try:
        return locator(selector).first.is_visible(timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        return False


def _first_name(full: str) -> str:
    return full.strip().split()[0] if full.strip() else ""


def _last_name(full: str) -> str:
    parts = full.strip().split()
    return " ".join(parts[1:]) if len(parts) > 1 else ""
