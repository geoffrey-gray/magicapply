"""ApplyPipeline — take TAILORED applications through submission.

Wires ATSHandlerFactory + a PageDriver into the state machine. ``apply_one``
takes a single Application + Job + ApplicationData and drives one submission;
``apply_batch`` walks every TAILORED application for a profile inside one
PlaywrightSession, calling ``apply_one`` per job.

Callers own the actual Playwright session lifecycle (login, storage-state
loading) and pass in a session ``apply_batch`` can pull pages from. Left
small on purpose: full Phase 9 completion work is per-ATS field mapping,
not this composition.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from magicapply.domain.apply.throttle import ApplyThrottle
from magicapply.domain.models.application import Application, ApplicationState
from magicapply.domain.models.job import Job
from magicapply.domain.repositories import (
    ApplicationsRepository,
    JobsRepository,
)
from magicapply.infrastructure.browser.ats.base import (
    ApplicationData,
    PageDriver,
)
from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory


def ats_key_for_url(url: str) -> str | None:
    """Derive the throttle / stats bucket for an apply destination URL.

    Board listings use host buckets (``indeed`` / ``linkedin`` / ``glassdoor``)
    so GenericHandler catch-all does not collapse all boards into one cap.
    Big-four ATS URLs use the handler name (``greenhouse``, ``workday``, …).
    """
    from urllib.parse import urlparse

    host = urlparse(url or "").netloc.lower()
    if "indeed.com" in host:
        return "indeed"
    if "linkedin.com" in host:
        return "linkedin"
    if "glassdoor.com" in host or "glassdoor.co.uk" in host:
        return "glassdoor"

    handler = ATSHandlerFactory.for_url(url)
    if handler is None:
        return None
    name = handler.__class__.__name__
    if name.endswith("Handler"):
        name = name[: -len("Handler")]
    return name.lower()

logger = logging.getLogger(__name__)


@dataclass
class ApplyReport:
    application_id: str
    final_state: ApplicationState
    error: str | None = None


class _SessionProto(Protocol):
    """Minimal PlaywrightSession surface used by apply_batch — just new_page."""

    def new_page(self) -> PageDriver: ...


# The data_builder closes over `loaded` (base config, static answers) and is
# called per-application with the dry_run bit picked by the CLI.
DataBuilder = Callable[[Application, Job], ApplicationData]


class ApplyPipeline:
    def __init__(
        self,
        *,
        applications_repo: ApplicationsRepository,
        jobs_repo: JobsRepository | None = None,
        data_builder: DataBuilder | None = None,
        throttle: ApplyThrottle | None = None,
    ) -> None:
        self._apps = applications_repo
        self._jobs = jobs_repo
        self._data_builder = data_builder
        self._throttle = throttle

    def apply_one(
        self,
        *,
        page: PageDriver,
        application: Application,
        job: Job,
        application_data: ApplicationData,
        retry: bool = False,
    ) -> ApplyReport:
        allowed = (
            {
                ApplicationState.TAILORED,
                ApplicationState.FAILED,
                ApplicationState.NEEDS_INTERVENTION,
                ApplicationState.APPLYING,  # orphaned mid-run
                ApplicationState.APPLIED,  # dry-run re-verify (W.4 fix loop)
            }
            if retry
            else {ApplicationState.TAILORED}
        )
        if application.state not in allowed:
            raise ValueError(
                f"apply_one requires {sorted(s.value for s in allowed)}; "
                f"got {application.state}"
            )
        if (
            application.state is ApplicationState.APPLIED
            and not application.dry_run
        ):
            raise ValueError(
                "cannot re-apply a real submission; only dry-run rows support --retry"
            )

        from magicapply.infrastructure.sources.apply_url import (
            job_with_resolved_apply_url,
            resolve_job_apply_destination,
        )

        # Prefer external/embed apply URLs when known. If the destination is
        # still an Indeed/LinkedIn listing, apply via GenericHandler (fallback)
        # under per-board throttle — do NOT skip board applies.
        job = job_with_resolved_apply_url(job)
        apply_target = resolve_job_apply_destination(job)
        if application_data.job_url != apply_target:
            application_data = application_data.model_copy(
                update={"job_url": apply_target, "job": job}
            )

        handler = ATSHandlerFactory.for_url(apply_target)
        if handler is None:
            application.transition_to(
                ApplicationState.FAILED,
                reason=f"no ATS handler for URL: {apply_target}",
            )
            application.error = "unsupported ATS"
            application.attempts += 1
            self._apps.save(application)
            return ApplyReport(application.id, application.state, "unsupported ATS")

        # Throttle pre-flight — check right before we transition to
        # APPLYING and touch the browser. On deny, leave the application
        # in TAILORED so score-first can try the next-best allowed dest.
        # Board listings throttle as indeed/linkedin (not a single "generic").
        if self._throttle is not None:
            ats_key = ats_key_for_url(apply_target) or "unknown"
            decision = self._throttle.check(ats=ats_key)
            if not decision.allowed:
                logger.info(
                    "throttle: deferring application %s (job=%s): %s",
                    application.id, job.id, decision.reason,
                )
                return ApplyReport(
                    application.id,
                    application.state,
                    f"throttle: {decision.reason}",
                )

        if application.state is not ApplicationState.APPLYING:
            application.transition_to(ApplicationState.APPLYING)
        application.attempts += 1
        self._apps.save(application)

        result = handler.apply(page, application_data)

        target = {
            "applied": ApplicationState.APPLIED,
            "needs_intervention": ApplicationState.NEEDS_INTERVENTION,
            "failed": ApplicationState.FAILED,
        }[result.state]

        application.transition_to(target, reason=result.error or "submitted")
        application.error = result.error
        # A successful dry-run lands in the APPLIED terminal state so the
        # pipeline plumbing stays uniform; the flag on the row is what tells
        # a real submission from a dry-run one.
        application.dry_run = application_data.dry_run
        self._apps.save(application)

        return ApplyReport(application.id, application.state, result.error)

    def apply_batch(
        self,
        *,
        session: _SessionProto,
        profile_name: str,
        dry_run: bool,
        max_outcomes: int | None = None,
        deadline: datetime | None = None,
        pace_seconds: float | None = None,
        include_retry_states: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
    ) -> list[ApplyReport]:
        """Apply to TAILORED applications for a profile in one session.

        Default (no pacing kwargs): one score-ordered pass over TAILORED rows.

        Paced mode (``max_outcomes`` / ``deadline`` / ``pace_seconds``):
        re-lists candidates sorted by **score** (best first), applies when
        throttle allows that destination, **skips** throttle-denied jobs to
        try the next-best, sleeps after counted outcomes only, and optionally
        includes FAILED / NEEDS_INTERVENTION via ``apply_one(..., retry=True)``.
        Throttle deny does not count toward ``max_outcomes``. Board listings
        (Indeed/LinkedIn) are valid apply targets via GenericHandler when no
        external URL is known.
        """
        if self._jobs is None or self._data_builder is None:
            raise RuntimeError(
                "apply_batch requires jobs_repo and data_builder — "
                "supply them to ApplyPipeline.__init__"
            )

        now_fn = clock or (lambda: datetime.now(UTC))
        paced = any(
            v is not None for v in (max_outcomes, deadline, pace_seconds)
        ) or include_retry_states

        if not paced:
            return self._apply_batch_once(
                session=session,
                profile_name=profile_name,
                dry_run=dry_run,
                include_retry_states=False,
            )

        reports: list[ApplyReport] = []
        stalled_passes = 0
        while True:
            if deadline is not None and now_fn() >= deadline:
                logger.info("apply_batch: deadline reached; stopping")
                break
            if max_outcomes is not None and _count_outcomes(reports) >= max_outcomes:
                logger.info(
                    "apply_batch: max_outcomes=%s reached; stopping", max_outcomes
                )
                break

            candidates = self._list_batch_candidates(
                profile_name, include_retry_states=include_retry_states
            )
            ordered = _order_candidates_by_score(candidates)
            if not ordered:
                logger.info("apply_batch: no candidates remaining")
                break

            counted_this_pass = 0
            skipped_quota = 0

            for app in ordered:
                if deadline is not None and now_fn() >= deadline:
                    break
                if max_outcomes is not None and _count_outcomes(reports) >= max_outcomes:
                    break

                job = self._jobs.get(app.job_id)
                if job is None:
                    logger.warning(
                        "apply_batch: no job for application %s (job_id=%s); skipping",
                        app.id,
                        app.job_id,
                    )
                    continue

                retry = app.state is not ApplicationState.TAILORED
                data = self._data_builder(app, job, dry_run=dry_run)  # type: ignore[call-arg]
                page = session.new_page()
                report = self.apply_one(
                    page=page,
                    application=app,
                    job=job,
                    application_data=data,
                    retry=retry,
                )
                reports.append(report)

                # Throttle: try next-best different destination — do not abort wave.
                if _is_throttle_defer(report):
                    skipped_quota += 1
                    logger.info(
                        "apply_batch: throttle skip app=%s; trying next-best",
                        app.id,
                    )
                    continue

                if _is_counted_outcome(report):
                    counted_this_pass += 1
                    if (
                        max_outcomes is not None
                        and _count_outcomes(reports) >= max_outcomes
                    ):
                        break
                    if pace_seconds is not None:
                        wait = pace_seconds
                        if deadline is not None:
                            remaining = (deadline - now_fn()).total_seconds()
                            wait = max(0.0, min(wait, remaining))
                        if wait > 0:
                            logger.info(
                                "apply_batch: paced sleep %.0fs after outcome %s",
                                wait,
                                report.final_state.value,
                            )
                            sleep(wait)

            if max_outcomes is not None and _count_outcomes(reports) >= max_outcomes:
                break
            if deadline is not None and now_fn() >= deadline:
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
                    wait = pace_seconds if pace_seconds is not None else 900.0
                    if deadline is not None:
                        remaining = (deadline - now_fn()).total_seconds()
                        wait = max(0.0, min(wait, remaining))
                    if wait <= 0:
                        break
                    logger.info(
                        "apply_batch: no allowed candidates "
                        "(quota_skips=%d); sleeping %.0fs then re-list",
                        skipped_quota,
                        wait,
                    )
                    sleep(wait)
                    continue
                break

            stalled_passes = 0
            # Had progress — re-list remaining TAILORED by score (no rediscover).
            continue

        return reports

    def _apply_batch_once(
        self,
        *,
        session: _SessionProto,
        profile_name: str,
        dry_run: bool,
        include_retry_states: bool,
    ) -> list[ApplyReport]:
        """One score-ordered pass over TAILORED (optional retry states)."""
        assert self._jobs is not None and self._data_builder is not None
        reports: list[ApplyReport] = []
        ordered = _order_candidates_by_score(
            self._list_batch_candidates(
                profile_name, include_retry_states=include_retry_states
            )
        )
        for app in ordered:
            job = self._jobs.get(app.job_id)
            if job is None:
                logger.warning(
                    "apply_batch: no job for application %s (job_id=%s); skipping",
                    app.id,
                    app.job_id,
                )
                continue
            retry = app.state is not ApplicationState.TAILORED
            data = self._data_builder(app, job, dry_run=dry_run)  # type: ignore[call-arg]
            page = session.new_page()
            reports.append(
                self.apply_one(
                    page=page,
                    application=app,
                    job=job,
                    application_data=data,
                    retry=retry,
                )
            )
        return reports

    def _list_batch_candidates(
        self,
        profile_name: str,
        *,
        include_retry_states: bool,
    ) -> list[Application]:
        apps = list(
            self._apps.list_by_state_and_profile(
                ApplicationState.TAILORED, profile_name
            )
        )
        if include_retry_states:
            apps.extend(
                self._apps.list_by_state_and_profile(
                    ApplicationState.FAILED, profile_name
                )
            )
            apps.extend(
                self._apps.list_by_state_and_profile(
                    ApplicationState.NEEDS_INTERVENTION, profile_name
                )
            )
        return apps


def _is_throttle_defer(report: ApplyReport) -> bool:
    return bool(report.error and report.error.startswith("throttle:"))


def _is_counted_outcome(report: ApplyReport) -> bool:
    if _is_throttle_defer(report):
        return False
    return report.final_state in {
        ApplicationState.APPLIED,
        ApplicationState.FAILED,
        ApplicationState.NEEDS_INTERVENTION,
    }


def _count_outcomes(reports: list[ApplyReport]) -> int:
    return sum(1 for r in reports if _is_counted_outcome(r))


def _order_candidates_by_score(apps: list[Application]) -> list[Application]:
    """Best JD-fit first; TAILORED before FAILED/NI retries; stable ties.

    Throttle skips are handled while walking this list so the next attempt
    is always the highest-score job that is currently allowed.
    """

    def _sort_key(a: Application) -> tuple:
        state_rank = 0 if a.state is ApplicationState.TAILORED else 1
        return (state_rank, -(a.score or 0), a.updated_at, a.job_id)

    return sorted(apps, key=_sort_key)
