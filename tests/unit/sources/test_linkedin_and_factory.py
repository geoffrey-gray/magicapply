"""Tests for LinkedInAdapter stub + build_source dispatch."""

from __future__ import annotations

import pytest

from magicapply.config.models import CareerPageSource, JobUrlSource, LinkedInSource
from magicapply.infrastructure.sources import build_source
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.custom_url import (
    CareerPageAdapter,
    JobUrlAdapter,
)
from magicapply.infrastructure.sources.linkedin import LinkedInAdapter


class TestLinkedInStub:
    def test_discover_raises_source_error(self) -> None:
        adapter = LinkedInAdapter.from_config(LinkedInSource(name="linkedin-search"))
        with pytest.raises(SourceError, match="not implemented"):
            list(adapter.discover())


class TestFactory:
    def test_career_page(self) -> None:
        cfg = CareerPageSource(name="a", urls=[])
        assert isinstance(build_source(cfg), CareerPageAdapter)

    def test_job_url(self) -> None:
        cfg = JobUrlSource(name="b", urls=[])
        assert isinstance(build_source(cfg), JobUrlAdapter)

    def test_linkedin(self) -> None:
        cfg = LinkedInSource(name="c")
        assert isinstance(build_source(cfg), LinkedInAdapter)
