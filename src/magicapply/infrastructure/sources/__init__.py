"""Job source adapters (Adapter pattern, per docs/GOF_PATTERNS.md)."""

from magicapply.infrastructure.sources.base import JobSource, SourceError
from magicapply.infrastructure.sources.custom_url import (
    CareerPageAdapter,
    JobUrlAdapter,
    extract_jsonld_jobs,
)
from magicapply.infrastructure.sources.factory import build_source
from magicapply.infrastructure.sources.linkedin import LinkedInAdapter
from magicapply.infrastructure.sources.rate_limit import RateLimiter

__all__ = [
    "CareerPageAdapter",
    "JobSource",
    "JobUrlAdapter",
    "LinkedInAdapter",
    "RateLimiter",
    "SourceError",
    "build_source",
    "extract_jsonld_jobs",
]
