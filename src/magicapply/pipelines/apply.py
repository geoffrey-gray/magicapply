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
from collections.abc import Callable
from dataclasses import dataclass
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
    """Derive the ATS name used as the throttle bucket + as the
    `SqlApplicationsRepository.count_applied_in_window` key. Returns the
    handler class name lowercased with the `handler` suffix stripped
    (`GreenhouseHandler` → `greenhouse`). Returns None when no handler
    matches — those apps are already routed to FAILED elsewhere."""
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

        # Resolve embedded ATS destinations (e.g. Stripe careers → Greenhouse
        # embed form) before handler selection. Indeed/LinkedIn listing URLs
        # are valid apply targets (GenericHandler) — throttle rate-limits them;
        # do not refuse board applies when no external ATS URL is known.
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
        # in TAILORED so the next batch (once the window rolls) picks it
        # up naturally. Real submissions and dry-runs both count against
        # the cap because both generate ATS traffic.
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
    ) -> list[ApplyReport]:
        """Apply to every TAILORED application for a profile in one session.

        Reuses ``apply_one`` per job — the browser session is shared, but
        each job gets a fresh page. A missing Job row for an Application is
        logged and skipped rather than crashing the batch, matching how
        ``TailoringPipeline`` treats the same corruption case.
        """
        if self._jobs is None or self._data_builder is None:
            raise RuntimeError(
                "apply_batch requires jobs_repo and data_builder — "
                "supply them to ApplyPipeline.__init__"
            )

        reports: list[ApplyReport] = []
        tailored = self._apps.list_by_state_and_profile(
            ApplicationState.TAILORED, profile_name
        )
        for app in tailored:
            job = self._jobs.get(app.job_id)
            if job is None:
                logger.warning(
                    "apply_batch: no job for application %s (job_id=%s); skipping",
                    app.id,
                    app.job_id,
                )
                continue
            data = self._data_builder(app, job, dry_run=dry_run)  # type: ignore[call-arg]
            page = session.new_page()
            reports.append(
                self.apply_one(
                    page=page,
                    application=app,
                    job=job,
                    application_data=data,
                )
            )
        return reports
