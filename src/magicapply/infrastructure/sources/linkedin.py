"""LinkedIn adapter — search-based, ToS-sensitive.

MVP status: **deferred**.

LinkedIn's search results page is JavaScript-rendered and blocks unauthenticated
scraping. Anything reliable enough to ship needs Playwright + a live session
cookie, which arrives in Phase 9 alongside the ATS browser stack. Rather than
half-implement here, this adapter raises a `SourceError` with a clear message.

Users who want to feed LinkedIn *specific* job URLs today can drop them into a
`job_url` source — LinkedIn's individual job pages embed schema.org JSON-LD
that `JobUrlAdapter` handles cleanly.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from magicapply.config.models import LinkedInSource
from magicapply.infrastructure.sources.base import SourceError

if TYPE_CHECKING:
    from magicapply.domain.models.job import Job


class LinkedInAdapter:
    """Stub adapter. Config is accepted so profiles referencing LinkedIn validate."""

    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    def from_config(cls, config: LinkedInSource) -> LinkedInAdapter:
        return cls(name=config.name)

    def discover(self) -> Iterator[Job]:
        raise SourceError(
            "LinkedIn search-based discovery is not implemented in MVP. "
            "For now, add specific LinkedIn job URLs to a 'job_url' source — "
            "individual LinkedIn postings expose schema.org JSON-LD that "
            "JobUrlAdapter parses. Search support lands in Phase 9 with "
            "Playwright + authenticated session cookie."
        )
        yield  # pragma: no cover  (unreachable; makes this a generator)
