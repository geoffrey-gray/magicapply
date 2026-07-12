"""Unit tests for board destination resolve helpers and handlers."""

from __future__ import annotations

from magicapply.domain.models.job import Job
from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
from magicapply.infrastructure.browser.ats.indeed import IndeedHandler
from magicapply.infrastructure.browser.ats.linkedin import LinkedInHandler
from magicapply.infrastructure.browser.board_resolve import resolve_board_destination
from magicapply.infrastructure.sources.apply_url import (
    description_looks_thin,
    indeed_apply_entry_url,
    is_indeed_applystart_url,
    mark_board_resolve_done,
    needs_board_destination_resolve,
)


def test_indeed_applystart_helpers() -> None:
    assert is_indeed_applystart_url("https://www.indeed.com/applystart?jk=abc")
    assert not is_indeed_applystart_url("https://jobs.ashbyhq.com/acme/1")
    job = Job.new(
        source_name="indeed-search",
        url="https://www.indeed.com/viewjob?jk=abc",
        title="Eng",
        company="Acme",
        raw={"indeed_apply_url": "https://www.indeed.com/applystart?jk=abc"},
    )
    assert indeed_apply_entry_url(job) is not None


def test_needs_board_destination_resolve() -> None:
    board = Job.new(
        source_name="indeed-search",
        url="https://www.indeed.com/viewjob?jk=1",
        title="Eng",
        company="Acme",
    )
    assert needs_board_destination_resolve(board)

    external = Job.new(
        source_name="indeed-search",
        url="https://www.indeed.com/viewjob?jk=1",
        apply_url="https://jobs.ashbyhq.com/acme/1",
        title="Eng",
        company="Acme",
        description="x" * 250,
    )
    assert not needs_board_destination_resolve(external)

    done = mark_board_resolve_done(board)
    assert not needs_board_destination_resolve(done)


def test_description_looks_thin() -> None:
    assert description_looks_thin("short")
    assert not description_looks_thin("y" * 250)


def test_factory_routes_board_hosts() -> None:
    assert isinstance(
        ATSHandlerFactory.for_url("https://www.indeed.com/viewjob?jk=1"),
        IndeedHandler,
    )
    assert isinstance(
        ATSHandlerFactory.for_url("https://www.linkedin.com/jobs/view/1"),
        LinkedInHandler,
    )


def test_resolve_board_destination_extracts_external() -> None:
    job = Job.new(
        source_name="indeed-search",
        url="https://www.indeed.com/viewjob?jk=xyz",
        title="Eng",
        company="Acme",
        description="snippet",
    )

    class _Page:
        url = job.url

        def goto(self, url: str) -> None:
            self.url = url

        def content(self) -> str:
            return """
            <html><body>
              <div id="applyButtonLinkContainer">
                <a href="https://jobs.ashbyhq.com/acme/role">Apply on company site</a>
              </div>
              <div id="jobDescriptionText">%s</div>
            </body></html>
            """ % ("JD text " * 40)

        def click(self, selector: str) -> None:
            raise RuntimeError("no click")

    updated, changed = resolve_board_destination(_Page(), job, dwell=False)
    assert changed
    assert updated.apply_url is not None
    assert "ashbyhq.com" in updated.apply_url
    assert updated.raw.get("board_resolve") == "done"
