"""Build a concrete JobSource from a config Source entry."""

from __future__ import annotations

from magicapply.config.models import (
    CareerPageSource,
    GlassdoorSource,
    IndeedSource,
    JobUrlSource,
    LinkedInSource,
)
from magicapply.infrastructure.sources.base import JobSource
from magicapply.infrastructure.sources.custom_url import (
    CareerPageAdapter,
    JobUrlAdapter,
)
from magicapply.infrastructure.sources.glassdoor import GlassdoorAdapter
from magicapply.infrastructure.sources.indeed import IndeedAdapter
from magicapply.infrastructure.sources.linkedin import LinkedInAdapter


def build_source(
    config: (
        CareerPageSource
        | JobUrlSource
        | LinkedInSource
        | IndeedSource
        | GlassdoorSource
    ),
) -> JobSource:
    """Return the concrete adapter for a Source config entry."""
    if isinstance(config, CareerPageSource):
        return CareerPageAdapter.from_config(config)
    if isinstance(config, JobUrlSource):
        return JobUrlAdapter.from_config(config)
    if isinstance(config, LinkedInSource):
        return LinkedInAdapter.from_config(config)
    if isinstance(config, IndeedSource):
        return IndeedAdapter.from_config(config)
    if isinstance(config, GlassdoorSource):
        return GlassdoorAdapter.from_config(config)
    raise TypeError(f"unknown source type: {type(config).__name__}")
