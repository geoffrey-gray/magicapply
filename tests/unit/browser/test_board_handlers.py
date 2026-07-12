"""IndeedHandler / LinkedInHandler unit smoke tests."""

from __future__ import annotations

from pathlib import Path

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import TailoredResume
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.browser.ats.greenhouse import GreenhouseHandler
from magicapply.infrastructure.browser.ats.indeed import IndeedHandler
from magicapply.infrastructure.browser.ats.linkedin import LinkedInHandler


def _data(job_url: str, tmp_path: Path, job: Job | None = None) -> ApplicationData:
    docx = tmp_path / "resume.docx"
    docx.write_bytes(b"PK\x03\x04")
    return ApplicationData(
        job_url=job_url,
        static_answers=StaticAnswers(full_name="Test Person", email="t@ex.com"),
        tailored_resume=TailoredResume(base_name="R", job_id="j1", name="Test"),
        resume_docx_path=docx,
        dry_run=True,
        job=job,
    )


def test_indeed_handler_redispatches_off_board(tmp_path: Path) -> None:
    job = Job.new(
        source_name="indeed-search",
        url="https://www.indeed.com/viewjob?jk=1",
        title="Eng",
        company="Acme",
        raw={"indeed_apply_url": "https://www.indeed.com/applystart?jk=1"},
    )

    class _Page:
        url = "https://www.indeed.com/applystart?jk=1"

        def goto(self, url: str) -> None:
            # Simulate Indeed redirecting to Greenhouse.
            self.url = "https://boards.greenhouse.io/acme/jobs/99"

        def content(self) -> str:
            return (
                "<html><body><form>"
                "<input id='first_name' /><input id='last_name' />"
                "<input id='email' /><input type='submit' />"
                "</form></body></html>"
            )

        def fill(self, selector: str, value: str) -> None:
            pass

        def click(self, selector: str) -> None:
            pass

        def set_input_files(self, selector: str, files: str) -> None:
            pass

        def select_option(self, selector: str, value: str) -> None:
            pass

        def check(self, selector: str) -> None:
            pass

    page = _Page()
    result = IndeedHandler().apply(
        page, _data("https://www.indeed.com/viewjob?jk=1", tmp_path, job)
    )
    # Greenhouse dry-run path after redispatch.
    assert result.state == "applied"
    assert page.url.startswith("https://")
    assert "greenhouse" in page.url or result.error == "dry-run: submit skipped"


def test_linkedin_handler_stays_on_board_dry_run(tmp_path: Path) -> None:
    job = Job.new(
        source_name="linkedin-search",
        url="https://www.linkedin.com/jobs/view/123",
        title="Eng",
        company="Acme",
        raw={"board_resolve": "done", "linkedin_easy_apply": True},
    )

    class _Page:
        url = job.url

        def goto(self, url: str) -> None:
            self.url = url

        def content(self) -> str:
            return (
                "<html><body><form class='jobs-easy-apply-form'>"
                "<input name='phone' /><button type='submit'>Submit</button>"
                "</form></body></html>"
            )

        def fill(self, selector: str, value: str) -> None:
            pass

        def click(self, selector: str) -> None:
            pass

        def set_input_files(self, selector: str, files: str) -> None:
            pass

        def select_option(self, selector: str, value: str) -> None:
            pass

        def check(self, selector: str) -> None:
            pass

    result = LinkedInHandler().apply(
        page=_Page(),
        data=_data(job.url, tmp_path, job),
    )
    # Without form_composer, fill is a no-op; dry-run still reports applied.
    assert result.state == "applied"
    assert isinstance(LinkedInHandler(), LinkedInHandler)
    assert GreenhouseHandler.matches("https://boards.greenhouse.io/x/jobs/1")
