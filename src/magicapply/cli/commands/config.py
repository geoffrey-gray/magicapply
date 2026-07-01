"""`magicapply config ...` subcommands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from magicapply.config import ConfigError, load_config
from magicapply.config.paths import default_config_root

app = typer.Typer(help="Configuration commands.", no_args_is_help=True)
console = Console()


@app.command()
def validate(
    root: Annotated[
        Path | None,
        typer.Argument(
            help="Config root directory. Defaults to ./configs, else the XDG config home.",
        ),
    ] = None,
) -> None:
    """Load and validate base config + all profiles under a config root."""
    resolved = root.resolve() if root else default_config_root()
    console.print(f"[dim]root:[/dim] {resolved}")

    try:
        loaded = load_config(resolved)
    except ConfigError as exc:
        console.print(f"[red]invalid[/red] — {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]ok[/green] — {len(loaded.base.sources)} source(s), "
        f"{len(loaded.profiles)} profile(s)"
    )
    for name in sorted(loaded.profiles):
        console.print(f"  profile: {name}")
