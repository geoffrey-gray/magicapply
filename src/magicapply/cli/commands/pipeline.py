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
) -> None:
    """Full cycle: discover + score + apply.

    Apply is not wired end-to-end yet — surface that so the user knows.
    """
    discover(profile=profile, root=root)
    console.print(
        "[yellow]note:[/yellow] apply phase is not wired in this build. "
        "See ROADMAP for Phase 9 completion work."
    )


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

    # A job may have TAILORED applications under more than one profile; find
    # them all and require a unique match.
    matches = [
        a for a in apps_repo.list_by_state(ApplicationState.TAILORED)
        if a.job_id == job_id
    ]
    if not matches:
        console.print(f"[red]no TAILORED application for job {job_id}[/red]")
        raise typer.Exit(code=1)
    if len(matches) > 1:
        profiles = sorted({a.profile_name for a in matches})
        console.print(
            f"[red]multiple TAILORED applications for job {job_id} "
            f"(profiles: {profiles}); ambiguous[/red]"
        )
        raise typer.Exit(code=1)

    application = matches[0]
    job = jobs_repo.get(application.job_id)
    if job is None:
        console.print(f"[red]job {application.job_id} missing from DB[/red]")
        raise typer.Exit(code=1)

    data = build_application_data(loaded, application, job, dry_run=no_submit)
    pipeline = build_apply_pipeline(apps_repo)

    with PlaywrightSession(headless=headless) as session:
        page = session.new_page()
        report = pipeline.apply_one(
            page=page,
            application=application,
            job=job,
            application_data=data,
        )

    console.print(f"application: {report.application_id}")
    console.print(f"final state: [bold]{report.final_state.value}[/bold]")
    if no_submit:
        console.print("[cyan]dry-run:[/cyan] submit was skipped")
    if report.error:
        console.print(f"[yellow]error:[/yellow] {report.error}")
