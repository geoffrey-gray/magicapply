"""`magicapply run` and `magicapply discover` — pipeline entry points.

`run` = discover + (apply). `discover` = discovery-only (no apply).
The apply half is left as a warning today since it requires a live Playwright
session and a real applicant identity — surface it explicitly rather than
silently doing nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from magicapply.cli.composition import (
    build_application_data,
    build_apply_pipeline,
    build_repos,
    build_scorer,
    build_sources_for_profile,
    build_tailoring_pipeline,
)
from magicapply.config import ConfigError, LoadedConfig, load_config
from magicapply.config.paths import default_config_root
from magicapply.domain.models.application import ApplicationState
from magicapply.infrastructure.browser.session import PlaywrightSession
from magicapply.pipelines.discovery import DiscoveryPipeline

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
    root: Annotated[Path | None, typer.Option(help="Config root")] = None,
) -> None:
    """Tailor every SCORED application for one profile.

    Reads SCORED applications for the profile, rewrites each resume summary
    against the job description, generates a cover letter, writes both to
    `<data_dir>/tailored/<application_id>/`, and transitions the row to
    TAILORED. Re-running is a no-op — already-TAILORED rows are not touched.
    """
    loaded = _load(root)
    profile_cfg = loaded.profile(profile)

    jobs_repo, apps_repo = build_repos(loaded.data_dir())
    pipeline = build_tailoring_pipeline(loaded, profile_cfg, apps_repo, jobs_repo)
    report = pipeline.run()

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
) -> None:
    """Full cycle: discover → tailor → apply, in one command.

    Sequences the three pipelines for a profile: DiscoveryPipeline surfaces
    and scores new jobs, TailoringPipeline rewrites the summary + generates
    a cover letter for every scored application, and ApplyPipeline.apply_batch
    drives every TAILORED application through the ATS handler in a single
    shared Chromium session. Default is dry-run (see the ``apply`` command
    for details); pass ``--yes-submit`` to actually click Submit.
    """
    loaded = _load(root)
    profile_cfg = loaded.profile(profile)

    jobs_repo, apps_repo = build_repos(loaded.data_dir())

    # --- discover ---
    sources = build_sources_for_profile(loaded, profile_cfg)
    scoring = profile_cfg.scoring or loaded.base.scoring
    scorer = build_scorer(loaded, profile_cfg, scoring)
    discover_report = DiscoveryPipeline(
        sources=sources,
        jobs_repo=jobs_repo,
        applications_repo=apps_repo,
        scorer=scorer,
        profile_name=profile_cfg.name,
        score_threshold=scoring.threshold,
    ).run()
    _render_discover(discover_report)

    # --- tailor ---
    tailor_report = build_tailoring_pipeline(
        loaded, profile_cfg, apps_repo, jobs_repo
    ).run()
    _render_tailor(tailor_report)

    # --- apply ---
    tailored_ready = apps_repo.list_by_state_and_profile(
        ApplicationState.TAILORED, profile_cfg.name
    )
    if not tailored_ready:
        console.print("[dim]no TAILORED applications; skipping apply phase[/dim]")
        return

    pipeline = build_apply_pipeline(loaded, apps_repo, jobs_repo)
    with PlaywrightSession(headless=headless) as session:
        apply_reports = pipeline.apply_batch(
            session=session,
            profile_name=profile_cfg.name,
            dry_run=no_submit,
        )
    _render_apply(apply_reports, dry_run=no_submit)


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
            help="Re-run apply against a previously FAILED application "
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

    # --retry searches FAILED applications; default searches TAILORED.
    target_state = ApplicationState.FAILED if retry else ApplicationState.TAILORED
    matches = [
        a for a in apps_repo.list_by_state(target_state)
        if a.job_id == job_id
    ]
    if not matches:
        state_word = target_state.value.upper()
        console.print(f"[red]no {state_word} application for job {job_id}[/red]")
        raise typer.Exit(code=1)
    if len(matches) > 1:
        profiles = sorted({a.profile_name for a in matches})
        console.print(
            f"[red]multiple {target_state.value.upper()} applications for job "
            f"{job_id} (profiles: {profiles}); ambiguous[/red]"
        )
        raise typer.Exit(code=1)

    application = matches[0]
    job = jobs_repo.get(application.job_id)
    if job is None:
        console.print(f"[red]job {application.job_id} missing from DB[/red]")
        raise typer.Exit(code=1)

    data = build_application_data(loaded, application, job, dry_run=no_submit)
    pipeline = build_apply_pipeline(loaded, apps_repo, jobs_repo)

    with PlaywrightSession(headless=headless) as session:
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
        if not headless and report.final_state is ApplicationState.NEEDS_INTERVENTION:
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
