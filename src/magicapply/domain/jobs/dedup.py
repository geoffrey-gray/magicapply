"""In-run deduplication.

Complements the repository-level dedup (Phase 4). Repos dedup across all-time
persisted jobs; this dedupes within a single discovery run before any DB write.
Both are needed: repo dedup catches "seen this last week"; batch dedup catches
"three sources found the same posting this run."

**Fair source distribution (Phase E):** when a job appears from multiple
sources (e.g., a Greenhouse posting surfaced on LinkedIn + Indeed + Glassdoor
+ the underlying board), the naive first-wins policy always picks whichever
source the pipeline iterates first — usually a hardcoded config order. That
concentrates future apply traffic on one platform.

Passing a `source_scorer` callable makes the winner the source with the
LOWEST score for that dedup collision. Composition wires this to "recent
apply count by source", so sources with fewer apps in the last 24h win
ties and future apply attempts distribute across LinkedIn / Indeed /
Glassdoor / greenhouse-boards. Default (no scorer) preserves first-wins
semantics for backwards compat and every existing test.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

from magicapply.domain.models.job import Job

SourceScorer = Callable[[str], int]


def dedupe_by_key(
    jobs: Iterable[Job],
    *,
    source_scorer: SourceScorer | None = None,
) -> Iterator[Job]:
    """Yield one Job per `dedup_key`.

    When `source_scorer` is None: first-wins, order preserving. Every
    call site pre-existing this option keeps unchanged behavior.

    When `source_scorer` is provided: buffer all jobs, group by
    `dedup_key`, pick the entry whose `source_name` scores lowest per
    the callable. Ties within a group broken by insertion order.
    Yield order across groups is the order in which each group's
    winning member first appeared in the input — same shape as the
    default path, so downstream code doesn't need to know which rule
    is active.
    """
    if source_scorer is None:
        seen: set[str] = set()
        for job in jobs:
            if job.dedup_key in seen:
                continue
            seen.add(job.dedup_key)
            yield job
        return

    # Group by dedup_key. `order[key]` records the first insertion index
    # of that key so we can yield groups in first-seen order.
    groups: dict[str, list[Job]] = {}
    order: dict[str, int] = {}
    for idx, job in enumerate(jobs):
        key = job.dedup_key
        if key not in groups:
            groups[key] = []
            order[key] = idx
        groups[key].append(job)

    for key in sorted(groups, key=lambda k: order[k]):
        candidates = groups[key]
        # Cache score per source so a five-way collision doesn't hit
        # the SQL query five times. Stable min: first insertion wins ties.
        scored = [
            (source_scorer(job.source_name), idx, job)
            for idx, job in enumerate(candidates)
        ]
        scored.sort(key=lambda t: (t[0], t[1]))
        yield scored[0][2]
