"""Unit tests for workday_widgets community interaction helpers."""

from __future__ import annotations

from magicapply.infrastructure.browser.ats.workday_widgets import (
    _PROMPT_OPTION,
    _css_attr,
    click_prompt_option,
    fill_multiselect,
    select_listbox_button,
    select_listbox_first_match,
    select_veteran_status_listbox,
)


class _FakeLocator:
    def __init__(self, page: "_FakePage", selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> _FakeLocator:
        return self

    def nth(self, index: int) -> _FakeLocator:
        self._page._nth = index
        return self

    def filter(self, *, has_text: str) -> _FakeLocator:
        self._page._filter_text = has_text
        return self

    def count(self) -> int:
        if "[role='listbox'] [role='option']" in self._selector:
            return len(self._page._listbox_options)
        return 1

    def click(self, *, timeout: int = 0, force: bool = False) -> None:
        self._page.actions.append(("click", self._selector))

    def is_visible(self, *, timeout: int = 0) -> bool:
        return True

    def get_by_role(self, role: str, name: str, *, exact: bool = False) -> _FakeRoleLocator:
        return _FakeRoleLocator(self._page, role, name)

    def inner_text(self, *, timeout: int = 0) -> str:
        if "#personalInfoUS--veteranStatus" in self._selector:
            return self._page._button_label
        if "[role='listbox'] [role='option']" in self._selector:
            return self._page._listbox_options[self._page._nth]
        return "1 item selected, Referral"


class _FakeInputLocator(_FakeLocator):
    def fill(self, value: str, *, timeout: int = 0) -> None:
        self._page.actions.append(("fill", f"{self._selector}={value}"))


class _FakeRoleLocator:
    def __init__(self, page: "_FakePage", role: str, name: str) -> None:
        self._page = page
        self._role = role
        self._name = name

    def click(self, *, timeout: int = 0) -> None:
        self._page.actions.append(("click", f"role={self._role}:{self._name}"))


class _FakePage:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []
        self._filter_text = ""
        self._button_label = "Select One"
        self._listbox_options: list[str] = []
        self._nth = 0

    def locator(self, selector: str) -> _FakeLocator:
        if selector.endswith(" input"):
            return _FakeInputLocator(self, selector)
        return _FakeLocator(self, selector)

    def get_by_role(self, role: str, name: str, *, exact: bool = False) -> _FakeRoleLocator:
        return _FakeRoleLocator(self, role, name)

    def click(self, selector: str, *, timeout: int = 0) -> None:
        self.actions.append(("click", selector))

    def wait_for_timeout(self, ms: int) -> None:
        pass


class TestCssAttr:
    def test_escapes_quotes(self) -> None:
        assert _css_attr("Member's") == "Member\\'s"


class TestPromptOption:
    def test_selector_uses_exact_automation_label(self) -> None:
        label = "Social Media"
        assert (
            _PROMPT_OPTION.format(label=_css_attr(label))
            == "div[data-automation-id='promptOption'][data-automation-label='Social Media']"
        )

    def test_click_prompt_option(self) -> None:
        page = _FakePage()
        assert click_prompt_option(page, "Social Media") is True
        assert page.actions[0] == (
            "click",
            "div[data-automation-id='promptOption'][data-automation-label='Social Media']",
        )


class TestMultiselect:
    def test_opens_container_then_clicks_option(self) -> None:
        page = _FakePage()
        assert fill_multiselect(page, "source--source", "Referral") is True
        assert page.actions[0] == (
            "click",
            "[data-fkit-id='source--source'] [data-automation-id='multiSelectContainer']",
        )
        assert "promptOption" in page.actions[1][1]

    def test_two_level_cascade(self) -> None:
        page = _FakePage()
        fill_multiselect(
            page,
            "source--source",
            "LinkedIn",
            parent_label="Job Board",
        )
        assert any("Job Board" in a[1] for a in page.actions)
        assert any("LinkedIn" in a[1] for a in page.actions)


class TestListbox:
    def test_clicks_trigger_then_option(self) -> None:
        page = _FakePage()
        assert select_listbox_button(page, "address--countryRegion", "Florida") is True
        assert page.actions[0] == ("click", "#address--countryRegion")
        assert "Florida" in page.actions[1][1]

    def test_skips_when_listbox_already_filled(self) -> None:
        page = _FakePage()
        page._button_label = "I DO NOT WISH TO SELF-IDENTIFY"
        assert select_listbox_first_match(page, "personalInfoUS--veteranStatus") is True
        assert not any(a[0] == "click" and "#personalInfoUS--veteranStatus" in a[1] for a in page.actions)

    def test_veteran_decline_picks_self_identify_option(self) -> None:
        page = _FakePage()
        page._listbox_options = [
            "Select One",
            "I IDENTIFY AS ONE OR MORE OF THE CLASSIFICATIONS OF PROTECTED VETERANS LISTED ABOVE",
            "I IDENTIFY AS A VETERAN, JUST NOT A PROTECTED VETERAN",
            "I AM NOT A VETERAN",
            "I DO NOT WISH TO SELF-IDENTIFY",
        ]
        assert select_veteran_status_listbox(page, None) is True
        assert page.actions[0] == ("click", "#personalInfoUS--veteranStatus")
        assert any("role=option:I DO NOT WISH TO SELF-IDENTIFY" in a[1] for a in page.actions)