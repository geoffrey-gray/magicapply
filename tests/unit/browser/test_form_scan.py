"""Unit tests for scan_form — label extraction and field classification."""

from __future__ import annotations

import pytest

from magicapply.infrastructure.browser.ats.form_scan import FormField, scan_form


class _FakePage:
    def __init__(self, html: str) -> None:
        self._html = html

    def content(self) -> str:
        return self._html

    # unused by scan_form but satisfies the PageDriver Protocol
    def goto(self, url: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def set_input_files(self, selector: str, files: str) -> None: ...


def _scan(html: str) -> list[FormField]:
    return scan_form(_FakePage(f"<html><body>{html}</body></html>"))


class TestLabels:
    def test_label_for_id_wins(self) -> None:
        fields = _scan(
            """
            <form>
              <label for="fn">First name</label>
              <input id="fn" name="first_name" type="text">
            </form>
            """
        )
        assert fields[0].label == "First name"
        assert fields[0].selector == "#fn"
        assert fields[0].variant == "text"

    def test_wrapping_label(self) -> None:
        fields = _scan(
            """
            <form>
              <label>Email <input name="email" type="email"></label>
            </form>
            """
        )
        assert "Email" in fields[0].label

    def test_aria_label_fallback(self) -> None:
        fields = _scan(
            """
            <form>
              <input name="phone" aria-label="Phone number">
            </form>
            """
        )
        assert fields[0].label == "Phone number"

    def test_name_fallback(self) -> None:
        fields = _scan(
            """
            <form>
              <input name="linkedin_url">
            </form>
            """
        )
        assert fields[0].label == "linkedin url"


class TestKindClassification:
    def test_text_input(self) -> None:
        [f] = _scan("<form><input name='x' type='text'></form>")
        assert f.kind == "text"

    def test_email_and_tel_are_text(self) -> None:
        fields = _scan(
            "<form>"
            "<input name='e' type='email'>"
            "<input name='p' type='tel'>"
            "</form>"
        )
        assert all(f.kind == "text" for f in fields)

    def test_textarea(self) -> None:
        [f] = _scan("<form><textarea name='cover'></textarea></form>")
        assert f.kind == "textarea"

    def test_select_with_options(self) -> None:
        [f] = _scan(
            """
            <form>
              <select name="auth">
                <option value="">--</option>
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </select>
            </form>
            """
        )
        assert f.kind == "select"
        assert f.options == ["", "yes", "no"]

    def test_checkbox(self) -> None:
        [f] = _scan("<form><input name='newsletter' type='checkbox'></form>")
        assert f.kind == "checkbox"

    def test_radio_group_collapses_to_one_field(self) -> None:
        fields = _scan(
            """
            <form>
              <input name="gender" type="radio" value="f">
              <input name="gender" type="radio" value="m">
              <input name="gender" type="radio" value="nb">
            </form>
            """
        )
        assert len(fields) == 1
        assert fields[0].kind == "radio"
        assert fields[0].options == ["f", "m", "nb"]

    def test_radio_group_uses_fieldset_legend_not_option_label(self) -> None:
        fields = _scan(
            """
            <form>
              <fieldset>
                <legend>Have you previously worked for Acme?</legend>
                <label><input id="yes" name="prev" type="radio" value="true"> Yes</label>
                <label><input id="no" name="prev" type="radio" value="false"> No</label>
              </fieldset>
            </form>
            """
        )
        assert len(fields) == 1
        assert fields[0].label == "Have you previously worked for Acme?"
        assert fields[0].options == ["true", "false"]

    def test_file_input(self) -> None:
        [f] = _scan("<form><input name='resume' type='file'></form>")
        assert f.kind == "file"


class TestSkips:
    def test_hidden_and_submit_are_skipped(self) -> None:
        fields = _scan(
            """
            <form>
              <input name='csrf' type='hidden' value='abc'>
              <input name='x' type='text'>
              <input type='submit' value='Send'>
            </form>
            """
        )
        assert [f.name for f in fields] == ["x"]


class TestRequired:
    def test_required_attribute_detected(self) -> None:
        [f] = _scan("<form><input name='e' required></form>")
        assert f.required is True

    def test_no_required_by_default(self) -> None:
        [f] = _scan("<form><input name='e'></form>")
        assert f.required is False


class TestFormSelector:
    def test_scoped_to_form_by_id(self) -> None:
        # Two forms on the page; scanner returns only the one asked for.
        html = """
        <form id="a"><input name='a1'></form>
        <form id="b"><input name='b1'></form>
        """
        page = _FakePage(f"<html><body>{html}</body></html>")
        fields = scan_form(page, form_selector="form#b")
        assert [f.name for f in fields] == ["b1"]

    def test_missing_form_yields_empty(self) -> None:
        page = _FakePage("<html><body></body></html>")
        assert scan_form(page, "form") == []

    def test_scoped_to_form_by_class(self) -> None:
        html = """
        <form id="noise"><input name="noise" type="text"></form>
        <form class="posting-form"><input name="target" id="target" type="text"></form>
        """
        page = _FakePage(f"<html><body>{html}</body></html>")
        fields = scan_form(page, form_selector="form.posting-form")
        assert [f.name for f in fields] == ["target"]

    def test_scoped_to_data_automation_id_root(self) -> None:
        html = """
        <div data-automation-id="applyFlowPage">
          <input name="inside" type="text">
        </div>
        <form><input name="outside" type="text"></form>
        """
        page = _FakePage(f"<html><body>{html}</body></html>")
        fields = scan_form(
            page,
            form_selector="[data-automation-id='applyFlowPage']",
        )
        assert [f.name for f in fields] == ["inside"]


class TestSelector:
    def test_data_testid_bracket_selector(self) -> None:
        html = """
        <div data-testid="application-form">
          <input name="custom_q" type="text">
        </div>
        """
        page = _FakePage(f"<html><body>{html}</body></html>")
        fields = scan_form(page, form_selector="[data-testid='application-form']")
        assert [f.name for f in fields] == ["custom_q"]

    def test_prefers_id_over_name(self) -> None:
        [f] = _scan("<form><input id='email' name='e' type='email'></form>")
        assert f.selector == "#email"

    def test_falls_back_to_name(self) -> None:
        [f] = _scan("<form><input name='e' type='email'></form>")
        assert f.selector == "input[name='e']"


class TestAshbyLabels:
    def test_fieldset_prompt_without_legend(self) -> None:
        fields = _scan(
            """
            <form>
              <fieldset class="_container_1258i_28">
                Have you previously worked in a remote or hybrid environment?
                <label><input type="radio" name="q1" id="r0">Yes, fully remote</label>
                <label><input type="radio" name="q1" id="r1">No</label>
              </fieldset>
            </form>
            """
        )
        assert len(fields) == 1
        assert "remote or hybrid" in fields[0].label.lower()
        assert fields[0].options == ["Yes, fully remote", "No"]

    def test_uuid_checkbox_uses_parent_prompt(self) -> None:
        fields = _scan(
            """
            <form>
              <div class="_fieldEntry_1e3gg_28">
                Are you legally authorized to work in your current country of employment?
                <input type="checkbox" name="f1787b93-cd38-40ed-a0ff-357056af73f2">
              </div>
            </form>
            """
        )
        assert len(fields) == 1
        assert "legally authorized" in fields[0].label.lower()


class TestLeverLabels:
    def test_custom_question_radio_uses_prompt_not_option(self) -> None:
        fields = _scan(
            """
            <form>
              <li class="application-question custom-question">
                <div class="application-label">
                  Are you eligible to work in the US without Sponsorship?
                </div>
                <ul>
                  <li><label><input type="radio" name="cards[x][field0]"
                    value="Yes (Citizen/Green-card)">Yes (Citizen/Green-card)</label></li>
                  <li><label><input type="radio" name="cards[x][field0]"
                    value="No (H1-B)">No (H1-B)</label></li>
                </ul>
              </li>
            </form>
            """
        )
        assert len(fields) == 1
        assert "eligible to work" in fields[0].label.lower()


class TestWorkdayVariants:
    def test_signature_date_spinbuttons_get_workday_variant(self) -> None:
        fields = _scan(
            """
            <form>
              <input id="selfIdentifiedDisabilityData--dateSignedOn-dateSectionMonth-input" type="text">
              <input id="selfIdentifiedDisabilityData--dateSignedOn-dateSectionDay-input" type="text">
              <input id="selfIdentifiedDisabilityData--dateSignedOn-dateSectionYear-input" type="text">
            </form>
            """
        )
        assert len(fields) == 3
        assert all(f.variant == "workday_date_spin" for f in fields)
        assert fields[0].recipe_value
        assert fields[0].step_id == "self_identify"


class TestScanNoise:
    def test_bare_input_without_id_or_name_is_dropped(self) -> None:
        fields = _scan(
            """
            <form>
              <label for="fn">First name</label>
              <input id="fn" name="first_name" type="text" required>
              <input type="text" required>
            </form>
            """
        )
        assert len(fields) == 1
        assert fields[0].selector == "#fn"

    def test_search_widget_input_is_dropped(self) -> None:
        fields = _scan(
            """
            <form>
              <input id="iti-0__search-input" type="text">
            </form>
            """
        )
        assert fields == []
