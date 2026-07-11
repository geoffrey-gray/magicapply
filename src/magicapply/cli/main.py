"""MagicApply CLI entry point.

The composition root lives here: every command wires domain + infrastructure
together explicitly. Deliberately no DI framework — see docs/GOF_PATTERNS.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
from typing import Annotated

import typer

from magicapply import __version__
from magicapply.cli.commands import auth as auth_cmds
from magicapply.cli.commands import config as config_cmds
from magicapply.cli.commands import custom_ats as custom_ats_cmds
from magicapply.cli.commands import pipeline as pipeline_cmds
from magicapply.cli.commands import profiles as profiles_cmds
from magicapply.cli.commands import status as status_cmds
from magicapply.config import ConfigError, load_config
from magicapply.config.paths import default_config_root

app = typer.Typer(
    name="magicapply",
    help="Config-driven, terminal-first job application automation.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(config_cmds.app, name="config")
app.add_typer(custom_ats_cmds.app, name="custom-ats")
app.add_typer(profiles_cmds.app, name="profiles")
app.add_typer(status_cmds.app, name="status")
app.add_typer(auth_cmds.app, name="auth")

# Pipeline commands live at the top level per ARCHITECTURE.md §7.
app.command()(pipeline_cmds.discover)
app.command()(pipeline_cmds.tailor)
app.command()(pipeline_cmds.run)
app.command()(pipeline_cmds.apply)
app.command("review")(_review := status_cmds.review)


@app.command()
def version() -> None:
    """Print the installed MagicApply version."""
    typer.echo(__version__)


@app.command()
def doctor(
    root: Annotated[Path | None, typer.Option(help="Config root")] = None,
) -> None:
    """Report install + config + Chromium state — non-gating diagnostic."""
    typer.echo(f"magicapply {__version__}")
    typer.echo(f"python     {sys.version.split()[0]} ({sys.executable})")
    typer.echo(f"package    {Path(__file__).resolve().parents[1]}")
    typer.echo(f"cwd        {Path.cwd()}")
    typer.echo(f"chromium   {_chromium_status()}")

    resolved = root.resolve() if root else default_config_root()
    typer.echo(f"config root {resolved}")
    try:
        loaded = load_config(resolved)
    except ConfigError as exc:
        # `doctor` is a diagnostic; never gate on config validity.
        typer.echo(f"config     [invalid] {exc}")
        return

    typer.echo(f"llm provider {loaded.base.llm.provider}")
    data_dir = loaded.data_dir()
    typer.echo(f"data dir   {data_dir}")
    db_path = data_dir / "magicapply.sqlite3"
    if db_path.exists():
        typer.echo(f"db         {db_path} ({db_path.stat().st_size} bytes)")
    else:
        typer.echo(f"db         {db_path} (not created yet)")
    try:
        from magicapply.infrastructure.browser.auth_session import site_status

        for row in site_status(data_dir):
            typer.echo(
                f"auth {row['site']:<10} resolved={row['resolved']}"
            )
    except Exception:  # noqa: BLE001 — doctor never gates
        pass


def _chromium_status() -> str:
    """Report whether Playwright's Chromium is installed, without running install."""
    from magicapply.infrastructure.browser.session import find_playwright_chromium_cache

    cache = find_playwright_chromium_cache()
    if cache is not None:
        return f"PRESENT ({cache})"
    return "MISSING (run: uv run playwright install chromium)"


if __name__ == "__main__":
    app()
