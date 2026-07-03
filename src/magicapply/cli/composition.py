"""Composition root helpers — assemble collaborators from LoadedConfig.

Deliberately no DI framework. This file is the single place where the
Pydantic config graph becomes runtime objects (repos, LLM client, sources,
scorer, tailorer). CLI commands only ever call helpers here — they don't
construct infrastructure classes themselves.
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

import yaml

from magicapply.config import LoadedConfig, Profile
from magicapply.config.models import ScoringConfig
from magicapply.domain.jobs.scoring import JobScorer, LLMScorer, Prefilter
from magicapply.domain.models.application import Application
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import BaseResume, TailoredResume
from magicapply.domain.resumes.narrative import NarrativeEngine
from magicapply.domain.resumes.tailor import Tailorer
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.llm import build_client
from magicapply.infrastructure.rendering.docx import DocxResumeRenderer
from magicapply.infrastructure.persistence import (
    SqlApplicationsRepository,
    SqlJobsRepository,
    create_db,
    create_engine_from_url,
)
from magicapply.infrastructure.persistence.db import sqlite_url_for
from magicapply.infrastructure.sources import build_source
from magicapply.infrastructure.sources.base import JobSource
from magicapply.pipelines.apply import ApplyPipeline
from magicapply.pipelines.tailoring import TailoringPipeline

logger = logging.getLogger(__name__)


def build_repos(data_dir: Path) -> tuple[SqlJobsRepository, SqlApplicationsRepository]:
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine_from_url(sqlite_url_for(data_dir / "magicapply.sqlite3"))
    create_db(engine)
    return SqlJobsRepository(engine), SqlApplicationsRepository(engine)


def build_sources_for_profile(loaded: LoadedConfig, profile: Profile) -> list[JobSource]:
    by_name = {s.name: s for s in loaded.base.sources}
    out: list[JobSource] = []
    for name in profile.sources:
        cfg = by_name.get(name)
        if cfg is None:
            logger.warning("profile %r references unknown source %r; skipping", profile.name, name)
            continue
        if getattr(cfg, "enabled", True) is False:
            logger.info("source %r disabled; skipping", name)
            continue
        out.append(build_source(cfg))
    return out


def build_scorer(loaded: LoadedConfig, profile: Profile, scoring: ScoringConfig) -> JobScorer:
    llm = build_client(loaded.base.llm, prompts=loaded.prompts)
    base = _load_base_resume(loaded, profile)
    # Deterministic YAML serialization is the cacheable prompt payload the
    # scorer sends to the LLM.
    base_text = yaml.safe_dump(base.model_dump(), sort_keys=True)
    return JobScorer(
        prefilter=Prefilter(scoring),
        llm_scorer=LLMScorer(
            llm,
            base_resume_text=base_text,
            scoring_prompt=loaded.prompts.scoring,
        ),
    )


def build_tailorer(loaded: LoadedConfig, profile: Profile) -> Tailorer:
    llm = build_client(loaded.base.llm, prompts=loaded.prompts)
    return Tailorer(
        llm,
        _load_base_resume(loaded, profile),
        summary_prompt=loaded.prompts.summary,
    )


def build_narrative(loaded: LoadedConfig, profile: Profile) -> NarrativeEngine:
    llm = build_client(loaded.base.llm, prompts=loaded.prompts)
    return NarrativeEngine(
        llm,
        _load_base_resume(loaded, profile),
        style=profile.apply.narrative_style,
        cover_letter_prompt=loaded.prompts.cover_letter,
        answer_prompt=loaded.prompts.answer,
    )


def build_tailoring_pipeline(
    loaded: LoadedConfig,
    profile: Profile,
    apps_repo: SqlApplicationsRepository,
    jobs_repo: SqlJobsRepository,
) -> TailoringPipeline:
    template_path = loaded.root / "resume_template.docx"
    return TailoringPipeline(
        apps_repo=apps_repo,
        jobs_repo=jobs_repo,
        tailorer=build_tailorer(loaded, profile),
        narrative=build_narrative(loaded, profile),
        resume_renderer=DocxResumeRenderer(template_path),
        profile_name=profile.name,
        data_dir=loaded.data_dir(),
    )


def build_apply_pipeline(
    loaded: LoadedConfig,
    apps_repo: SqlApplicationsRepository,
    jobs_repo: SqlJobsRepository,
) -> ApplyPipeline:
    """Build an ApplyPipeline wired for both single-job and batch use.

    Injects a data_builder closure over ``loaded`` so ``apply_batch`` can
    construct ApplicationData per application without threading the config
    through the pipeline signature.
    """
    return ApplyPipeline(
        applications_repo=apps_repo,
        jobs_repo=jobs_repo,
        data_builder=partial(build_application_data, loaded),
    )


def build_application_data(
    loaded: LoadedConfig,
    app: Application,
    job: Job,
    *,
    dry_run: bool = False,
) -> ApplicationData:
    """Assemble the input the ATS handler needs for one Application.

    Reads the tailored resume + cover letter from disk (Phase D wrote them
    under ``app.tailored_path``) and combines them with the profile's static
    answers from ``base_config.yaml``. ``dry_run`` toggles the pre-submit
    short-circuit in the ATS template method (Phase F).
    """
    if not app.tailored_path:
        raise ValueError(f"application {app.id} has no tailored_path")

    tailored_dir = Path(app.tailored_path)
    tailored = TailoredResume.model_validate(
        yaml.safe_load((tailored_dir / "resume.yaml").read_text())
    )
    cover_text = (tailored_dir / "cover_letter.md").read_text().strip()
    return ApplicationData(
        job_url=job.url,
        static_answers=loaded.base.static_answers,
        tailored_resume=tailored,
        cover_letter=cover_text or None,
        dry_run=dry_run,
    )


def _load_base_resume(loaded: LoadedConfig, profile: Profile) -> BaseResume:
    """Load and validate the profile's base resume YAML.

    One helper serves the scorer (which wants a YAML text form) and the
    tailorer / narrative engine (which want the parsed BaseResume). The
    scorer calls yaml.safe_dump on the returned object inline in
    build_scorer — no separate `_load_base_resume_text` sibling.
    """
    resume_path = loaded.resumes_dir() / profile.base_resume
    with resume_path.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp)
    return BaseResume.model_validate(raw)
