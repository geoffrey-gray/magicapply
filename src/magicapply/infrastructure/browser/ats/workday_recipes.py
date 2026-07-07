"""Workday widget-step recipes for ``FormComposer.fill_recipe`` (CF.3)."""

from __future__ import annotations

from datetime import date

from magicapply.config.models import StaticAnswers
from magicapply.infrastructure.browser.forms.fields import FormField
from magicapply.infrastructure.browser.forms.schema import FormSchema

VOLUNTARY_DISCLOSURES_PAGE = "[data-automation-id='applyFlowVoluntaryDisclosuresPage']"
SELF_IDENTIFY_NAME = "#selfIdentifiedDisabilityData--name"

DEI_DECLINE_LABELS: tuple[str, ...] = (
    "I don't wish to answer",
    "I do not wish to answer",
    "Decline to answer",
    "Prefer not to answer",
)

DISABILITY_DECLINE_LABEL = "I do not want to answer"

_DATE_SPIN_PREFIX = "#selfIdentifiedDisabilityData--dateSignedOn-dateSection"


def voluntary_disclosures_schema(answers: StaticAnswers) -> FormSchema:
    """DEI listboxes on the Voluntary Disclosures wizard step."""
    fields = [
        _dei_listbox("Ethnicity", "personalInfoUS--ethnicity", answers.ethnicity),
        _dei_listbox("Gender", "personalInfoUS--gender", answers.gender),
        FormField(
            selector="#personalInfoUS--veteranStatus",
            label="Veteran status",
            kind="select",
            variant="workday_listbox",
            widget_id="personalInfoUS--veteranStatus",
            step_id="voluntary_disclosures",
            recipe_labels=_value_or_decline(answers.veteran_status, ()),
        ),
    ]
    return FormSchema(
        schema_id="workday_voluntary_disclosures",
        ats="workday",
        step_id="voluntary_disclosures",
        fields=fields,
        form_selector=VOLUNTARY_DISCLOSURES_PAGE,
        source="recipe",
    )


def self_identify_schema(
    answers: StaticAnswers,
    *,
    today: date | None = None,
) -> FormSchema:
    """Disability self-identification step (name, signature date, status)."""
    signed = today or date.today()
    decline = answers.disability_status or DISABILITY_DECLINE_LABEL
    fields = [
        FormField(
            selector=SELF_IDENTIFY_NAME,
            label="Name",
            kind="text",
            variant="text",
            step_id="self_identify",
        ),
        _date_spin("Signature date month", "Month", signed.month),
        _date_spin("Signature date day", "Day", signed.day),
        _date_spin("Signature date year", "Year", signed.year),
        FormField(
            selector="",
            label="Disability status",
            kind="radio",
            variant="radio",
            step_id="self_identify",
            group_id="disability_status",
            recipe_labels=(decline, DISABILITY_DECLINE_LABEL),
        ),
    ]
    return FormSchema(
        schema_id="workday_self_identify",
        ats="workday",
        step_id="self_identify",
        fields=fields,
        form_selector=SELF_IDENTIFY_NAME,
        source="recipe",
    )


def _dei_listbox(label: str, widget_id: str, value: str | None) -> FormField:
    return FormField(
        selector=f"#{widget_id}",
        label=label,
        kind="select",
        variant="workday_listbox",
        widget_id=widget_id,
        step_id="voluntary_disclosures",
        recipe_labels=_value_or_decline(value, DEI_DECLINE_LABELS),
    )


def _date_spin(label: str, part: str, value: int) -> FormField:
    selector = f"{_DATE_SPIN_PREFIX}{part}-input"
    return FormField(
        selector=selector,
        label=label,
        kind="text",
        variant="workday_date_spin",
        step_id="self_identify",
        group_id="selfIdentifiedDisabilityData--dateSignedOn",
        recipe_value=str(value),
    )


def _value_or_decline(
    value: str | None,
    decline_labels: tuple[str, ...],
) -> tuple[str, ...]:
    if value:
        return (value,)
    return decline_labels