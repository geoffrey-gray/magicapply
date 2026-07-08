"""Unit tests for AnswerRouter."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.infrastructure.browser.ats.answer_router import (
    AnswerRouter,
    ResolvedAnswer,
)
from magicapply.infrastructure.browser.ats.form_scan import FormField


class _RecordingNarrative:
    """NarrativeEngine stand-in that records every answer(job, question) call."""

    def __init__(self, reply: str = "canned narrative answer") -> None:
        self._reply = reply
        self.calls: list[tuple[Job, str]] = []

    def answer(self, job: Job, question: str) -> str:
        self.calls.append((job, question))
        return self._reply

    # NarrativeEngine also exposes cover_letter but the router never calls it.


def _router(
    static: StaticAnswers | None = None,
    narrative: _RecordingNarrative | None = None,
    resume_docx_path: Path = Path("/tmp/resume.docx"),
) -> AnswerRouter:
    return AnswerRouter(
        static_answers=static or _default_static(),
        narrative=narrative or _RecordingNarrative(),
        resume_docx_path=resume_docx_path,
    )


def _default_static() -> StaticAnswers:
    return StaticAnswers(
        full_name="Jane Doe",
        email="jane@example.com",
        phone="555-0100",
        linkedin_url="https://linkedin.com/in/jane",
        location="Boston, MA",
        authorized_to_work_us=True,
        needs_sponsorship_us=False,
        hispanic_latino=False,
        years_of_experience=8,
        desired_salary="$180k-$210k",
    )


def _job() -> Job:
    return Job.new(source_name="s", url="https://example.com/j", title="Eng", company="Acme")


class TestFile:
    def test_file_field_routes_to_resume_path(self) -> None:
        r = _router(resume_docx_path=Path("/tmp/my-resume.docx"))
        field = FormField(selector="input", label="Resume", kind="file")
        assert r.resolve(field, _job()) == ResolvedAnswer("file", "/tmp/my-resume.docx")


class TestIdentityText:
    def test_first_name(self) -> None:
        field = FormField(selector="#first_name", label="First Name", kind="text")
        assert _router().resolve(field, _job()) == ResolvedAnswer("static", "Jane")

    def test_last_name(self) -> None:
        field = FormField(selector="#last_name", label="Last Name", kind="text")
        assert _router().resolve(field, _job()) == ResolvedAnswer("static", "Doe")

    def test_email(self) -> None:
        field = FormField(selector="#email", label="Email Address", kind="text")
        assert _router().resolve(field, _job()) == ResolvedAnswer("static", "jane@example.com")

    def test_phone(self) -> None:
        field = FormField(selector="#phone", label="Phone Number", kind="text")
        assert _router().resolve(field, _job()) == ResolvedAnswer("static", "555-0100")

    def test_phone_bare_label(self) -> None:
        field = FormField(selector="#12d057e6", label="Phone", kind="text")
        assert _router().resolve(field, _job()) == ResolvedAnswer("static", "555-0100")

    def test_linkedin(self) -> None:
        field = FormField(
            selector="input[name='linkedin_url']", label="LinkedIn URL", kind="text"
        )
        assert _router().resolve(field, _job()).value.startswith("https://linkedin.com")

    def test_years_of_experience(self) -> None:
        field = FormField(selector="#yoe", label="Years of Experience", kind="text")
        assert _router().resolve(field, _job()) == ResolvedAnswer("static", "8")

    def test_country(self) -> None:
        answers = _default_static().model_copy(update={"country": "United States"})
        field = FormField(selector="#country", label="Country*", kind="text")
        assert _router(static=answers).resolve(field, _job()) == ResolvedAnswer(
            "static", "United States"
        )

    def test_current_employer(self) -> None:
        answers = _default_static().model_copy(update={"current_employer": "Intrinsic"})
        field = FormField(
            selector="#q",
            label="Please provide the name of your current (or most recent) company*",
            kind="text",
        )
        assert _router(static=answers).resolve(field, _job()) == ResolvedAnswer(
            "static", "Intrinsic"
        )

    def test_most_recent_employer_label(self) -> None:
        answers = _default_static().model_copy(update={"current_employer": "Intrinsic"})
        field = FormField(
            selector="#75184292",
            label="Please list your most recent employer",
            kind="text",
        )
        assert _router(static=answers).resolve(field, _job()) == ResolvedAnswer(
            "static", "Intrinsic"
        )

    def test_ashby_authorized_checkbox(self) -> None:
        field = FormField(
            selector="input[name='f1787b93']",
            label="Are you legally authorized to work in your current country of employment?",
            kind="checkbox",
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("check", check=True)


class TestWorkdayFields:
    def test_address_and_postal(self) -> None:
        answers = _default_static().model_copy(
            update={"address_line_1": "123 Main St", "postal_code": "33572"}
        )
        r = _router(static=answers)
        addr = FormField(selector="#address--addressLine1", label="Address Line 1*", kind="text")
        postal = FormField(selector="#address--postalCode", label="Postal Code*", kind="text")
        assert r.resolve(addr, _job()) == ResolvedAnswer("static", "123 Main St")
        assert r.resolve(postal, _job()) == ResolvedAnswer("static", "33572")

    def test_previously_employed_radio_true_false_options(self) -> None:
        answers = _default_static().model_copy(update={"previously_employed": False})
        field = FormField(
            selector="#yes",
            label="Have you previously worked for Pluralsight?",
            kind="radio",
            name="candidateIsPreviousWorker",
            options=["true", "false"],
        )
        assert _router(static=answers).resolve(field, _job()) == ResolvedAnswer(
            "select", "false"
        )

    def test_previously_employed_circle_wording(self) -> None:
        answers = _default_static().model_copy(update={"previously_employed": False})
        field = FormField(
            selector="#no",
            label="Have you previously been employed by Circle in any capacity?",
            kind="radio",
            name="prev",
            options=["true", "false"],
        )
        assert _router(static=answers).resolve(field, _job()) == ResolvedAnswer(
            "select", "false"
        )

    def test_preferred_name_checkbox_left_unchecked(self) -> None:
        field = FormField(
            selector="#name--preferredCheck",
            label="I have a preferred name",
            kind="checkbox",
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("check", check=False)

    def test_country_phone_code_is_handler_owned_not_country_field(self) -> None:
        answers = _default_static().model_copy(
            update={
                "country": "United States",
                "country_phone_code": "United States of America (+1)",
            }
        )
        field = FormField(
            selector="#phoneNumber--countryPhoneCode",
            label="Country Phone Code*",
            kind="text",
        )
        assert _router(static=answers).resolve(field, _job()).strategy == "unhandled"

    def test_sms_opt_in_by_selector(self) -> None:
        answers = _default_static().model_copy(update={"workday_sms_opt_in": True})
        field = FormField(selector="#phone-sms-opt-in", label="", kind="checkbox")
        assert _router(static=answers).resolve(field, _job()) == ResolvedAnswer(
            "check", check=True
        )

    def test_how_did_you_hear_is_handler_owned_not_narrative(self) -> None:
        narrative = _RecordingNarrative()
        field = FormField(
            selector="#source--source",
            label="How Did You Hear About Us?*",
            kind="text",
        )
        result = _router(narrative=narrative).resolve(field, _job())
        assert result.strategy == "unhandled"
        assert narrative.calls == []


class TestYesNoSelect:
    def test_authorized_yes(self) -> None:
        field = FormField(
            selector="#auth",
            label="Are you authorized to work in the US?",
            kind="select",
            options=["", "Yes", "No"],
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("select", "Yes")

    def test_needs_sponsorship_no(self) -> None:
        field = FormField(
            selector="#sp",
            label="Will you require sponsorship?",
            kind="select",
            options=["Yes", "No"],
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("select", "No")

    def test_hispanic_no(self) -> None:
        field = FormField(
            selector="#h",
            label="Are you Hispanic or Latino?",
            kind="select",
            options=["Yes", "No", "Decline to state"],
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("select", "No")

    def test_lever_citizen_green_card_option(self) -> None:
        field = FormField(
            selector="#citizen",
            label="Are you eligible to work in the US without Sponsorship?",
            kind="radio",
            options=[
                "Yes (Citizen/Green-card)",
                "No (H1-B or other Visa sponsorship required)",
            ],
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer(
            "select", "Yes (Citizen/Green-card)"
        )

    def test_unset_bool_is_unhandled(self) -> None:
        answers = _default_static().model_copy(update={"authorized_to_work_us": None})
        field = FormField(
            selector="#auth",
            label="Are you authorized to work in the US?",
            kind="select",
            options=["Yes", "No"],
        )
        assert _router(static=answers).resolve(field, _job()).strategy == "unhandled"

    def test_no_matching_option_is_unhandled(self) -> None:
        # Bank has bool but options are exotic — router bails rather than guess.
        field = FormField(
            selector="#auth",
            label="Are you authorized to work in the US?",
            kind="select",
            options=["Citizen", "Green card", "H1B"],
        )
        assert _router().resolve(field, _job()).strategy == "unhandled"


class TestNarrative:
    def test_textarea_dispatched_to_narrative(self) -> None:
        narrative = _RecordingNarrative(reply="Because I love it")
        field = FormField(
            selector="textarea[name='why']", label="Why Acme?", kind="textarea"
        )
        result = _router(narrative=narrative).resolve(field, _job())
        assert result == ResolvedAnswer("narrative", "Because I love it")
        assert narrative.calls[0][1] == "Why Acme?"  # question preserved

    def test_question_mark_text_input_is_narrative(self) -> None:
        # A text input that reads like a question falls through to narrative.
        narrative = _RecordingNarrative(reply="Their platform mission resonates with me.")
        field = FormField(
            selector="#why-here", label="Why do you want to work here?", kind="text"
        )
        result = _router(narrative=narrative).resolve(field, _job())
        assert result.strategy == "narrative"


class TestUnhandled:
    def test_opaque_text_input_is_unhandled(self) -> None:
        field = FormField(
            selector="input[name='favourite_colour']",
            label="Favourite colour",
            kind="text",
        )
        assert _router().resolve(field, _job()).strategy == "unhandled"

    def test_checkbox_is_unhandled(self) -> None:
        field = FormField(
            selector="input[name='newsletter']", label="Newsletter", kind="checkbox"
        )
        assert _router().resolve(field, _job()).strategy == "unhandled"

    def test_disability_decline_checkbox_is_checked(self) -> None:
        field = FormField(
            selector="#abc-disabilityStatus",
            label="I do not want to answer",
            kind="checkbox",
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("check", check=True)

    def test_disability_yes_checkbox_left_unchecked_when_declining(self) -> None:
        field = FormField(
            selector="#abc-disabilityStatus",
            label="Yes, I have a disability, or have had one in the past",
            kind="checkbox",
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("check", check=False)

    def test_consent_checkbox_is_checked(self) -> None:
        field = FormField(
            selector="#gdpr",
            label="By checking this box, I consent to demographic data surveys.*",
            kind="checkbox",
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("check", check=True)

    def test_terms_of_use_checkbox_is_checked(self) -> None:
        field = FormField(
            selector="#terms",
            label="I understand and acknowledge the terms of use for Circle.",
            kind="checkbox",
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("check", check=True)

    def test_consent_radio_single_option_agree_is_selected(self) -> None:
        """Ashby / iCIMS surface acknowledgement widgets as radios with the
        only option being 'I agree'. The consent patterns fire for radios
        too — the router picks the affirmative option instead of falling
        through to unhandled."""
        field = FormField(
            selector="#ack",
            label="I agree",
            kind="radio",
            options=["I agree"],
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("select", "I agree")

    def test_consent_radio_picks_agree_from_multi_option(self) -> None:
        field = FormField(
            selector="#expectations",
            label="I have read and understand the expectations of working at TRM.",
            kind="radio",
            options=["Agree", "Disagree"],
        )
        assert _router().resolve(field, _job()) == ResolvedAnswer("select", "Agree")

    def test_consent_radio_falls_through_when_no_affirmative_option(self) -> None:
        field = FormField(
            selector="#weird",
            label="I acknowledge",
            kind="radio",
            options=["Option A", "Option B"],
        )
        assert _router().resolve(field, _job()).strategy == "unhandled"

    def test_workday_variant_skipped_even_with_matchable_label(self) -> None:
        """Variant-based skip fires before any pattern lookup. A scan-derived
        Workday listbox with a label the identity/yes-no tier would happily
        match must still be left alone — the handler recipe owns it and
        `page.fill()` would corrupt the widget state."""
        field = FormField(
            selector="#authorizedToWork",
            label="Email",  # would normally hit _IDENTITY_PATTERNS
            kind="select",
            variant="workday_listbox",
        )
        assert _router().resolve(field, _job()).strategy == "unhandled"

    def test_workday_multiselect_variant_skipped(self) -> None:
        field = FormField(
            selector="#ethnicity",
            label="Ethnicity",  # would normally hit _DEI_PATTERNS
            kind="select",
            variant="workday_multiselect",
            options=["Asian", "White"],
        )
        assert _router().resolve(field, _job()).strategy == "unhandled"

    def test_non_workday_variant_still_resolves_normally(self) -> None:
        """Sanity — the variant-skip must not bleed into ordinary text/select
        fields."""
        field = FormField(
            selector="#email",
            label="Email",
            kind="text",
            variant="text",
        )
        result = _router().resolve(field, _job())
        assert result.strategy == "static"
        assert "@" in result.value

    def test_workday_password_fields_use_static_config(self) -> None:
        answers = _default_static().model_copy(
            update={"workday_apply_password": "test-pass-123"}
        )
        for label in ("Password*", "Verify New Password*"):
            field = FormField(selector="#pwd", label=label, kind="text")
            assert _router(static=answers).resolve(field, _job()) == ResolvedAnswer(
                "static", "test-pass-123"
            )

    def test_dei_select_decline_when_unset(self) -> None:
        answers = _default_static().model_copy(update={"ethnicity": None})
        field = FormField(
            selector="#eth",
            label="Ethnicity",
            kind="select",
            options=["Asian", "Decline to state"],
        )
        assert _router(static=answers).resolve(field, _job()) == ResolvedAnswer(
            "select", "Decline to state"
        )

    def test_cover_letter_textarea_is_unhandled(self) -> None:
        # The Greenhouse handler fills the cover letter textarea explicitly;
        # the router must not overwrite it via a narrative call.
        narrative = _RecordingNarrative()
        field = FormField(
            selector="textarea[name='cover_letter_text']",
            label="Cover letter",
            kind="textarea",
        )
        result = _router(narrative=narrative).resolve(field, _job())
        assert result.strategy == "unhandled"
        assert narrative.calls == []  # narrative engine not called

    def test_plain_textarea_without_question_marker_is_unhandled(self) -> None:
        # A textarea that does not read like a screening question stays
        # unhandled so we do not spend tokens on it by accident.
        narrative = _RecordingNarrative()
        field = FormField(
            selector="textarea[name='comments']", label="Comments", kind="textarea"
        )
        result = _router(narrative=narrative).resolve(field, _job())
        assert result.strategy == "unhandled"
        assert narrative.calls == []
