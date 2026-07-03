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
    ) -> None:
        self._apps = applications_repo
        self._jobs = jobs_repo
        self._data_builder = data_builder

    def apply_one(
        self,
        *,
        page: PageDriver,
        application: Application,
        job: Job,
        application_data: ApplicationData,
    ) -> ApplyReport:
        if application.state is not ApplicationState.TAILORED:
            raise ValueError(
                f"apply_one requires TAILORED state; got {application.state}"
            )

        handler = ATSHandlerFactory.for_url(job.url)
        if handler is None:
            application.transition_to(
                ApplicationState.FAILED,
                reason=f"no ATS handler for URL: {job.url}",
            )
            application.error = "unsupported ATS"
            application.attempts += 1
            self._apps.save(application)
            return ApplyReport(application.id, application.state, "unsupported ATS")

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
