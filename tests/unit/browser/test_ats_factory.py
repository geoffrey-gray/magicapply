"""Tests for ATSHandlerFactory URL-based dispatch."""

from __future__ import annotations

import pytest

from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
from magicapply.infrastructure.browser.ats.generic import GenericHandler
from magicapply.infrastructure.browser.ats.greenhouse import GreenhouseHandler


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/acme/jobs/123",
        "https://job-boards.greenhouse.io/acme/jobs/456",
        "https://acme.greenhouse.io/jobs/789",
    ],
)
def test_greenhouse_urls_dispatch(url: str) -> None:
    handler = ATSHandlerFactory.for_url(url)
    assert isinstance(handler, GreenhouseHandler)


def test_unknown_url_returns_generic_handler() -> None:
    handler = ATSHandlerFactory.for_url("https://careers.unknown-custom.com/jobs/1")
    assert isinstance(handler, GenericHandler)
