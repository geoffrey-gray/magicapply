"""Apply batch strategies — simple one-pass vs. paced multi-pass."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from magicapply.domain.models.application import Application, ApplicationState
from magicapply.domain.models.job import Job
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.pipelines.apply_types import ApplyReport
from magicapply.pipelines.apply_utils import (
    count_outcomes,
    is_counted_outcome,
    is_throttle_defer,
)

logger = logging.getLogger(__name__)


class _SessionProto(Protocol):
    """Session that can create new pages."""

    def new_page(self) -> object:
        """Create a new page for browser automation."""
        ...


class _ApplierProto(Protocol):
    """Apply pipeline interface needed by strategies."""

    def apply_one(
        self,
        *,
        page: object,
        application: Application,
        job: Job,
        application_data: ApplicationData,
        retry: bool = False,
    ) -> ApplyReport:
        """Apply to one job."""
        ...


class ApplyStrategy(Protocol):
    """Strategy for applying to a batch of applications."""

    def execute(
        self,
        *,
        applier: _ApplierProto,
        session: _SessionProto,
        candidates: list[Application],
        jobs_getter: Callable[[str], Job | None],
        data_builder: Callable[[Application, Job, bool], ApplicationData],
        dry_run: bool,
    ) -> list[ApplyReport]:
        """Execute the strategy and return reports."""
        ...


class SimpleApplyStrategy:
    """One score-ordered pass over candidates (no pacing, no throttle handling)."""

    def execute(
        self,
        *,
        applier: _ApplierProto,
        session: _SessionProto,
        candidates: list[Application],
        jobs_getter: Callable[[str], Job | None],
        data_builder: Callable[[Application, Job, bool], ApplicationData],
        dry_run: bool,
    ) -> list[ApplyReport]:
        """Apply to all candidates in one pass."""
        reports: list[ApplyReport] = []
        for app in candidates:
            job = jobs_getter(app.job_id)
            if job is None:
                logger.warning(
                    "apply_batch: no job for application %s (job_id=%s); skipping",
                    app.id,
                    app.job_id,
                )
                continue
            retry = app.state is not ApplicationState.TAILORED
            data = data_builder(app, job, dry_run)
            page = session.new_page()
            reports.append(
                applier.apply_one(
                    page=page,
                    application=app,
                    job=job,
                    application_data=data,
                    retry=retry,
                )
            )
        return reports


class PacedApplyStrategy:
    """Multi-pass paced strategy with throttle handling and outcome limits."""

    def __init__(
        self,
        *,
        max_outcomes: int | None = None,
        deadline: datetime | None = None,
        pace_seconds: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._max_outcomes = max_outcomes
        self._deadline = deadline
        self._pace_seconds = pace_seconds
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        *,
        applier: _ApplierProto,
        session: _SessionProto,
        candidates: list[Application],
        jobs_getter: Callable[[str], Job | None],
        data_builder: Callable[[Application, Job, bool], ApplicationData],
        dry_run: bool,
    ) -> list[ApplyReport]:
        """Apply to candidates with pacing, respecting max_outcomes and deadline."""
        reports: list[ApplyReport] = []
        stalled_passes = 0

        while candidates:
            if self._deadline is not None and self._clock() >= self._deadline:
                logger.info("apply_batch: deadline reached; stopping")
                break
            if self._max_outcomes is not None and count_outcomes(reports) >= self._max_outcomes:
                logger.info(
                    "apply_batch: max_outcomes=%s reached; stopping", self._max_outcomes
                )
                break

            counted_this_pass = 0
            skipped_quota = 0

            for app in candidates:
                if self._deadline is not None and self._clock() >= self._deadline:
                    break
                if self._max_outcomes is not None and count_outcomes(reports) >= self._max_outcomes:
                    break

                job = jobs_getter(app.job_id)
                if job is None:
                    logger.warning(
                        "apply_batch: no job for application %s (job_id=%s); skipping",
                        app.id,
                        app.job_id,
                    )
                    continue

                retry = app.state is not ApplicationState.TAILORED
                data = data_builder(app, job, dry_run)
                page = session.new_page()
                report = applier.apply_one(
                    page=page,
                    application=app,
                    job=job,
                    application_data=data,
                    retry=retry,
                )
                reports.append(report)

                # Throttle: try next-best different destination — do not abort wave.
                if is_throttle_defer(report):
                    skipped_quota += 1
                    logger.info(
                        "apply_batch: throttle skip app=%s; trying next-best",
                        app.id,
                    )
                    continue

                if is_counted_outcome(report):
                    counted_this_pass += 1
                    if (
                        self._max_outcomes is not None
                        and count_outcomes(reports) >= self._max_outcomes
                    ):
                        break
                    if self._pace_seconds is not None:
                        wait = self._pace_seconds
                        if self._deadline is not None:
                            remaining = (self._deadline - self._clock()).total_seconds()
                            wait = max(0.0, min(wait, remaining))
                        if wait > 0:
                            logger.info(
                                "apply_batch: paced sleep %.0fs after outcome %s",
                                wait,
                                report.final_state.value,
                            )
                            self._sleep(wait)

            if self._max_outcomes is not None and count_outcomes(reports) >= self._max_outcomes:
                break
            if self._deadline is not None and self._clock() >= self._deadline:
                break

            # No counted applies this pass: only quota skips left, or nothing
            # workable. Sleep once then re-list; if still blocked, return so
            # outer ``run`` can rediscover later.
            if counted_this_pass == 0:
                if skipped_quota > 0:
                    stalled_passes += 1
                    if stalled_passes >= 2:
                        logger.info(
                            "apply_batch: still no allowed candidates after wait; "
                            "returning (quota_skips=%d)",
                            skipped_quota,
                        )
                        break
                    wait = self._pace_seconds if self._pace_seconds is not None else 900.0
                    if self._deadline is not None:
                        remaining = (self._deadline - self._clock()).total_seconds()
                        wait = max(0.0, min(wait, remaining))
                    if wait <= 0:
                        break
                    logger.info(
                        "apply_batch: no allowed candidates "
                        "(quota_skips=%d); sleeping %.0fs then re-list",
                        skipped_quota,
                        wait,
                    )
                    self._sleep(wait)
                    # Re-list happens in outer apply_batch, not here
                    break
                break

            stalled_passes = 0
            # Had progress — need re-list in outer apply_batch
            break

        return reports

    def should_continue(self, reports: list[ApplyReport]) -> bool:
        """Check if strategy should continue to next iteration.

        Called by the orchestrator (apply_batch) after each batch to determine
        whether to re-list candidates and run another iteration.

        Returns False when:
        - Deadline has been reached
        - max_outcomes limit has been hit

        Returns True otherwise, allowing the orchestrator to continue.
        """
        if self._deadline is not None and self._clock() >= self._deadline:
            return False
        if self._max_outcomes is not None and count_outcomes(reports) >= self._max_outcomes:
            return False
        # Continue if there's potential for more work
        return True
