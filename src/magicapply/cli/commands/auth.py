"""`magicapply auth` — optional Playwright login-once for discovery sources."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from magicapply.config import ConfigError, load_config
from magicapply.config.paths import default_config_root
from magicapply.infrastructure.browser.auth_session import (
    clear_auth_state,
    run_interactive_login,
    site_status,
)
from magicapply.infrastructure.browser.auth_sites import get_site, list_sites

app = typer.Typer(help="Optional site login / session management.", no_args_is_help=True)
console = Console()


def _data_dir(root: Path | None) -> Path:
    resolved = root.resolve() if root else default_config_root()
    try:
        return load_config(resolved).data_dir()
    except ConfigError as exc:
        console.print(f"[red]invalid config[/red] — {exc}")
        raise typer.Exit(code=1) from exc


@app.command("sites")
def sites_cmd() -> None:
    """List registered auth sites (generic registry)."""
    table = Table("site", "login_url", "cookie_env")
    for spec in list_sites():
        table.add_row(spec.name, spec.login_url, spec.cookie_env)
    console.print(table)


@app.command("status")
def status_cmd(
    root: Annotated[Path | None, typer.Option(help="Config root")] = None,
) -> None:
    """Show which sites have storage_state or env cookies (no secret values)."""
    data_dir = _data_dir(root)
    table = Table("site", "resolved", "storage_state", "env_cookies", "legacy")
    for row in site_status(data_dir):
        table.add_row(
            str(row["site"]),
            str(row["resolved"]),
            "yes" if row["storage_state"] else "no",
            "yes" if row["env_cookies"] else "no",
            "yes" if row["legacy"] else "no",
        )
    console.print(table)
    console.print(f"[dim]auth dir: {data_dir / 'auth'}[/dim]")


@app.command("login")
def login_cmd(
    site: Annotated[str, typer.Argument(help="Site name (see `auth sites`)")],
    root: Annotated[Path | None, typer.Option(help="Config root")] = None,
    headed: Annotated[
        bool,
        typer.Option("--headed/--headless", help="Open a visible browser (default headed)"),
    ] = True,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite existing storage_state"),
    ] = False,
    auto_save: Annotated[
        bool,
        typer.Option(
            "--auto-save",
            help="Save when session cookie appears (no Enter; good for remote/agent)",
        ),
    ] = False,
    timeout: Annotated[
        int,
        typer.Option(help="Seconds to wait for login when using --auto-save"),
    ] = 600,
) -> None:
    """Open a browser, log in manually, save Playwright storage_state."""
    try:
        get_site(site)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    data_dir = _data_dir(root)
    try:
        path = run_interactive_login(
            site,
            data_dir=data_dir,
            headed=headed,
            force=force,
            auto_save=auto_save,
            timeout_seconds=timeout,
        )
    except FileExistsError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=1) from exc
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]auth login failed[/red] — {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]ok[/green] — {path}")


@app.command("clear")
def clear_cmd(
    site: Annotated[str, typer.Argument(help="Site name")],
    root: Annotated[Path | None, typer.Option(help="Config root")] = None,
) -> None:
    """Delete saved storage_state for a site (env cookies untouched)."""
    try:
        get_site(site)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    data_dir = _data_dir(root)
    if clear_auth_state(data_dir, site):
        console.print(f"[green]cleared[/green] storage_state for {site}")
    else:
        console.print(f"[dim]no storage_state for {site}[/dim]")
