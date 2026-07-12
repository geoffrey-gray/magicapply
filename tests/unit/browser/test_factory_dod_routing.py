"""Big-four ATS factory routing still wins over board/generic handlers."""

from __future__ import annotations

import pytest

from magicapply.infrastructure.browser.ats.ashby import AshbyHandler
from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
from magicapply.infrastructure.browser.ats.greenhouse import GreenhouseHandler
from magicapply.infrastructure.browser.ats.indeed import IndeedHandler
from magicapply.infrastructure.browser.ats.lever import LeverHandler
from magicapply.infrastructure.browser.ats.linkedin import LinkedInHandler
from magicapply.infrastructure.browser.ats.workday import WorkdayHandler


@pytest.mark.parametrize(
    ("url", "handler_cls"),
    [
        ("https://boards.greenhouse.io/acme/jobs/1", GreenhouseHandler),
        ("https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1", GreenhouseHandler),
        ("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/X", WorkdayHandler),
        ("https://jobs.lever.co/acme/abc-def", LeverHandler),
        ("https://jobs.ashbyhq.com/acme/uuid", AshbyHandler),
        ("https://www.indeed.com/viewjob?jk=1", IndeedHandler),
        ("https://www.linkedin.com/jobs/view/1", LinkedInHandler),
    ],
)
def test_factory_routes_dod_destinations(url: str, handler_cls: type) -> None:
    handler = ATSHandlerFactory.for_url(url)
    assert isinstance(handler, handler_cls)
