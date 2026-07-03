"""Composition root helpers — assemble collaborators from LoadedConfig.

Deliberately no DI framework. This file is the single place where the
Pydantic config graph becomes runtime objects (repos, LLM client, sources,
scorer, tailorer). CLI commands only ever call helpers here — they don't
construct infrastructure classes themselves.
"""

from __future__ import annotations

import logging
from pathlib import Path

from magicapply.config import LoadedConfig, Profile
from magicapply.config.models import ScoringConfig
from magicapply.domain.jobs.scoring import JobScorer, LLMScorer, Prefilter
from magicapply.domain.models.resume import BaseResume
from magicapply.infrastructure.llm import build_client
from magicapply.infrastructure.persistence import (
    SqlApplicationsRepository,
    SqlJobsRepository,
    create_db,
    create_engine_from_url,
)
from magicapply.infrastructure.persistence.db import sqlite_url_for
from magicapply.infrastructure.sources import build_source
from magicapply.infrastructure.sources.base import JobSource

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
    base_text = _load_base_resume_text(loaded, profile)
    return JobScorer(
        prefilter=Prefilter(scoring),
        llm_scorer=LLMScorer(
            llm,
            base_resume_text=base_text,
            scoring_prompt=loaded.prompts.scoring,
        ),
    )


def _load_base_resume_text(loaded: LoadedConfig, profile: Profile) -> str:
    """Load the profile's base resume as YAML text (used as the cacheable prompt payload)."""
    import yaml

    resume_path = loaded.resumes_dir() / profile.base_resume
    with resume_path.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp)
    # Round-trip through BaseResume for a validated, deterministic serialization.
    resume = BaseResume.model_validate(raw)
    return yaml.safe_dump(resume.model_dump(), sort_keys=True)
