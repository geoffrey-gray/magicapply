"""Route a scanned FormField to a concrete fill strategy.

The router is deliberately dumb: it inspects the field's label + kind and
picks one of six strategies. Handler code turns each ``ResolvedAnswer`` into
a page call. Anything the router does not recognise is returned as
``strategy="unhandled"`` — the caller logs it and moves on; a real
submission may still succeed if the field is optional.

Split into three intents:
- **static**: identity, contact, DEI, work-auth — value comes straight from
  ``StaticAnswers``.
- **narrative**: open-ended text (why do you want to work here, screening
  questions) — value comes from ``NarrativeEngine.answer(job, label)``.
- **file**: resume upload — value is the tailored DOCX path already on
  ``ApplicationData``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.domain.resumes.narrative import NarrativeEngine
from magicapply.infrastructure.browser.ats.form_scan import FormField

Strategy = Literal["static", "select", "check", "narrative", "file", "unhandled"]


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
    (re.compile(r"\bphone\b|\btelephone\b|\bmobile\b"), "phone"),
    (re.compile(r"\blinkedin\b"), "linkedin_url"),
    (re.compile(r"\bgithub\b"), "github_url"),
    (re.compile(r"\bportfolio\b|\bwebsite\b|\bpersonal\s+site\b"), "portfolio_url"),
    (re.compile(r"\blocation\b|\bcity\b"), "location"),
    (re.compile(r"\byears?\s+of\s+experience\b"), "years_of_experience"),
    (re.compile(r"\bdesired\s+salary\b|\bsalary\s+expectation\b|\bcompensation\b"), "desired_salary"),
    (re.compile(r"\bwork\s+authorization\b"), "work_authorization"),
]

# For yes/no <select> or radio groups, keyed on StaticAnswers bool attributes.
_YES_NO_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"authori[sz]ed\s+to\s+work"), "authorized_to_work_us"),
    (re.compile(r"require\s+sponsorship|need\s+sponsorship|require\s+visa"), "needs_sponsorship_us"),
    (re.compile(r"hispanic|latino|latin[a-z]"), "hispanic_latino"),
]

# DEI / EEO free-text (usually a select with specific options).
_DEI_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgender\b"), "gender"),
    (re.compile(r"\brace\b|\bethnicity\b"), "ethnicity"),
    (re.compile(r"\bveteran\b"), "veteran_status"),
    (re.compile(r"\bdisability\b|\bdisabled\b"), "disability_status"),
]


class AnswerRouter:
    def __init__(
        self,
        *,
        static_answers: StaticAnswers,
        narrative: NarrativeEngine,
        resume_docx_path: Path,
    ) -> None:
        self._answers = static_answers
        self._narrative = narrative
        self._resume_docx_path = resume_docx_path

    def resolve(self, field: FormField, job: Job) -> ResolvedAnswer:
        label = field.label.lower()

        # 1. File upload — the resume goes here regardless of label.
        if field.kind == "file":
            return ResolvedAnswer("file", str(self._resume_docx_path))

        # 2. Yes/no radio or select — try the boolean patterns first.
        if field.kind in {"select", "radio"}:
            for pattern, attr in _YES_NO_PATTERNS:
                if pattern.search(label):
                    value = getattr(self._answers, attr, None)
                    if value is None:
                        return ResolvedAnswer("unhandled")
                    option = _match_yes_no_option(field.options, value)
                    if option is None:
                        return ResolvedAnswer("unhandled")
                    return ResolvedAnswer("select", option)
            # DEI select / radio — pull a free-text value and match it into
            # an option, falling back to unhandled when the operator hasn't
            # set the field (many people leave DEI blank on purpose).
            for pattern, attr in _DEI_PATTERNS:
                if pattern.search(label):
                    value = getattr(self._answers, attr, None)
                    if not value:
                        return ResolvedAnswer("unhandled")
                    option = _match_option_by_substring(field.options, value)
                    if option is None:
                        return ResolvedAnswer("unhandled")
                    return ResolvedAnswer("select", option)

        # 3. Checkbox — no default StaticAnswers routing (opt-in checkboxes
        #    such as "I agree to terms" are best left to per-handler code).
        if field.kind == "checkbox":
            return ResolvedAnswer("unhandled")

        # 4. Text / textarea — identity patterns win first, then anything
        #    long-form goes to the narrative engine.
        if field.kind in {"text", "textarea"}:
            for pattern, attr in _IDENTITY_PATTERNS:
                if pattern.search(label):
                    return _identity_answer(attr, self._answers)

            # Fall through to narrative for open-ended text/textarea.
            if field.kind == "textarea" or _looks_open_ended(label):
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
    want = positives if truthy else negatives
    for opt in options:
        if opt.strip().lower() in want:
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
