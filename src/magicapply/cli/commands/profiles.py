"""`magicapply profiles list`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from magicapply.config import ConfigError, load_config
from magicapply.config.paths import default_config_root

app = typer.Typer(help="Profile commands.", no_args_is_help=True)
console = Console()


@app.command("list")
def list_profiles(
    root: Annotated[Path | None, typer.Argument(help="Config root")] = None,
) -> None:
    """List available profiles from the config root."""
    resolved = root.resolve() if root else default_config_root()
    try:
        loaded = load_config(resolved)
    except ConfigError as exc:
        console.print(f"[red]invalid[/red] — {exc}")
        raise typer.Exit(code=1) from exc

    if not loaded.profiles:
        console.print("[yellow]no profiles defined[/yellow]")
        return

    for name, profile in sorted(loaded.profiles.items()):
        sources = ", ".join(profile.sources) or "-"
        console.print(
            f"[green]{name}[/green]  base_resume={profile.base_resume}  sources={sources}"
        )
