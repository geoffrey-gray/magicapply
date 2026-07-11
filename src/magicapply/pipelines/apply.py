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
from urllib.parse import urlparse

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
        max_outcomes: int | None = None,
        deadline: datetime | None = None,
        pace_seconds: float | None = None,
        include_retry_states: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
    ) -> list[ApplyReport]:
        """Apply to TAILORED applications for a profile in one session.

        Default (no pacing kwargs): one pass over every TAILORED row — historical
        batch behavior.

        Paced mode (``max_outcomes`` / ``deadline`` / ``pace_seconds``):
        re-lists candidates, rotates Indeed / LinkedIn / external destinations,
        sleeps after counted outcomes and on throttle denials, and optionally
        includes FAILED / NEEDS_INTERVENTION via ``apply_one(..., retry=True)``.
        Throttle deny does not count toward ``max_outcomes``. Successful dry-run
        rows are already APPLIED and never appear in the TAILORED queue.
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
        wave = 0
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
            ordered = _order_by_host_bucket(candidates, wave=wave, jobs=self._jobs)
            if not ordered:
                logger.info("apply_batch: no candidates remaining this wave")
                break

            wave_progress = False
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
                wave_progress = True

                if _is_throttle_defer(report):
                    wait = pace_seconds if pace_seconds is not None else 900.0
                    if deadline is not None:
                        remaining = (deadline - now_fn()).total_seconds()
                        wait = max(0.0, min(wait, remaining))
                    if wait > 0:
                        logger.info(
                            "apply_batch: throttle defer; sleeping %.0fs "
                            "(caller may re-enter batch after rediscover)",
                            wait,
                        )
                        sleep(wait)
                    # Return to ``run`` so it can rediscover and open a new wave
                    # rather than spinning the same TAILORED rows.
                    return reports

                if _is_counted_outcome(report):
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

            wave += 1
            if not wave_progress:
                break
            # Exhausted current candidate set — return so ``run`` can rediscover.
            break

        return reports

    def _apply_batch_once(
        self,
        *,
        session: _SessionProto,
        profile_name: str,
        dry_run: bool,
        include_retry_states: bool,
    ) -> list[ApplyReport]:
        """Historical one-pass batch over TAILORED (optional retry states)."""
        assert self._jobs is not None and self._data_builder is not None
        reports: list[ApplyReport] = []
        for app in self._list_batch_candidates(
            profile_name, include_retry_states=include_retry_states
        ):
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


def _host_bucket(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    if "indeed.com" in host:
        return "indeed"
    if "linkedin.com" in host:
        return "linkedin"
    return "external"


def _order_by_host_bucket(
    apps: list[Application],
    *,
    wave: int,
    jobs: JobsRepository,
) -> list[Application]:
    """Rotate preference: indeed / linkedin / external (≈ 2:2:1 over 5 waves)."""
    from magicapply.infrastructure.sources.apply_url import resolve_job_apply_destination

    preference = ("indeed", "linkedin", "external", "indeed", "linkedin")
    pref = preference[wave % len(preference)]
    buckets: dict[str, list[Application]] = {
        "indeed": [],
        "linkedin": [],
        "external": [],
    }
    for app in apps:
        job = jobs.get(app.job_id)
        if job is None:
            buckets["external"].append(app)
            continue
        dest = resolve_job_apply_destination(job)
        buckets[_host_bucket(dest)].append(app)

    def _sort_key(a: Application) -> tuple:
        return (-(a.score or 0), a.updated_at, a.job_id)

    for key in buckets:
        buckets[key].sort(key=_sort_key)

    order = [pref] + [b for b in ("indeed", "linkedin", "external") if b != pref]
    ordered: list[Application] = []
    for key in order:
        ordered.extend(buckets[key])
    return ordered
