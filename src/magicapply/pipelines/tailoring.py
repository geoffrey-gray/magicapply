"""TailoringPipeline — take SCORED applications through resume + cover tailoring.

State machine transition performed by this pipeline:
    SCORED → TAILORED

Artifacts written to disk under ``<data_dir>/tailored/<application_id>/`` so
the user can inspect exactly what will be sent to the ATS:
    - ``resume.yaml``       -- the tailored resume (YAML, same shape as base)
    - ``cover_letter.md``   -- the cover-letter body (Markdown)

The pipeline is naturally idempotent: it queries
``apps_repo.list_by_state_and_profile(SCORED, profile_name)``, so already-
TAILORED applications are not returned on a re-run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from magicapply.domain.models.application import ApplicationState
from magicapply.domain.repositories import (
    ApplicationsRepository,
    JobsRepository,
)
from magicapply.domain.resumes.narrative import NarrativeEngine
from magicapply.domain.resumes.tailor import Tailorer
from magicapply.infrastructure.rendering.docx import DocxResumeRenderer

logger = logging.getLogger(__name__)


@dataclass
class TailoringReport:
    """Summary of one tailoring run — returned to the CLI."""

    tailored: int = 0
    missing_job: int = 0
    errors: list[str] = field(default_factory=list)


class TailoringPipeline:
    def __init__(
        self,
        *,
        apps_repo: ApplicationsRepository,
        jobs_repo: JobsRepository,
        tailorer: Tailorer,
        narrative: NarrativeEngine,
        resume_renderer: DocxResumeRenderer,
        profile_name: str,
        data_dir: Path,
    ) -> None:
        self._apps = apps_repo
        self._jobs = jobs_repo
        self._tailorer = tailorer
        self._narrative = narrative
        self._renderer = resume_renderer
        self._profile = profile_name
        self._data_dir = data_dir

    def run(self) -> TailoringReport:
        report = TailoringReport()
        tailored_root = self._data_dir / "tailored"

        scored = self._apps.list_by_state_and_profile(
            ApplicationState.SCORED, self._profile
        )
        for app in scored:
            job = self._jobs.get(app.job_id)
            if job is None:
                logger.warning(
                    "tailoring: no job for application %s (job_id=%s); skipping",
                    app.id,
                    app.job_id,
                )
                report.missing_job += 1
                continue

            try:
                tailored = self._tailorer.tailor_for(job)
                cover = self._narrative.cover_letter(job)
            except Exception as exc:  # noqa: BLE001
                logger.warning("tailoring failed for %s: %s", app.id, exc)
                report.errors.append(f"{app.id}: {type(exc).__name__}: {exc}")
                continue

            app_dir = tailored_root / app.id
            app_dir.mkdir(parents=True, exist_ok=True)
            (app_dir / "resume.yaml").write_text(
                yaml.safe_dump(
                    tailored.model_dump(mode="json"),
                    sort_keys=False,
                    allow_unicode=True,
                )
            )
            (app_dir / "cover_letter.md").write_text(cover)

            # Render the tailored resume to DOCX for ATS upload. Failures here
            # (template missing, disk full) are treated like tailoring
            # failures — reported per-app, no crash.
            try:
                self._renderer.render(tailored, app_dir / "resume.docx")
            except Exception as exc:  # noqa: BLE001
                logger.warning("resume render failed for %s: %s", app.id, exc)
                report.errors.append(
                    f"{app.id}: render {type(exc).__name__}: {exc}"
                )
                continue

            app.tailored_path = str(app_dir)
            app.transition_to(
                ApplicationState.TAILORED, reason="tailoring pipeline"
            )
            self._apps.save(app)
            report.tailored += 1

        return report
