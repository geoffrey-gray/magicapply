"""Unit tests for LinkedIn same-origin navigation helper."""

from __future__ import annotations

from magicapply.infrastructure.browser.navigate import linkedin_same_origin_goto


class _FakePage:
    def __init__(self, url: str = "") -> None:
        self.url = url
        self.gotos: list[str] = []
        self.assigns: list[str] = []

    def goto(self, url: str, **_kwargs: object) -> None:
        self.gotos.append(url)
        self.url = url

    def evaluate(self, _script: str, path: str) -> None:
        self.assigns.append(path)
        self.url = f"https://www.linkedin.com{path}"

    def content(self) -> str:
        # Non-empty so rate-limit guard does not fire in unit tests.
        return "<html><body>" + ("x" * 250) + "</body></html>"

    def wait_for_url(self, _pattern: str, **_kwargs: object) -> None:
        return

    def wait_for_timeout(self, _ms: int) -> None:
        return


def test_linkedin_same_origin_goto_warms_then_assigns() -> None:
    page = _FakePage()
    linkedin_same_origin_goto(
        page, "https://www.linkedin.com/jobs/view/123456"
    )
    assert page.gotos[0] == "https://www.linkedin.com/feed/"
    assert page.assigns == ["/jobs/view/123456"]
    assert "jobs/view/123456" in page.url


def test_linkedin_same_origin_goto_skips_warm_when_already_on_site() -> None:
    page = _FakePage(url="https://www.linkedin.com/feed/")
    linkedin_same_origin_goto(
        page, "https://www.linkedin.com/jobs/view/999/?trk=x"
    )
    assert page.gotos == []
    assert page.assigns == ["/jobs/view/999/?trk=x"]
