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
from magicapply.pipelines.apply_types import ApplyReport
from magicapply.pipelines.apply_utils import (
    count_outcomes,
    is_counted_outcome,
    is_throttle_defer,
)


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
        retailor: Callable[[Application, Job], Application] | None = None,
    ) -> None:
        self._apps = applications_repo
        self._jobs = jobs_repo
        self._data_builder = data_builder
        self._throttle = throttle
        self._retailor = retailor

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

        from magicapply.infrastructure.browser.board_resolve import (
            resolve_board_destination,
        )
        from magicapply.infrastructure.sources.apply_url import (
            description_looks_thin,
            job_with_resolved_apply_url,
            needs_board_destination_resolve,
            resolve_job_apply_destination,
        )

        # Lazy board resolve: prefer external ATS; stamp Easy Apply meta when
        # the listing has no offsite URL. Persist so retries skip re-fetch.
        prev_desc_len = len(job.description or "")
        if needs_board_destination_resolve(job) or (
            job.apply_url
            and description_looks_thin(job.description)
        ):
            job, _changed = resolve_board_destination(page, job)
            if self._jobs is not None:
                job = self._jobs.save(job)
            # JD upgraded after a prior tailor → re-tailor once.
            if (
                self._retailor is not None
                and len(job.description or "") > prev_desc_len + 80
                and application.state is ApplicationState.TAILORED
            ):
                logger.info(
                    "apply_one: JD upgraded for job %s; re-tailoring", job.id
                )
                application = self._retailor(application, job)
                if self._data_builder is not None:
                    application_data = self._data_builder(  # type: ignore[call-arg]
                        application, job, dry_run=application_data.dry_run
                    )

        # Prefer external/embed apply URLs when known. If the destination is
        # still an Indeed/LinkedIn listing, apply via board handler under
        # per-board throttle — do NOT skip board applies.
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

        # If a board handler redirected off-site and persisted apply_url on
        # the in-memory job via handler side effects, keep DB in sync when
        # application_data.job was mutated.
        if self._jobs is not None and isinstance(application_data.job, Job):
            maybe = application_data.job
            if maybe.apply_url and maybe.apply_url != job.apply_url:
                self._jobs.save(maybe)

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

        _clock = clock or (lambda: datetime.now(UTC))
        reports: list[ApplyReport] = []
        stalled_passes = 0

        # Simple mode vs paced mode detection
        paced = any(
            v is not None for v in (max_outcomes, deadline, pace_seconds)
        ) or include_retry_states

        # Natural progression loop
        while True:
            # Re-list candidates (TAILORED + optional retry states, score-ordered)
            candidates = self._list_batch_candidates(
                profile_name, include_retry_states=include_retry_states
            )
            if not candidates:
                break

            # Check exit conditions BEFORE processing
            if deadline is not None and _clock() >= deadline:
                logger.info("apply_batch: deadline reached; stopping")
                break
            if max_outcomes is not None and count_outcomes(reports) >= max_outcomes:
                logger.info(
                    "apply_batch: max_outcomes=%s reached; stopping", max_outcomes
                )
                break

            # Process this batch
            counted_this_pass = 0
            skipped_quota = 0

            for app in candidates:
                # Check limits mid-batch
                if deadline is not None and _clock() >= deadline:
                    break
                if max_outcomes is not None and count_outcomes(reports) >= max_outcomes:
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
                from magicapply.infrastructure.browser.apply_session import (
                    configure_apply_page,
                )

                configure_apply_page(page)
                report = self.apply_one(
                    page=page,
                    application=app,
                    job=job,
                    application_data=data,
                    retry=retry,
                )
                reports.append(report)

                # Throttle deny: app stayed TAILORED, will re-list
                if is_throttle_defer(report):
                    skipped_quota += 1
                    logger.info(
                        "apply_batch: throttle skip app=%s; trying next-best",
                        app.id,
                    )
                    continue

                # Counted outcome: sleep if pacing
                if is_counted_outcome(report):
                    counted_this_pass += 1
                    if (
                        max_outcomes is not None
                        and count_outcomes(reports) >= max_outcomes
                    ):
                        break
                    if pace_seconds is not None:
                        wait = pace_seconds
                        if deadline is not None:
                            remaining = (deadline - _clock()).total_seconds()
                            wait = max(0.0, min(wait, remaining))
                        if wait > 0:
                            logger.info(
                                "apply_batch: paced sleep %.0fs after outcome %s",
                                wait,
                                report.final_state.value,
                            )
                            sleep(wait)

            # Simple mode: one pass only
            if not paced:
                break

            # Exit on limits
            if max_outcomes is not None and count_outcomes(reports) >= max_outcomes:
                break
            if deadline is not None and _clock() >= deadline:
                break

            # Stall detection: no progress made
            if counted_this_pass == 0:
                if skipped_quota > 0:
                    # All candidates throttled - wait once, retry once more
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
                        remaining = (deadline - _clock()).total_seconds()
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
                    continue  # Re-list after sleep
                # No progress and not throttle-related (e.g., all jobs missing).
                # Signal to stop - this won't resolve by re-listing.
                break

            # Progress made - reset stall counter and continue
            stalled_passes = 0

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
