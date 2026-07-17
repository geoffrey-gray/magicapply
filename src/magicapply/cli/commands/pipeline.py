"""`magicapply run` and `magicapply discover` — pipeline entry points.

`run` = discover + (apply). `discover` = discovery-only (no apply).
The apply half is left as a warning today since it requires a live Playwright
session and a real applicant identity — surface it explicitly rather than
silently doing nothing.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from magicapply.cli.composition import (
    build_application_data,
    build_apply_pipeline,
    build_repos,
    build_scorer,
    build_source_scorer,
    build_sources_for_profile,
    build_tailoring_pipeline,
)
from magicapply.config import ConfigError, LoadedConfig, load_config
from magicapply.config.paths import default_config_root
from magicapply.domain.models.application import ApplicationState
from magicapply.infrastructure.browser.apply_session import (
    apply_pending_auth_cookies,
    board_auth_site_for_job,
    configure_apply_page,
    open_apply_session,
    order_board_auth_sites,
    requires_headed_board_session,
)
from magicapply.infrastructure.browser.board_resolve import resolve_board_destination
from magicapply.infrastructure.sources.apply_url import needs_board_destination_resolve
from magicapply.pipelines.apply_types import ApplyReport
from magicapply.pipelines.apply_utils import count_outcomes
from magicapply.pipelines.discovery import DiscoveryPipeline

logger = logging.getLogger(__name__)

console = Console()

app = typer.Typer(help="Pipeline commands.", no_args_is_help=True)


def _load(root: Path | None) -> LoadedConfig:
    resolved = root.resolve() if root else default_config_root()
    try:
        return load_config(resolved)
    except ConfigError as exc:
        console.print(f"[red]invalid config[/red] — {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def discover(
    profile: Annotated[str, typer.Argument(help="Profile name from configs/profiles/")],
    root: Annotated[Path | None, typer.Option(help="Config root")] = None,
) -> None:
    """Discover + score jobs for one profile."""
    loaded = _load(root)
    profile_cfg = loaded.profile(profile)

    jobs_repo, apps_repo = build_repos(loaded.data_dir())
    sources = build_sources_for_profile(loaded, profile_cfg)
    scoring = profile_cfg.scoring or loaded.base.scoring
    scorer = build_scorer(loaded, profile_cfg, scoring)

    pipeline = DiscoveryPipeline(
        sources=sources,
        jobs_repo=jobs_repo,
        applications_repo=apps_repo,
        scorer=scorer,
        profile_name=profile_cfg.name,
        score_threshold=scoring.threshold,
        source_scorer=build_source_scorer(apps_repo),
    )
    report = pipeline.run()

    console.print(f"[green]discovered:[/green] {report.discovered}")
    console.print(f"[yellow]duplicates:[/yellow] {report.duplicates_in_run}")
    console.print(f"[dim]already seen:[/dim] {report.already_seen}")
    console.print(f"[green]scored:[/green] {report.scored}")
    console.print(f"[red]rejected:[/red] {report.rejected_by_threshold}")
    if report.source_errors:
        console.print("[red]source errors:[/red]")
        for err in report.source_errors:
            console.print(f"  - {err}")


@app.command()
def tailor(
    profile: Annotated[str, typer.Argument(help="Profile name from configs/profiles/")],
    job_id: Annotated[
        str | None,
        typer.Argument(
            help="Optional job id (16-char hash). When set, tailor only that "
            "SCORED application; otherwise tailor every SCORED row for the profile.",
        ),
    ] = None,
    root: Annotated[Path | None, typer.Option(help="Config root")] = None,
) -> None:
    """Tailor SCORED applications for one profile.

    With a ``job_id``, tailors only the chosen application (Phase 1 cadence:
    score all discovered jobs, tailor only the one you intend to apply to).
    Without ``job_id``, tailors every SCORED row for the profile.

    Writes artifacts to ``<data_dir>/tailored/<application_id>/`` and
    transitions the row to TAILORED. Re-running is a no-op for already-TAILORED
    rows.
    """
    loaded = _load(root)
    profile_cfg = loaded.profile(profile)

    jobs_repo, apps_repo = build_repos(loaded.data_dir())
    pipeline = build_tailoring_pipeline(loaded, profile_cfg, apps_repo, jobs_repo)
    report = pipeline.run(job_id=job_id)

    if job_id:
        job = jobs_repo.get(job_id)
        if job:
            console.print(f"[dim]job:[/dim] {job.title} @ {job.company}")
            console.print(f"[dim]url:[/dim] {job.url}")
    console.print(f"[green]tailored:[/green] {report.tailored}")
    if report.missing_job:
        console.print(f"[yellow]missing job:[/yellow] {report.missing_job}")
    if report.errors:
        console.print("[red]errors:[/red]")
        for err in report.errors:
            console.print(f"  - {err}")


@app.command()
def run(
    profile: Annotated[str, typer.Argument(help="Profile name")],
    root: Annotated[Path | None, typer.Option(help="Config root")] = None,
    headless: Annotated[bool, typer.Option(help="Run Chromium headless")] = True,
    no_submit: Annotated[
        bool,
        typer.Option(
            "--no-submit/--yes-submit",
            help="--no-submit (default) stops one click short of Submit for every "
            "application; --yes-submit performs real submissions.",
        ),
    ] = True,
    max_applies: Annotated[
        int | None,
        typer.Option(
            "--max-applies",
            help="Stop after this many counted apply outcomes (APPLIED / FAILED / "
            "NEEDS_INTERVENTION). Throttle denials do not count. Omit for a "
            "single one-shot pass over TAILORED apps.",
        ),
    ] = None,
    duration_hours: Annotated[
        float | None,
        typer.Option(
            "--duration-hours",
            help="Hard deadline for paced runs (hours from now).",
        ),
    ] = None,
    pace_seconds: Annotated[
        float | None,
        typer.Option(
            "--pace-seconds",
            help="Sleep this many seconds after each counted apply outcome "
            "(and as throttle backoff). Defaults to 1080 when --max-applies is set.",
        ),
    ] = None,
    skip_discover: Annotated[
        bool,
        typer.Option(
            "--skip-discover/--discover",
            help="Skip discovery and use the existing SQLite corpus "
            "(resolve → tailor → apply only). Default rediscovers.",
        ),
    ] = False,
) -> None:
    """Full cycle: discover → resolve → tailor → apply, in one command.

    Sequences DiscoveryPipeline, TailoringPipeline, and ApplyPipeline.apply_batch
    for a profile. Default is a one-shot pass. With ``--max-applies`` /
    ``--duration-hours``, the process stays alive and re-discovers / re-tailors /
    re-batches under domain throttle + optional pace sleeps — one MagicApply
    process owns the loop (no external shell apply loop).

    Pass ``--skip-discover`` when inventory already exists and you want a
    continual apply campaign without re-SERPing Indeed/LinkedIn.
    """
    loaded = _load(root)
    profile_cfg = loaded.profile(profile)
    jobs_repo, apps_repo = build_repos(loaded.data_dir())

    paced = max_applies is not None or duration_hours is not None
    if paced and pace_seconds is None:
        pace_seconds = 1080.0
    deadline: datetime | None = None
    if duration_hours is not None:
        deadline = datetime.now(UTC) + timedelta(hours=duration_hours)

    sources = build_sources_for_profile(loaded, profile_cfg)
    scoring = profile_cfg.scoring or loaded.base.scoring
    scorer = build_scorer(loaded, profile_cfg, scoring)
    pipeline = build_apply_pipeline(
        loaded, apps_repo, jobs_repo, profile=profile_cfg
    )
    all_reports: list[ApplyReport] = []

    def _resolve_scored_board_jobs() -> None:
        """Before tailor: open listings once for SCORED jobs missing apply_url."""
        scored = apps_repo.list_by_state_and_profile(
            ApplicationState.SCORED, profile_cfg.name
        )
        needing = []
        for app in scored:
            job = jobs_repo.get(app.job_id)
            if job is not None and needs_board_destination_resolve(job):
                needing.append(job)
        if not needing:
            return
        console.print(
            f"[dim]resolving board destinations for {len(needing)} SCORED job(s)[/dim]"
        )
        sites = order_board_auth_sites(
            [
                site
                for job in needing
                if (site := board_auth_site_for_job(job)) is not None
            ]
        )
        if requires_headed_board_session(sites) and headless:
            console.print("[dim]board auth session: headed (linkedin)[/dim]")
        session = open_apply_session(
            headless=headless,
            data_dir=loaded.data_dir(),
            prefer_site=sites[0] if sites else None,
            also_sites=sites[1:],
        )
        with session:
            apply_pending_auth_cookies(session)
            page = session.new_page()
            configure_apply_page(page)
            for job in needing:
                updated, changed = resolve_board_destination(page, job)
                if changed:
                    jobs_repo.save(updated)
                    console.print(
                        f"[dim]resolved[/dim] {job.id[:8]} → "
                        f"{updated.apply_url or 'board-only'}"
                    )

    def _discover_and_tailor() -> None:
        if not skip_discover:
            discover_report = DiscoveryPipeline(
                sources=sources,
                jobs_repo=jobs_repo,
                applications_repo=apps_repo,
                scorer=scorer,
                profile_name=profile_cfg.name,
                score_threshold=scoring.threshold,
                source_scorer=build_source_scorer(apps_repo),
            ).run()
            _render_discover(discover_report)
        else:
            console.print("[dim]skip-discover: using existing corpus[/dim]")
        _resolve_scored_board_jobs()
        tailor_report = build_tailoring_pipeline(
            loaded, profile_cfg, apps_repo, jobs_repo
        ).run()
        _render_tailor(tailor_report)

    def _open_apply_session_for_batch():
        # Mixed Indeed+LinkedIn batches need both storage_states in one context.
        # Prefer LinkedIn as primary (auth-sensitive); merge Indeed cookies in.
        candidates = (
            apps_repo.list_by_state_and_profile(
                ApplicationState.TAILORED, profile_cfg.name
            )
            + apps_repo.list_by_state_and_profile(
                ApplicationState.FAILED, profile_cfg.name
            )
            + apps_repo.list_by_state_and_profile(
                ApplicationState.NEEDS_INTERVENTION, profile_cfg.name
            )
        )
        sites: list[str] = []
        for app in candidates:
            job = jobs_repo.get(app.job_id)
            if job is None:
                continue
            site = board_auth_site_for_job(job)
            if site is not None and site not in sites:
                sites.append(site)
        ordered = order_board_auth_sites(sites)
        if requires_headed_board_session(ordered) and headless:
            console.print("[dim]board auth session: headed (linkedin)[/dim]")
        return open_apply_session(
            headless=headless,
            data_dir=loaded.data_dir(),
            prefer_site=ordered[0] if ordered else None,
            also_sites=ordered[1:],
        )

    if not paced:
        _discover_and_tailor()
        tailored_ready = apps_repo.list_by_state_and_profile(
            ApplicationState.TAILORED, profile_cfg.name
        )
        if not tailored_ready:
            console.print("[dim]no TAILORED applications; skipping apply phase[/dim]")
            return
        session = _open_apply_session_for_batch()
        with session:
            apply_pending_auth_cookies(session)
            apply_reports = pipeline.apply_batch(
                session=session,
                profile_name=profile_cfg.name,
                dry_run=no_submit,
            )
        _render_apply(apply_reports, dry_run=no_submit)
        return

    console.print(
        f"[cyan]paced run[/cyan] max_applies={max_applies} "
        f"duration_hours={duration_hours} pace_seconds={pace_seconds} "
        f"skip_discover={skip_discover}"
    )
    empty_waves = 0
    while True:
        if deadline is not None and datetime.now(UTC) >= deadline:
            console.print("[yellow]duration deadline reached[/yellow]")
            break
        counted = count_outcomes(all_reports)
        if max_applies is not None and counted >= max_applies:
            console.print(f"[green]max applies reached:[/green] {counted}")
            break

        try:
            _discover_and_tailor()
        except Exception as exc:  # noqa: BLE001 — continue paced run
            logger.warning("discover/tailor failed (continue): %s", exc)
            console.print(f"[yellow]discover/tailor error (continue):[/yellow] {exc}")

        remaining = None
        if max_applies is not None:
            remaining = max(0, max_applies - count_outcomes(all_reports))
            if remaining == 0:
                break

        candidates = (
            apps_repo.list_by_state_and_profile(
                ApplicationState.TAILORED, profile_cfg.name
            )
            + apps_repo.list_by_state_and_profile(
                ApplicationState.FAILED, profile_cfg.name
            )
            + apps_repo.list_by_state_and_profile(
                ApplicationState.NEEDS_INTERVENTION, profile_cfg.name
            )
        )
        if not candidates:
            empty_waves += 1
            console.print(
                f"[dim]no eligible applications (wave empty #{empty_waves})[/dim]"
            )
            if empty_waves >= 3 and (pace_seconds or 0) > 0:
                wait = float(pace_seconds or 1080)
                if deadline is not None:
                    wait = min(
                        wait, max(0.0, (deadline - datetime.now(UTC)).total_seconds())
                    )
                if wait <= 0:
                    break
                console.print(f"[dim]sleeping {wait:.0f}s before rediscover[/dim]")
                time.sleep(wait)
            elif empty_waves >= 5:
                console.print("[yellow]giving up: no eligible applications[/yellow]")
                break
            continue

        empty_waves = 0
        try:
            session = _open_apply_session_for_batch()
            with session:
                apply_pending_auth_cookies(session)
                wave_reports = pipeline.apply_batch(
                    session=session,
                    profile_name=profile_cfg.name,
                    dry_run=no_submit,
                    max_outcomes=remaining,
                    deadline=deadline,
                    pace_seconds=pace_seconds,
                    include_retry_states=True,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("apply session failed (continue): %s", exc)
            console.print(f"[yellow]apply session error (continue):[/yellow] {exc}")
            wave_reports = []
            if pace_seconds:
                time.sleep(min(60.0, float(pace_seconds)))

        all_reports.extend(wave_reports)
        _render_apply(wave_reports, dry_run=no_submit)
        console.print(
            f"[dim]campaign outcomes so far: {count_outcomes(all_reports)}"
            f"{f'/{max_applies}' if max_applies is not None else ''}[/dim]"
        )

        if max_applies is not None and count_outcomes(all_reports) >= max_applies:
            break
        if deadline is not None and datetime.now(UTC) >= deadline:
            break
        # Between waves, if we returned early with remaining quota, rediscover.
        if not wave_reports and pace_seconds:
            time.sleep(min(60.0, float(pace_seconds)))

    _render_apply(all_reports, dry_run=no_submit)
    console.print(
        f"[bold]paced run finished[/bold] counted_outcomes={count_outcomes(all_reports)}"
    )


def _render_discover(report) -> None:
    console.print(f"[green]discovered:[/green] {report.discovered}")
    console.print(f"[yellow]duplicates:[/yellow] {report.duplicates_in_run}")
    console.print(f"[dim]already seen:[/dim] {report.already_seen}")
    console.print(f"[green]scored:[/green] {report.scored}")
    console.print(f"[red]rejected:[/red] {report.rejected_by_threshold}")
    if report.source_errors:
        console.print("[red]source errors:[/red]")
        for err in report.source_errors:
            console.print(f"  - {err}")


def _render_tailor(report) -> None:
    console.print(f"[green]tailored:[/green] {report.tailored}")
    if report.missing_job:
        console.print(f"[yellow]missing job:[/yellow] {report.missing_job}")
    if report.errors:
        console.print("[red]tailor errors:[/red]")
        for err in report.errors:
            console.print(f"  - {err}")


def _render_apply(reports, *, dry_run: bool) -> None:
    if not reports:
        console.print("[dim]apply: no reports[/dim]")
        return
    applied = sum(1 for r in reports if r.final_state is ApplicationState.APPLIED)
    intervention = sum(
        1 for r in reports if r.final_state is ApplicationState.NEEDS_INTERVENTION
    )
    failed = sum(1 for r in reports if r.final_state is ApplicationState.FAILED)
    console.print(f"[green]applied:[/green] {applied}", end="")
    if dry_run:
        console.print("  [cyan](dry-run)[/cyan]")
    else:
        console.print()
    if intervention:
        console.print(f"[yellow]needs intervention:[/yellow] {intervention}")
    if failed:
        console.print(f"[red]failed:[/red] {failed}")


@app.command()
def apply(
    job_id: Annotated[str, typer.Argument(help="Job id (16-char hash)")],
    root: Annotated[Path | None, typer.Option(help="Config root")] = None,
    headless: Annotated[bool, typer.Option(help="Run Chromium headless")] = True,
    no_submit: Annotated[
        bool,
        typer.Option(
            "--no-submit/--yes-submit",
            help="--no-submit (default) stops one click short of Submit; "
            "--yes-submit performs a real submission.",
        ),
    ] = True,
    retry: Annotated[
        bool,
        typer.Option(
            "--retry",
            help="Re-run apply after FAILED or NEEDS_INTERVENTION "
            "(re-executes the ATS handler on the same tailored artifacts).",
        ),
    ] = False,
) -> None:
    """Apply to one job. Requires the application to be in TAILORED state.

    Default is a dry-run: the flow navigates and fills every field but does
    not click Submit. The Application still transitions to APPLIED so the
    downstream tracking works uniformly; ``dry_run=True`` on the row lets
    ``magicapply status`` split real submissions from dry runs. Pass
    ``--yes-submit`` to actually submit.
    """
    loaded = _load(root)
    jobs_repo, apps_repo = build_repos(loaded.data_dir())

    if retry:
        retry_states = {
            ApplicationState.FAILED,
            ApplicationState.NEEDS_INTERVENTION,
            ApplicationState.APPLYING,
            ApplicationState.APPLIED,
        }
        matches = [
            a
            for a in apps_repo.list_by_state(ApplicationState.FAILED)
            + apps_repo.list_by_state(ApplicationState.NEEDS_INTERVENTION)
            + apps_repo.list_by_state(ApplicationState.APPLYING)
            + apps_repo.list_by_state(ApplicationState.APPLIED)
            if a.job_id == job_id
            and a.state in retry_states
            and (a.state is not ApplicationState.APPLIED or a.dry_run)
        ]
    else:
        matches = [
            a
            for a in apps_repo.list_by_state(ApplicationState.TAILORED)
            if a.job_id == job_id
        ]
    if not matches:
        if retry:
            console.print(
                f"[red]no FAILED, NEEDS_INTERVENTION, APPLYING, or APPLIED "
                f"application for job {job_id}[/red]"
            )
        else:
            console.print(f"[red]no TAILORED application for job {job_id}[/red]")
        raise typer.Exit(code=1)
    if len(matches) > 1:
        profiles = sorted({a.profile_name for a in matches})
        console.print(
            f"[red]multiple applications for job {job_id} "
            f"(profiles: {profiles}); ambiguous[/red]"
        )
        raise typer.Exit(code=1)

    application = matches[0]
    job = jobs_repo.get(application.job_id)
    if job is None:
        console.print(f"[red]job {application.job_id} missing from DB[/red]")
        raise typer.Exit(code=1)

    console.print(f"[dim]job:[/dim] {job.title} @ {job.company}")
    console.print(f"[dim]url:[/dim] {job.url}")
    if job.apply_url:
        console.print(f"[dim]apply_url:[/dim] {job.apply_url}")
    console.print(f"[dim]profile:[/dim] {application.profile_name}")

    data = build_application_data(loaded, application, job, dry_run=no_submit)
    profile_cfg = loaded.profile(application.profile_name)
    pipeline = build_apply_pipeline(
        loaded, apps_repo, jobs_repo, profile=profile_cfg
    )

    session = open_apply_session(
        headless=headless,
        data_dir=loaded.data_dir(),
        job=job,
    )
    # Intervention prompts only when the operator asked for a visible browser.
    session_headed = not bool(getattr(session, "_headless", headless))
    with session:
        apply_pending_auth_cookies(session)
        page = session.new_page()
        report = pipeline.apply_one(
            page=page,
            application=application,
            job=job,
            application_data=data,
            retry=retry,
        )
        # Interactive intervention: when the browser is visible (--no-
        # headless) and the flow bailed for CAPTCHA / manual review, keep
        # the session open and let the operator finish the application
        # by hand, then record what happened.
        if session_headed and report.final_state is ApplicationState.NEEDS_INTERVENTION:
            report = _run_manual_intervention(page, apps_repo, application, report)

    console.print(f"application: {report.application_id}")
    console.print(f"final state: [bold]{report.final_state.value}[/bold]")
    if no_submit:
        console.print("[cyan]dry-run:[/cyan] submit was skipped")
    if report.error:
        console.print(f"[yellow]error:[/yellow] {report.error}")


def _run_manual_intervention(
    page,  # type: ignore[no-untyped-def]
    apps_repo,  # type: ignore[no-untyped-def]
    application,  # type: ignore[no-untyped-def]
    report,  # type: ignore[no-untyped-def]
):
    """Prompt the operator to complete the application manually and record
    the outcome. Called only when the browser is visible and the handler
    surfaced NEEDS_INTERVENTION (CAPTCHA on load or before submit, per
    BaseATSHandler.apply).
    """
    from magicapply.pipelines.apply import ApplyReport

    current_url = getattr(page, "url", "?")
    console.print(f"[yellow]manual intervention needed[/yellow] at {current_url}")
    console.print("Complete the application in the browser window, then press Enter.")
    try:
        input()
    except EOFError:
        # Non-interactive stdin (e.g., piped) — treat as skip.
        console.print("[dim]no input available; leaving state as NEEDS_INTERVENTION[/dim]")
        return report

    outcome = typer.prompt(
        "Outcome? [y=applied / n=failed / s=skip]",
        default="s",
    ).strip().lower()

    from magicapply.domain.models.application import ApplicationState

    if outcome.startswith("y"):
        target = ApplicationState.APPLIED
        reason = "manual submit confirmed"
    elif outcome.startswith("n"):
        target = ApplicationState.FAILED
        reason = "manual attempt failed"
    else:
        target = ApplicationState.SKIPPED
        reason = "manual intervention skipped"

    # Re-load in case save() mid-transition below wants the fresh row;
    # then transition and persist.
    fresh = apps_repo.get(application.id) or application
    fresh.transition_to(target, reason=reason)
    apps_repo.save(fresh)
    return ApplyReport(fresh.id, fresh.state, reason)
