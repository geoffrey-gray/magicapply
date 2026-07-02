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

from magicapply.cli.composition import build_repos, build_scorer, build_sources_for_profile
from magicapply.config import ConfigError, LoadedConfig, load_config
from magicapply.config.paths import default_config_root
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
) -> None:
    """Manually apply to a specific job."""
    console.print(
        f"[yellow]apply {job_id}[/yellow] is not implemented — "
        "requires the Playwright session stack from Phase 9 completion."
    )
    raise typer.Exit(code=1)
