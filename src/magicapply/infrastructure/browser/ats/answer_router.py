"""Route a scanned FormField to a concrete fill strategy.

The router is deliberately dumb: it inspects the field's label + kind and
picks one of six strategies. Handler code turns each ``ResolvedAnswer`` into
a page call. Anything the router does not recognise is returned as
``strategy="unhandled"`` — the caller logs it and moves on; a real
submission may still succeed if the field is optional.

Priority tiers evaluated inside ``resolve``:
- **static**: identity, contact, DEI, work-auth — value comes straight from
  ``StaticAnswers``.
- **library**: verified answer from ``configs/answer_library.yaml`` — grows
  from real ATS runs. Matched by exact label, then by ``question_regex``.
- **narrative**: open-ended text (why do you want to work here, screening
  questions) — value comes from ``NarrativeEngine.answer(job, label)``.
- **file**: resume upload — value is the tailored DOCX path already on
  ``ApplicationData``.
"""

# NOTE: CoR threshold — this router evaluates six imperative priority
# tiers (static / library / select / check / narrative / file / unhandled)
# inside one method. GOF_PATTERNS.md defers formal Chain of Responsibility
# "if resolution grows past ~3 strategies." We are past the threshold but
# keep the imperative structure because every tier evaluates on the same
# input and is a pure lookup — no independent state, no cross-tier
# feedback. Refactor to CoR the first time a tier grows its own state
# (e.g., an LLM answer memoization tier that caches by question hash).

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from magicapply.config.models import AnswerLibrary, StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.domain.resumes.narrative import NarrativeEngine
from magicapply.infrastructure.browser.ats.form_scan import FormField

Strategy = Literal[
    "static", "library", "select", "check", "narrative", "file", "unhandled"
]


@dataclass
class ResolvedAnswer:
    strategy: Strategy
    value: str = ""  # text value, option value, or file path (as str)
    # For "check" strategy: True means check the box, False means leave alone.
    check: bool = True


# --- Label patterns ---------------------------------------------------------

# Each entry: (compiled regex, StaticAnswers attribute name). First hit wins.
# Regexes are lowercased at match time.
_IDENTITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bfirst[\s-]*name\b"), "_first_name"),
    (re.compile(r"\blast[\s-]*name\b|\bfamily[\s-]*name\b|\bsurname\b"), "_last_name"),
    (re.compile(r"\bfull[\s-]*name\b|^name$"), "full_name"),
    (re.compile(r"\bemail\b"), "email"),
    (
        re.compile(r"\bverify\b.*\bpassword\b|\bconfirm\b.*\bpassword\b"),
        "workday_apply_password",
    ),
    (re.compile(r"\bpassword\b"), "workday_apply_password"),
    (
        re.compile(r"phone\s*number|phone\s*extension|\btelephone\b|\bmobile\b|^phone$"),
        "phone",
    ),
    (re.compile(r"\blinkedin\b"), "linkedin_url"),
    (re.compile(r"\bgithub\b"), "github_url"),
    (re.compile(r"\bportfolio\b|\bwebsite\b|\bpersonal\s+site\b"), "portfolio_url"),
    (re.compile(r"\blocation\b"), "location"),
    (re.compile(r"^city\b|\bcity\*"), "city"),
    (re.compile(r"address\s*line\s*1|street\s*address"), "address_line_1"),
    (re.compile(r"postal\s*code|zip\s*code"), "postal_code"),
    (re.compile(r"country\s+phone\s+code|phone\s+country\s+code"), "country_phone_code"),
    (re.compile(r"phone\s+device\s+type|device\s+type"), "phone_device_type"),
    (re.compile(r"^state\b|\bstate\*"), "state"),
    (re.compile(r"\bcountry\b"), "country"),
    (
        re.compile(r"current.*company|most recent.*company|most recent.*employer"),
        "current_employer",
    ),
    (re.compile(r"\byears?\s+of\s+experience\b"), "years_of_experience"),
    (re.compile(r"\bdesired\s+salary\b|\bsalary\s+expectation\b|\bcompensation\b"), "desired_salary"),
    (re.compile(r"\bwork\s+authorization\b"), "work_authorization"),
]

# For yes/no <select> or radio groups, keyed on StaticAnswers bool attributes.
_YES_NO_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"authori[sz]ed\s+to\s+work"), "authorized_to_work_us"),
    (re.compile(r"legally\s+authori[sz]ed\s+to\s+work"), "authorized_to_work_us"),
    (re.compile(r"eligible to work.*sponsorship", re.IGNORECASE), "authorized_to_work_us"),
    (re.compile(r"require\s+sponsorship|need\s+sponsorship|require\s+visa"), "needs_sponsorship_us"),
    (re.compile(r"previously\s+(?:worked|been\s+employed|employed)"), "previously_employed"),
    (re.compile(r"hispanic|latino|latin[a-z]"), "hispanic_latino"),
    (re.compile(r"located in the united states", re.IGNORECASE), "_us_located"),
]

_EXPERIENCE_YEARS_PATTERN = re.compile(
    r"(\d+)\+?\s*years.*(?:data science|professional)",
    re.IGNORECASE,
)
_SKILL_SCREENING_PATTERN = re.compile(
    r"(?:do you have|are you expert).*(?:experience|expert|deep|production-level|python)",
    re.IGNORECASE,
)

# DEI / EEO free-text (usually a select with specific options).
_DEI_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgender\b"), "gender"),
    (re.compile(r"\brace\b|\bethnicity\b"), "ethnicity"),
    (re.compile(r"\bveteran\b"), "veteran_status"),
    (re.compile(r"\bdisability\b|\bdisabled\b"), "disability_status"),
]

# Consent / compliance labels — used by BOTH the checkbox and the radio/select
# branches. Ashby, iCIMS, and some custom_careers surface acknowledgement
# widgets as single-option radio groups ("I agree" is the only choice); the
# router picks that option instead of leaving it unhandled.
_CONSENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bconsent\b", re.IGNORECASE),
    re.compile(r"terms and conditions", re.IGNORECASE),
    re.compile(r"terms of use", re.IGNORECASE),
    re.compile(r"acknowledge the terms", re.IGNORECASE),
    re.compile(r"demographic data", re.IGNORECASE),
    re.compile(r"i agree", re.IGNORECASE),
    re.compile(r"i accept", re.IGNORECASE),
    re.compile(r"i acknowledge", re.IGNORECASE),
    re.compile(r"i have read and understand", re.IGNORECASE),
]

_CONSENT_OPTION_PHRASES: tuple[str, ...] = (
    "agree",
    "accept",
    "acknowledge",
    "yes",
)

# Optional checkboxes the operator has pre-declined — leave unchecked.
_OPTIONAL_CHECKBOX_PATTERNS: list[tuple[re.Pattern[str], bool]] = [
    (re.compile(r"preferred\s+name", re.IGNORECASE), False),
]

_SMS_OPT_IN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"phone-sms-opt-in", re.IGNORECASE),
    re.compile(r"sms\s+opt", re.IGNORECASE),
]

# Workday React multiselects — WorkdayHandler fills via workday_widgets;
# page.fill() from the router corrupts the widget state.
_HANDLER_OWNED_WIDGET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"how\s+did\s+you\s+hear", re.IGNORECASE),
    re.compile(r"country\s+phone\s+code", re.IGNORECASE),
    re.compile(r"selfidentif|disability", re.IGNORECASE),
    re.compile(r"authori[sz]ed\s+to\s+work", re.IGNORECASE),
    re.compile(r"require\s+visa\s+sponsorship", re.IGNORECASE),
]


class AnswerRouter:
    def __init__(
        self,
        *,
        static_answers: StaticAnswers,
        narrative: NarrativeEngine,
        resume_docx_path: Path,
        answer_library: AnswerLibrary | None = None,
    ) -> None:
        self._answers = static_answers
        self._narrative = narrative
        self._resume_docx_path = resume_docx_path
        # Only verified entries drive router behaviour; proposed entries
        # live in the yaml for operator review + promotion.
        self._library = [
            entry
            for entry in (answer_library.answers if answer_library else [])
            if entry.status == "verified"
        ]

    def resolve(self, field: FormField, job: Job) -> ResolvedAnswer:
        label = field.label.lower()

        # 1. File upload — the resume goes here regardless of label.
        if field.kind == "file":
            return ResolvedAnswer("file", str(self._resume_docx_path))

        # 1a. Answer library — verified answers to real screening questions,
        # consulted before the narrative engine so a library hit skips an
        # LLM call entirely.
        library_answer = _match_library(field.label, self._library)
        if library_answer is not None:
            return ResolvedAnswer("library", library_answer)

        # 2. Yes/no radio or select — try the boolean patterns first.
        if field.kind in {"select", "radio"}:
            years_match = _EXPERIENCE_YEARS_PATTERN.search(label)
            if years_match:
                need = int(years_match.group(1))
                has = self._answers.years_of_experience or 0
                option = _match_yes_no_option(field.options, has >= need)
                if option is not None:
                    return ResolvedAnswer("select", option)
            if _SKILL_SCREENING_PATTERN.search(label):
                option = _match_yes_no_option(field.options, True)
                if option is not None:
                    return ResolvedAnswer("select", option)

            for pattern, attr in _YES_NO_PATTERNS:
                if pattern.search(label):
                    if attr == "_us_located":
                        option = _match_yes_no_option(field.options, True)
                        if option is None:
                            return ResolvedAnswer("unhandled")
                        return ResolvedAnswer("select", option)
                    value = getattr(self._answers, attr, None)
                    if value is None:
                        return ResolvedAnswer("unhandled")
                    option = _match_yes_no_option(field.options, value)
                    if option is None:
                        return ResolvedAnswer("unhandled")
                    return ResolvedAnswer("select", option)
            # Consent / acknowledgement radios — surface as single-option
            # widgets on Ashby, iCIMS, and some custom_careers pages. Same
            # semantic as the checkbox consent tier: the operator has
            # pre-approved acknowledgement, so pick the affirmative option.
            if any(pattern.search(label) for pattern in _CONSENT_PATTERNS):
                for phrase in _CONSENT_OPTION_PHRASES:
                    option = _match_option_by_substring(field.options, phrase)
                    if option is not None:
                        return ResolvedAnswer("select", option)
                # Single-option radios (e.g. Ashby ["I agree"]) — take it.
                if len(field.options) == 1:
                    return ResolvedAnswer("select", field.options[0])
                return ResolvedAnswer("unhandled")

            # DEI select / radio — pull a free-text value and match it into
            # an option, falling back to unhandled when the operator hasn't
            # set the field (many people leave DEI blank on purpose).
            for pattern, attr in _DEI_PATTERNS:
                if pattern.search(label):
                    value = getattr(self._answers, attr, None)
                    if not value:
                        decline = _match_decline_option(field.options)
                        if decline is not None:
                            return ResolvedAnswer("select", decline)
                        return ResolvedAnswer("unhandled")
                    option = _match_option_by_substring(field.options, value)
                    if option is None:
                        return ResolvedAnswer("unhandled")
                    return ResolvedAnswer("select", option)

        # 3. Checkbox — consent / agreement boxes the operator has pre-approved.
        if field.kind == "checkbox":
            for pattern, attr in _YES_NO_PATTERNS:
                if pattern.search(label):
                    if attr == "_us_located":
                        return ResolvedAnswer("check", check=True)
                    value = getattr(self._answers, attr, None)
                    if value is None:
                        return ResolvedAnswer("unhandled")
                    return ResolvedAnswer("check", check=bool(value))
            for pattern, check in _OPTIONAL_CHECKBOX_PATTERNS:
                if pattern.search(label):
                    return ResolvedAnswer("check", check=check)
            if any(p.search(label) for p in _CONSENT_PATTERNS):
                return ResolvedAnswer("check", check=True)
            if field.selector and any(
                p.search(field.selector) for p in _SMS_OPT_IN_PATTERNS
            ):
                opt_in = self._answers.workday_sms_opt_in
                if opt_in is None:
                    return ResolvedAnswer("unhandled")
                return ResolvedAnswer("check", check=opt_in)
            if _is_disability_checkbox(field):
                value = self._answers.disability_status
                if value:
                    return ResolvedAnswer(
                        "check", check=_disability_label_matches(label, value)
                    )
                return ResolvedAnswer(
                    "check", check=_is_decline_disability_label(label)
                )
            return ResolvedAnswer("unhandled")

        # 4. Text / textarea — identity patterns win first, then anything
        #    that reads like a screening question goes to the narrative
        #    engine. "Cover letter" and similar known-handler-owned
        #    textareas fall through to unhandled so the handler's explicit
        #    fill is not overwritten.
        if field.kind in {"text", "textarea"}:
            if any(p.search(label) for p in _HANDLER_OWNED_WIDGET_PATTERNS):
                return ResolvedAnswer("unhandled")
            for pattern, attr in _IDENTITY_PATTERNS:
                if pattern.search(label):
                    return _identity_answer(attr, self._answers)

            if _is_handler_owned_textarea(label):
                return ResolvedAnswer("unhandled")

            if _looks_open_ended(label):
                answer = self._narrative.answer(job, field.label)
                return ResolvedAnswer("narrative", answer)

        return ResolvedAnswer("unhandled")


def _identity_answer(attr: str, answers: StaticAnswers) -> ResolvedAnswer:
    if attr == "_first_name":
        return ResolvedAnswer("static", _first_name(answers.full_name))
    if attr == "_last_name":
        return ResolvedAnswer("static", _last_name(answers.full_name))
    value = getattr(answers, attr, None)
    if value is None:
        return ResolvedAnswer("unhandled")
    return ResolvedAnswer("static", str(value))


def _first_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[0] if parts else ""


def _last_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return " ".join(parts[1:]) if len(parts) > 1 else ""


def _match_yes_no_option(options: list[str], truthy: bool) -> str | None:
    """Given [Yes, No] / [yes, no] / [true, false] pick the matching one."""
    positives = {"yes", "true", "1", "y"}
    negatives = {"no", "false", "0", "n"}
    for opt in options:
        lo = opt.strip().lower()
        if truthy:
            if lo in positives or lo.startswith("yes"):
                return opt
        elif lo in negatives or lo.startswith("no"):
            return opt
    return None


_DECLINE_DISABILITY_MARKERS = (
    "do not want to answer",
    "don't wish to answer",
    "do not wish to answer",
    "decline to answer",
    "prefer not to answer",
)


def _is_disability_checkbox(field: FormField) -> bool:
    haystack = f"{field.label} {field.selector}".lower()
    return "disability" in haystack or "disabilitystatus" in haystack.replace("-", "")


def _is_decline_disability_label(label: str) -> bool:
    lo = label.strip().lower()
    return any(marker in lo for marker in _DECLINE_DISABILITY_MARKERS)


def _disability_label_matches(label: str, value: str) -> bool:
    lo_label = label.strip().lower()
    lo_value = value.strip().lower()
    if lo_value in lo_label:
        return True
    if lo_value in {"yes", "true"} and "yes" in lo_label and "disability" in lo_label:
        return True
    if lo_value in {"no", "false"} and lo_label.startswith("no,"):
        return True
    return _is_decline_disability_label(label) and _is_decline_disability_label(value)


def _match_decline_option(options: list[str]) -> str | None:
    """Pick a 'decline / prefer not' option when the operator left DEI blank."""
    if not options:
        return None
    markers = ("decline", "prefer not", "choose not", "do not wish", "not wish")
    for opt in options:
        lo = opt.strip().lower()
        if any(m in lo for m in markers):
            return opt
    return None


def _match_option_by_substring(options: list[str], answer: str) -> str | None:
    lo = answer.lower()
    for opt in options:
        if opt and lo in opt.lower():
            return opt
    return None


def _looks_open_ended(label: str) -> bool:
    """Heuristic: labels that read like screening questions."""
    return any(
        marker in label
        for marker in (
            "why ",
            "tell us",
            "describe",
            "how did you",
            "what interests",
            "why are you",
            "?",
        )
    )


def _is_handler_owned_textarea(label: str) -> bool:
    """Labels the handler fills explicitly (cover letter, resume text)."""
    return any(marker in label for marker in ("cover letter", "letter of introduction"))


def _match_library(label: str, library: list) -> str | None:
    """Case-insensitive lookup: exact question match first, then regex."""
    if not library:
        return None
    lo = label.strip().lower()
    for entry in library:
        if entry.question.strip().lower() == lo:
            return entry.canonical_answer
    for entry in library:
        if entry.question_regex:
            try:
                if re.search(entry.question_regex, label, re.IGNORECASE):
                    return entry.canonical_answer
            except re.error:
                continue
    return None
