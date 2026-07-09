"""`magicapply status` and `magicapply review` — read-only reporting."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from magicapply.cli.composition import build_repos
from magicapply.config import ConfigError, load_config
from magicapply.config.paths import default_config_root
from magicapply.domain.models.application import ApplicationState

app = typer.Typer(help="Status and review.", no_args_is_help=False)
console = Console()


def _load_or_exit(root: Path | None):
    resolved = root.resolve() if root else default_config_root()
    try:
        return load_config(resolved)
    except ConfigError as exc:
        console.print(f"[red]invalid config[/red] — {exc}")
        raise typer.Exit(code=1) from exc


@app.callback(invoke_without_command=True)
def status(
    ctx: typer.Context,
    root: Annotated[Path | None, typer.Option(help="Config root")] = None,
) -> None:
    """Show a summary of recent application activity."""
    if ctx.invoked_subcommand is not None:
        return

    loaded = _load_or_exit(root)
    _jobs, apps_repo = build_repos(loaded.data_dir())

    table = Table("state", "count", "note")
    for state in ApplicationState:
        apps_in_state = apps_repo.list_by_state(state)
        total = len(apps_in_state)
        note = ""
        # The APPLIED bucket may mix real submissions and dry-runs; split them
        # so the operator can tell which is which at a glance.
        if state is ApplicationState.APPLIED and total:
            dry = sum(1 for a in apps_in_state if a.dry_run)
            real = total - dry
            note = f"real: {real}, dry_run: {dry}"
        # Keyword-alignment before/after when tailor has re-scored.
        scored = [a.score for a in apps_in_state if a.score is not None]
        after = [
            a.score_after_tailor
            for a in apps_in_state
            if a.score_after_tailor is not None
        ]
        if scored:
            avg_before = sum(scored) / len(scored)
            score_note = f"avg score: {avg_before:.0f}"
            if after:
                avg_after = sum(after) / len(after)
                pairs = [
                    (a.score, a.score_after_tailor)
                    for a in apps_in_state
                    if a.score is not None and a.score_after_tailor is not None
                ]
                if pairs:
                    avg_delta = sum(b - a for a, b in pairs) / len(pairs)
                    score_note = (
                        f"avg score: {avg_before:.0f} → {avg_after:.0f} "
                        f"(Δ {avg_delta:+.0f}, n={len(pairs)})"
                    )
                else:
                    score_note = f"avg score: {avg_before:.0f} → {avg_after:.0f}"
            note = f"{note}; {score_note}" if note else score_note
        table.add_row(state.value, str(total), note)
    console.print(table)


@app.command()
def review(
    root: Annotated[Path | None, typer.Option(help="Config root")] = None,
) -> None:
    """List failed applications and those needing intervention."""
    loaded = _load_or_exit(root)
    _jobs, apps_repo = build_repos(loaded.data_dir())

    for label, state in [
        ("NEEDS INTERVENTION", ApplicationState.NEEDS_INTERVENTION),
        ("FAILED", ApplicationState.FAILED),
    ]:
        apps = apps_repo.list_by_state(state)
        console.print(f"[bold]{label}[/bold] ({len(apps)})")
        for app in apps:
            console.print(
                f"  {app.id[:8]}  job={app.job_id[:8]}  profile={app.profile_name}  "
                f"error={app.error!r}"
            )
