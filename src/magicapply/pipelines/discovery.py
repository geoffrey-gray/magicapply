"""DiscoveryPipeline — iterate sources, dedupe, persist, score.

State machine transitions performed by this pipeline:
    (new job)  → Application(DISCOVERED)  → SCORED
    prefilter miss or LLM below threshold → SCORED → REJECTED (caller does this)
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from magicapply.domain.jobs.dedup import dedupe_by_key
from magicapply.domain.jobs.scoring import JobScorer
from magicapply.domain.models.application import Application, ApplicationState
from magicapply.domain.repositories import (
    ApplicationsRepository,
    JobsRepository,
)
from magicapply.infrastructure.sources.base import JobSource, SourceError

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryReport:
    """Summary of one discovery run — logged and returned to the CLI."""

    discovered: int = 0
    duplicates_in_run: int = 0
    already_seen: int = 0
    scored: int = 0
    rejected_by_threshold: int = 0
    source_errors: list[str] = field(default_factory=list)


class DiscoveryPipeline:
    def __init__(
        self,
        *,
        sources: Sequence[JobSource],
        jobs_repo: JobsRepository,
        applications_repo: ApplicationsRepository,
        scorer: JobScorer,
        profile_name: str,
        score_threshold: int,
        source_scorer: Callable[[str], int] | None = None,
    ) -> None:
        self._sources = sources
        self._jobs = jobs_repo
        self._apps = applications_repo
        self._scorer = scorer
        self._profile = profile_name
        self._threshold = score_threshold
        # Optional load-balancing scorer: on cross-source dedup
        # collisions, pick the source with the LOWEST score (usually
        # "recent apply count") so future apply traffic distributes.
        # None → default first-wins semantics.
        self._source_scorer = source_scorer

    def run(self) -> DiscoveryReport:
        report = DiscoveryReport()

        # Corpus snapshot for this run. Paginated sources use this to skip
        # already-ingested IDs so max_jobs_per_run means "up to N *new*
        # jobs", not "re-scrape the same top-N SERP cards every time."
        known_ids = frozenset(j.id for j in self._jobs.list_all())

        all_jobs = []
        for source in self._sources:
            try:
                for job in source.discover(known_ids=known_ids):
                    all_jobs.append(job)
            except SourceError as exc:
                logger.warning("source %s failed: %s", source.name, exc)
                report.source_errors.append(f"{source.name}: {exc}")

        seen_ids: set[str] = set()
        # Cache scorer results per source across the run — a 5-way
        # collision on one dedup_key shouldn't hit the SQL query 5 times,
        # and scores don't change during a single discover pass.
        scorer_cache: dict[str, int] = {}
        cached_scorer = None
        if self._source_scorer is not None:
            def cached_scorer(source_name: str) -> int:  # noqa: E306
                if source_name not in scorer_cache:
                    scorer_cache[source_name] = self._source_scorer(source_name)  # type: ignore[misc]
                return scorer_cache[source_name]
        for job in dedupe_by_key(all_jobs, source_scorer=cached_scorer):
            if job.id in seen_ids:
                report.duplicates_in_run += 1
                continue
            seen_ids.add(job.id)

            _, was_new = self._jobs.upsert(job)
            if not was_new:
                report.already_seen += 1
                continue
            report.discovered += 1

            # New job → open an Application and score it.
            if self._apps.by_job_and_profile(job.id, self._profile) is not None:
                # Racing another run — skip.
                continue

            app = Application(job_id=job.id, profile_name=self._profile)
            self._apps.add(app)

            score = self._scorer.score(job)
            app.transition_to(
                ApplicationState.SCORED,
                reason=f"score={score.value} — {score.rationale[:80]}",
            )
            app.score = score.value
            app.score_rationale = score.rationale
            report.scored += 1

            if score.value < self._threshold:
                app.transition_to(
                    ApplicationState.REJECTED,
                    reason=f"below threshold ({score.value} < {self._threshold})",
                )
                report.rejected_by_threshold += 1

            self._apps.save(app)

        return report
