"""MagicApply CLI entry point.

The composition root lives here: every command wires domain + infrastructure
together explicitly. Deliberately no DI framework — see docs/GOF_PATTERNS.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from magicapply import __version__
from magicapply.cli.commands import config as config_cmds
from magicapply.cli.commands import pipeline as pipeline_cmds
from magicapply.cli.commands import profiles as profiles_cmds
from magicapply.cli.commands import status as status_cmds

app = typer.Typer(
    name="magicapply",
    help="Config-driven, terminal-first job application automation.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(config_cmds.app, name="config")
app.add_typer(profiles_cmds.app, name="profiles")
app.add_typer(status_cmds.app, name="status")

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
def doctor() -> None:
    """Report Python, package, and cwd — a fast sanity check for the local install."""
    typer.echo(f"magicapply {__version__}")
    typer.echo(f"python     {sys.version.split()[0]} ({sys.executable})")
    typer.echo(f"package    {Path(__file__).resolve().parents[1]}")
    typer.echo(f"cwd        {Path.cwd()}")


if __name__ == "__main__":
    app()
