"""In-run deduplication.

Complements the repository-level dedup (Phase 4). Repos dedup across all-time
persisted jobs; this dedupes within a single discovery run before any DB write.
Both are needed: repo dedup catches "seen this last week"; batch dedup catches
"three sources found the same posting this run."
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from magicapply.domain.models.job import Job


def dedupe_by_key(jobs: Iterable[Job]) -> Iterator[Job]:
    """Yield the first Job seen for each dedup_key. Order preserving."""
    seen: set[str] = set()
    for job in jobs:
        if job.dedup_key in seen:
            continue
        seen.add(job.dedup_key)
        yield job
