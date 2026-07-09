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
from magicapply.config.router_rules import RouterRules
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


# All label/selector regex tables live in
# `src/magicapply/infrastructure/browser/ats/resources/router_rules.yaml`, loaded
# via `RouterRules.load_default()`. The router receives the loaded rules by
# injection (see `AnswerRouter.__init__`) — operator overrides at
# `<config_root>/router_rules.yaml` replace the package default. See
# `docs/ARCHITECTURE.md` §9 and `docs/ARCHITECTURE_COMPOSABLE_FORMS.md` §2.6.


class AnswerRouter:
    def __init__(
        self,
        *,
        static_answers: StaticAnswers,
        narrative: NarrativeEngine,
        resume_docx_path: Path,
        answer_library: AnswerLibrary | None = None,
        router_rules: RouterRules | None = None,
    ) -> None:
        self._answers = static_answers
        self._narrative = narrative
        self._resume_docx_path = resume_docx_path
        # Config-driven regex tables live in
        # `src/magicapply/infrastructure/browser/ats/resources/router_rules.yaml`
        # (package default) or `<config_root>/router_rules.yaml` (operator
        # override). Missing argument → package default. See
        # `config/router_rules.py`.
        self._rules = router_rules or RouterRules.load_default()
        # Only verified entries drive router behaviour; proposed entries
        # live in the yaml for operator review + promotion.
        self._library = [
            entry
            for entry in (answer_library.answers if answer_library else [])
            if entry.status == "verified"
        ]

    def resolve(self, field: FormField, job: Job) -> ResolvedAnswer:
        label = field.label.lower()

        # 0. Variant-based handler ownership — Workday listboxes,
        # multiselects, and date-spin groups are filled by
        # `workday_widgets` / recipe execute paths; `page.fill()` from the
        # router corrupts the React state. Skip via explicit field typing
        # (config-driven — see `router_rules.yaml::handler_owned_variants`)
        # so scan-derived widgets get skipped even when their label isn't
        # in the `handler_owned_widget` regex list. See
        # ARCHITECTURE_COMPOSABLE_FORMS.md §4.4.
        if field.variant in self._rules.handler_owned_variants:
            return ResolvedAnswer("unhandled")

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
            years_match = self._rules.experience_years.pattern.search(label)
            if years_match:
                need = int(years_match.group(1))
                has = self._answers.years_of_experience or 0
                option = _match_yes_no_option(
                    field.options, has >= need, attr="experience_years"
                )
                if option is not None:
                    return ResolvedAnswer("select", option)
            if self._rules.skill_screening.pattern.search(label):
                option = _match_yes_no_option(
                    field.options, True, attr="skill_screening"
                )
                if option is not None:
                    return ResolvedAnswer("select", option)

            for rule in self._rules.yes_no:
                if rule.pattern.search(label):
                    attr = rule.attr
                    if attr == "_us_located":
                        option = _match_yes_no_option(
                            field.options, True, attr=attr
                        )
                        if option is None:
                            return ResolvedAnswer("unhandled")
                        return ResolvedAnswer("select", option)
                    value = getattr(self._answers, attr, None)
                    if value is None:
                        return ResolvedAnswer("unhandled")
                    option = _match_yes_no_option(
                        field.options, bool(value), attr=attr
                    )
                    if option is None:
                        return ResolvedAnswer("unhandled")
                    return ResolvedAnswer("select", option)
            # Consent / acknowledgement radios — surface as single-option
            # widgets on Ashby, iCIMS, and some custom_careers pages. Same
            # semantic as the checkbox consent tier: the operator has
            # pre-approved acknowledgement, so pick the affirmative option.
            if any(rule.pattern.search(label) for rule in self._rules.consent.patterns):
                for phrase in self._rules.consent.option_phrases:
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
            for rule in self._rules.dei:
                if rule.pattern.search(label):
                    value = getattr(self._answers, rule.attr, None)
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
            for rule in self._rules.yes_no:
                if rule.pattern.search(label):
                    attr = rule.attr
                    if attr == "_us_located":
                        return ResolvedAnswer("check", check=True)
                    value = getattr(self._answers, attr, None)
                    if value is None:
                        return ResolvedAnswer("unhandled")
                    return ResolvedAnswer("check", check=bool(value))
            for rule in self._rules.optional_checkbox:
                if rule.pattern.search(label):
                    return ResolvedAnswer("check", check=bool(rule.check))
            if any(rule.pattern.search(label) for rule in self._rules.consent.patterns):
                return ResolvedAnswer("check", check=True)
            if field.selector and any(
                rule.pattern.search(field.selector) for rule in self._rules.sms_opt_in
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

        # 4. Text / textarea.
        # Order: Workday-owned labels → identity → yes/no → DEI → narrative.
        # Handler-owned runs before identity so "Country Phone Code" does not
        # match the generic country/phone identity rules (Workday multiselect).
        # Greenhouse "How did you hear" is identity (not handler_owned); Workday
        # source widgets skip via workday_multiselect variant at step 0.
        if field.kind in {"text", "textarea"}:
            if any(rule.pattern.search(label) for rule in self._rules.handler_owned_widget):
                return ResolvedAnswer("unhandled")

            for rule in self._rules.identity:
                if rule.pattern.search(label):
                    return _identity_answer(rule.attr, self._answers)

            for rule in self._rules.yes_no:
                if rule.pattern.search(label):
                    attr = rule.attr
                    if attr == "_us_located":
                        return ResolvedAnswer("static", "Yes")
                    value = getattr(self._answers, attr, None)
                    if value is None:
                        return ResolvedAnswer("unhandled")
                    return ResolvedAnswer("static", "Yes" if value else "No")

            for rule in self._rules.dei:
                if rule.pattern.search(label):
                    return _dei_text_answer(rule.attr, self._answers)

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


_DEI_DECLINE = "Decline to state"


def _dei_text_answer(attr: str, answers: StaticAnswers) -> ResolvedAnswer:
    """Free-text DEI screens (Greenhouse custom questions mis-typed as text).

    Operator-null DEI attrs mean "decline" — never invent a demographic value
    and never hand the mock narrative a Yes/No-shaped question.
    """
    value = getattr(answers, attr, None)
    if value is None or (isinstance(value, str) and not value.strip()):
        return ResolvedAnswer("static", _DEI_DECLINE)
    return ResolvedAnswer("static", str(value))


def _first_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[0] if parts else ""


def _last_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return " ".join(parts[1:]) if len(parts) > 1 else ""


def _match_yes_no_option(
    options: list[str],
    truthy: bool,
    *,
    attr: str | None = None,
) -> str | None:
    """Pick a radio/select option for a boolean StaticAnswers attr.

    Handles short Yes/No widgets and longer ATS phrases such as
    "I am authorized to work in the US without sponsorship" /
    "I need H-1B sponsorship". ``attr`` disambiguates sponsorship vs
    work-auth so the same option list maps correctly for both.
    """
    positives = {"yes", "true", "1", "y"}
    negatives = {"no", "false", "0", "n"}
    for opt in options:
        lo = opt.strip().lower()
        if truthy:
            if lo in positives or lo.startswith("yes"):
                return opt
        elif lo in negatives or lo.startswith("no"):
            return opt

    without_sponsorship: list[str] = []
    needs_sponsorship: list[str] = []
    authorized: list[str] = []
    for opt in options:
        lo = opt.strip().lower()
        if not lo:
            continue
        if "without sponsorship" in lo or "no sponsorship" in lo:
            without_sponsorship.append(opt)
        elif re.search(
            r"\bneed(?:s)?\b.*sponsorship|sponsorship required|"
            r"require(?:s)? sponsorship|h-?1b|visa sponsorship",
            lo,
        ):
            needs_sponsorship.append(opt)
        elif (
            "authorized to work" in lo
            or "legally authorized" in lo
            or "citizen" in lo
            or "green card" in lo
            or "green-card" in lo
        ):
            authorized.append(opt)

    if attr == "needs_sponsorship_us":
        # True → must pick a "I need sponsorship" option; False → without.
        if truthy:
            return needs_sponsorship[0] if needs_sponsorship else None
        if without_sponsorship:
            return without_sponsorship[0]
        if authorized:
            return authorized[0]
        return None

    # authorized_to_work_us / experience / skill / generic:
    # True → without-sponsorship or authorized/citizen phrasing.
    if truthy:
        if without_sponsorship:
            return without_sponsorship[0]
        if authorized:
            return authorized[0]
        return None

    # truthy=False for authorization → needs-sponsorship / not authorized.
    if needs_sponsorship:
        return needs_sponsorship[0]
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
    """Heuristic: labels that read like screening questions.

    Deliberately omits "how did you" — source questions resolve via
    ``how_did_you_hear`` identity. A bare ``?`` still catches open screens.
    """
    return any(
        marker in label
        for marker in (
            "why ",
            "tell us",
            "describe",
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
