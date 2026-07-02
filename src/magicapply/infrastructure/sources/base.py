"""Job source Protocol + shared error type."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from magicapply.domain.models.job import Job


class SourceError(Exception):
    """Raised when a source fails structurally (bad response, wrong format, auth missing)."""


class JobSource(Protocol):
    """Adapter for one configured source. Yields Job instances one at a time.

    Instances bind to one config entry (by `name`). They own their own HTTP
    client and rate limiter — no shared mutable state between sources.
    """

    name: str

    def discover(self) -> Iterator[Job]: ...
