"""ApplyPipeline — take one Application in TAILORED state through submission.

Wires ATSHandlerFactory + a PageDriver into the state machine. Callers own
the actual Playwright session lifecycle (login, storage-state loading) and
pass in a ready-to-use page.

Left small on purpose: full Phase 9 completion work is per-ATS field mapping,
not this composition.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from magicapply.domain.models.application import Application, ApplicationState
from magicapply.domain.models.job import Job
from magicapply.domain.repositories import ApplicationsRepository
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


class ApplyPipeline:
    def __init__(self, *, applications_repo: ApplicationsRepository) -> None:
        self._apps = applications_repo

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
        self._apps.save(application)

        return ApplyReport(application.id, application.state, result.error)
