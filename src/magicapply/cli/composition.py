"""Composition root helpers — assemble collaborators from LoadedConfig.

Deliberately no DI framework. This file is the single place where the
Pydantic config graph becomes runtime objects (repos, LLM client, sources,
scorer, tailorer). CLI commands only ever call helpers here — they don't
construct infrastructure classes themselves.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path

import yaml

from magicapply.config import LoadedConfig, Profile
from magicapply.config.models import ScoringConfig
from magicapply.domain.jobs.scoring import (
    JobScorer,
    KeywordAlignmentScorer,
    LLMScorer,
    Prefilter,
)
from magicapply.domain.keywords.alignment import serialize_resume_text
from magicapply.domain.models.application import Application
from magicapply.domain.models.job import Job
from magicapply.domain.models.resume import BaseResume, TailoredResume
from magicapply.domain.resumes.narrative import NarrativeEngine
from magicapply.domain.resumes.tailor import Tailorer
from magicapply.infrastructure.browser.ats.answer_router import AnswerRouter
from magicapply.infrastructure.browser.ats.base import ApplicationData
from magicapply.infrastructure.llm import build_client
from magicapply.infrastructure.rendering.docx_inplace import InPlaceDocxTailorer
from magicapply.infrastructure.persistence import (
    SqlApplicationsRepository,
    SqlJobsRepository,
    create_db,
    create_engine_from_url,
)
from magicapply.infrastructure.persistence.db import sqlite_url_for
from magicapply.infrastructure.sources import build_source
from magicapply.infrastructure.sources.base import JobSource
from magicapply.infrastructure.sources.factory import build_proxy_pool
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
    # Build the pool once and share it across all Cloudflare-adjacent
    # adapters. `build_proxy_pool` returns None when the pool is disabled
    # in config — adapters then fall back to direct fetches.
    proxy_pool = build_proxy_pool(loaded.base.proxies)
    out: list[JobSource] = []
    for name in profile.sources:
        cfg = by_name.get(name)
        if cfg is None:
            logger.warning("profile %r references unknown source %r; skipping", profile.name, name)
            continue
        if getattr(cfg, "enabled", True) is False:
            logger.info("source %r disabled; skipping", name)
            continue
        out.append(
            build_source(
                cfg,
                proxy_pool=proxy_pool,
                data_dir=loaded.data_dir(),
            )
        )
    return out


def build_source_scorer(
    apps_repo: SqlApplicationsRepository,
) -> Callable[[str], int]:
    """Return a callable `source_name -> recent apply count` used by
    `dedupe_by_key` in `DiscoveryPipeline` to load-balance cross-source
    duplicates. Window fixed at 24h; that's short enough that stale
    activity doesn't dominate and long enough to smooth per-run bursts."""
    from datetime import UTC, datetime, timedelta

    def scorer(source_name: str) -> int:
        since = datetime.now(UTC) - timedelta(hours=24)
        return apps_repo.count_applied_by_source_in_window(source_name, since)

    return scorer


def build_scorer(loaded: LoadedConfig, profile: Profile, scoring: ScoringConfig) -> JobScorer:
    base = _load_base_resume(loaded, profile)
    prefilter = Prefilter(scoring)
    if scoring.mode == "keyword":
        # YAKE extracts keyphrases from the real JD (no LLM). Score =
        # fraction of those terms present on the resume. Prefer DOCX body.
        from magicapply.domain.keywords.alignment import docx_plain_text
        from magicapply.domain.keywords.yake_extractor import YakeKeywordExtractor

        resume_text = serialize_resume_text(base)
        if base.source_docx_path is not None and base.source_docx_path.is_file():
            resume_text = docx_plain_text(base.source_docx_path)
        return JobScorer(
            prefilter=prefilter,
            fit_scorer=KeywordAlignmentScorer(
                resume_text=resume_text,
                extractor=YakeKeywordExtractor(top=20, max_ngram_size=2),
                bank=loaded.effective_bank(profile),
            ),
        )
    # mode == "llm" — prompt-based score via configured provider.
    llm = build_client(loaded.base.llm, prompts=loaded.prompts)
    base_text = yaml.safe_dump(base.model_dump(mode="json"), sort_keys=True)
    return JobScorer(
        prefilter=prefilter,
        fit_scorer=LLMScorer(
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
        bullet_prompt=loaded.prompts.bullet_rewrite,
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
    base = _load_base_resume(loaded, profile)
    if base.source_docx_path is None:
        raise ValueError(
            f"resume {profile.base_resume!r} has no source_docx_path — "
            f"the Phase 1 in-place DOCX tailorer requires the operator's "
            f"original .docx file. Add `source_docx_path: <path>` to the "
            f"resume YAML."
        )
    # Same YAKE extractor as discover scoring so tailor bank-matching and
    # post-tailor alignment use real JD keyphrases, not mock LLM cans.
    from magicapply.domain.keywords.yake_extractor import YakeKeywordExtractor

    return TailoringPipeline(
        apps_repo=apps_repo,
        jobs_repo=jobs_repo,
        tailorer=build_tailorer(loaded, profile),
        narrative=build_narrative(loaded, profile),
        resume_renderer=InPlaceDocxTailorer(),
        keyword_extractor=YakeKeywordExtractor(top=20, max_ngram_size=2),
        keyword_bank=loaded.effective_bank(profile),
        source_docx_path=base.source_docx_path,
        profile_name=profile.name,
        data_dir=loaded.data_dir(),
        # Phase 1 defers cover letter generation per final_dod_plan.md W.2.
        # Phase 2 flips this to True once the real LLM is layered in.
        generate_cover_letter=False,
    )


def build_apply_pipeline(
    loaded: LoadedConfig,
    apps_repo: SqlApplicationsRepository,
    jobs_repo: SqlJobsRepository,
) -> ApplyPipeline:
    """Build an ApplyPipeline wired for both single-job and batch use.

    Injects a data_builder closure over ``loaded`` so ``apply_batch`` can
    construct ApplicationData per application without threading the config
    through the pipeline signature. Also wires the `ApplyThrottle` from
    the operator's config.
    """
    throttle = _build_apply_throttle(loaded, apps_repo)
    return ApplyPipeline(
        applications_repo=apps_repo,
        jobs_repo=jobs_repo,
        data_builder=partial(build_application_data, loaded),
        throttle=throttle,
    )


def _build_apply_throttle(
    loaded: LoadedConfig,
    apps_repo: SqlApplicationsRepository,
) -> "ApplyThrottle":
    from magicapply.domain.apply.throttle import ApplyThrottle
    from magicapply.pipelines.apply import ats_key_for_url

    return ApplyThrottle(
        config=loaded.base.apply_throttle,
        ats_hourly_count=lambda ats, since: apps_repo.count_applied_in_window(
            ats_key_for_url, ats, since
        ),
        ats_daily_count=lambda ats, since: apps_repo.count_applied_in_window(
            ats_key_for_url, ats, since
        ),
        global_hourly_count=apps_repo.count_applied_all_in_window,
        global_daily_count=apps_repo.count_applied_all_in_window,
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
    short-circuit in the ATS template method (Phase F). Also attaches an
    AnswerRouter (Phase L) so handlers can discover and fill per-role
    custom fields (screening questions, DEI, yes/no) without a hardcoded
    selector list per employer.
    """
    if not app.tailored_path:
        raise ValueError(f"application {app.id} has no tailored_path")

    tailored_dir = Path(app.tailored_path)
    tailored = TailoredResume.model_validate(
        yaml.safe_load((tailored_dir / "resume.yaml").read_text())
    )
    # Phase 1 (W.2): cover letter is optional — the tailoring pipeline
    # skips writing cover_letter.md unless generate_cover_letter=True. If
    # the file is absent, the ATS handlers' cover-letter fill is a no-op.
    cover_letter_path = tailored_dir / "cover_letter.md"
    cover_text = (
        cover_letter_path.read_text().strip()
        if cover_letter_path.exists()
        else ""
    )
    resume_docx = tailored_dir / "resume.docx"
    if not resume_docx.exists():
        raise FileNotFoundError(
            f"application {app.id}: rendered DOCX missing at {resume_docx} — "
            f"re-run `magicapply tailor` to regenerate"
        )
    profile = loaded.profile(app.profile_name)
    data_dir = loaded.data_dir()
    workday_store = None
    static_answers = loaded.base.static_answers
    # Phone / current employer from base resume when static_answers omit them.
    from magicapply.domain.resumes.static_overlay import overlay_static_from_resume

    base_resume = _load_base_resume(loaded, profile)
    static_answers = overlay_static_from_resume(static_answers, base_resume)
    from magicapply.infrastructure.sources.apply_url import (
        job_with_resolved_apply_url,
        resolve_job_apply_destination,
    )

    job = job_with_resolved_apply_url(job)
    apply_target = resolve_job_apply_destination(job)
    if (
        "myworkdayjobs.com" in apply_target.lower()
        or ".myworkday.com" in apply_target.lower()
    ):
        from magicapply.infrastructure.browser.ats.workday_accounts import (
            WorkdayAccountStore,
        )

        workday_store = WorkdayAccountStore(WorkdayAccountStore.default_path(data_dir))
        tenant = WorkdayAccountStore.tenant_from_url(apply_target)
        stored = workday_store.get(tenant)
        effective_password = (
            stored.password
            if stored
            else static_answers.workday_apply_password
        )
        if effective_password:
            static_answers = static_answers.model_copy(
                update={"workday_apply_password": effective_password}
            )
    router = AnswerRouter(
        static_answers=static_answers,
        narrative=build_narrative(loaded, profile),
        resume_docx_path=resume_docx,
        answer_library=loaded.answer_library,
        router_rules=loaded.router_rules,
    )
    # Determine ATS for timeout config
    from magicapply.pipelines.apply import ats_key_for_url
    ats = ats_key_for_url(apply_target) or "unknown"
    timeouts = loaded.base.ats_timeouts.get_for_ats(ats)

    data = ApplicationData(
        job_url=apply_target,
        static_answers=static_answers,
        tailored_resume=tailored,
        resume_docx_path=resume_docx,
        cover_letter=cover_text or None,
        dry_run=dry_run,
        answer_router=router,
        job=job,
        data_dir=data_dir,
        workday_account_store=workday_store,
        ats_timeouts=timeouts,
    )
    from magicapply.infrastructure.browser.forms.composer import composer_from_registry
    from magicapply.infrastructure.browser.forms.registry import build_driver_registry

    narrative = build_narrative(loaded, profile)
    registry = build_driver_registry(
        router,
        narrative,
        loaded.base.form_drivers,
    )
    return data.model_copy(
        update={"form_composer": composer_from_registry(registry, data)}
    )


def _load_base_resume(loaded: LoadedConfig, profile: Profile) -> BaseResume:
    """Load and validate the profile's base resume YAML.

    One helper serves the scorer (which wants a YAML text form) and the
    tailorer / narrative engine (which want the parsed BaseResume). The
    scorer calls yaml.safe_dump on the returned object inline in
    build_scorer — no separate `_load_base_resume_text` sibling.

    Relative ``source_docx_path`` values resolve against ``resumes_dir``
    (same directory as the resume YAML), so operators can write
    ``source_docx_path: my_resume.docx`` next to the YAML.
    """
    resume_path = loaded.resumes_dir() / profile.base_resume
    with resume_path.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp)
    base = BaseResume.model_validate(raw)
    if base.source_docx_path is not None and not base.source_docx_path.is_absolute():
        base = base.model_copy(
            update={
                "source_docx_path": (
                    loaded.resumes_dir() / base.source_docx_path
                ).resolve()
            }
        )
    return base
