"""`magicapply custom-ats` — custom (non–big-4) ATS corpus reporting.

Reads the versioned manifest at ``tests/corpus/custom_ats_manifest.yaml`` and
reports pass/fail/pending/skipped counts by platform and source, the current
offline pass rate against the 80% gate, and top unhandled screening-question
labels aggregated from ``data/answer_proposals.yaml``.

See `plan.md` §6 for the full 80% gate spec.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from magicapply.config import ConfigError, load_config
from magicapply.config.paths import default_config_root
from magicapply.infrastructure.corpus.custom_ats import load_manifest
from magicapply.infrastructure.sources.apply_url import (
    is_big_four_platform,
    sniff_platform,
)

app = typer.Typer(help="Custom ATS corpus reporting.", no_args_is_help=True)
console = Console()

DEFAULT_MANIFEST = Path("tests/corpus/custom_ats_manifest.yaml")
PASS_RATE_THRESHOLD = 0.80
STATUSES = ("pass", "fail", "pending", "skipped")


def _offline_eligible(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entries with a ``capture_dir`` that resolves to an existing directory."""
    eligible = []
    for entry in entries:
        capture_dir = entry.get("capture_dir")
        if capture_dir and Path(capture_dir).is_dir():
            eligible.append(entry)
    return eligible


def _counts_by(entries: list[dict[str, Any]], key: str) -> dict[str, Counter]:
    """Return ``{key_value: Counter(status: n)}`` across every entry."""
    grouped: dict[str, Counter] = {}
    for entry in entries:
        bucket = str(entry.get(key) or "-")
        status = str(entry.get("status") or "pending")
        grouped.setdefault(bucket, Counter())[status] += 1
    return grouped


def _print_status_table(title: str, grouped: dict[str, Counter]) -> None:
    table = Table(title=title)
    table.add_column(title.split(" ", 1)[-1])
    for status in STATUSES:
        table.add_column(status, justify="right")
    table.add_column("total", justify="right")
    for bucket in sorted(grouped):
        counts = grouped[bucket]
        row = [bucket] + [str(counts.get(s, 0)) for s in STATUSES]
        row.append(str(sum(counts.values())))
        table.add_row(*row)
    console.print(table)


def _top_required_unhandled(
    observed_forms_dir: Path, limit: int
) -> list[tuple[str, int]]:
    """Return the top-N *required* + unhandled labels across custom-ATS captures.

    Reads ``data/observed_forms/*/form.yaml`` — that stream has both
    ``resolved_strategy`` and ``required`` per field, which
    ``answer_proposals.yaml`` does not. Non-required unhandled fields are
    interesting to nobody (soft gaps); required unhandled fields block real
    dry-runs. Big-four ATS captures are filtered out via ``sniff_platform``
    on the recorded ``job_url`` so the count reflects the custom-stack
    surface the 80% gate covers.
    """
    if not observed_forms_dir.is_dir():
        return []
    labels: Counter[str] = Counter()
    for form_path in observed_forms_dir.glob("*/form.yaml"):
        try:
            payload = yaml.safe_load(form_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        job_url = str(payload.get("job_url") or "")
        if job_url and is_big_four_platform(sniff_platform(job_url)):
            continue
        for field in payload.get("fields") or []:
            if not isinstance(field, dict):
                continue
            if field.get("resolved_strategy") != "unhandled":
                continue
            if not field.get("required"):
                continue
            label = str(field.get("label") or "").strip()
            if label:
                labels[label] += 1
    return labels.most_common(limit)


@app.command("report")
def report(
    root: Annotated[Path | None, typer.Option(help="Config root")] = None,
    manifest: Annotated[
        Path,
        typer.Option(help="Path to custom_ats_manifest.yaml"),
    ] = DEFAULT_MANIFEST,
    top: Annotated[
        int, typer.Option(help="Number of top unhandled labels to show")
    ] = 10,
) -> None:
    """Report pass/fail/skip by platform and source; exit 1 if pass rate < 80%."""
    manifest_path = manifest.resolve()
    if not manifest_path.exists():
        console.print(f"[red]manifest not found[/red]: {manifest_path}")
        raise typer.Exit(code=1)

    entries = load_manifest(manifest_path)
    console.print(f"[bold]custom ATS corpus[/bold] — {manifest_path}")
    console.print(f"entries: {len(entries)}")

    _print_status_table("by platform", _counts_by(entries, "platform"))
    _print_status_table("by source", _counts_by(entries, "source"))

    eligible = _offline_eligible(entries)
    if not eligible:
        console.print("[yellow]no offline-eligible entries (no capture_dir on disk)[/yellow]")
        raise typer.Exit(code=1)

    passes = sum(1 for e in eligible if e.get("status") == "pass")
    fails = sum(1 for e in eligible if e.get("status") == "fail")
    pass_rate = passes / len(eligible)
    gate_ok = pass_rate >= PASS_RATE_THRESHOLD
    tag = "[green]OK[/green]" if gate_ok else "[red]BELOW GATE[/red]"
    console.print(
        f"offline pass rate: [bold]{pass_rate:.0%}[/bold] "
        f"({passes} pass / {fails} fail / {len(eligible)} eligible) "
        f"— gate {PASS_RATE_THRESHOLD:.0%} {tag}"
    )

    # Best-effort unhandled aggregation. Requires a resolvable config root
    # only for the data_dir; missing config is not fatal for the report.
    resolved_root = root.resolve() if root else default_config_root()
    try:
        loaded = load_config(resolved_root)
    except ConfigError:
        loaded = None
    if loaded is not None:
        observed_dir = loaded.data_dir() / "observed_forms"
        top_unhandled = _top_required_unhandled(observed_dir, top)
        if top_unhandled:
            table = Table(
                title=f"top {len(top_unhandled)} required unhandled labels (custom ATS)"
            )
            table.add_column("count", justify="right")
            table.add_column("label")
            for label, count in top_unhandled:
                table.add_row(str(count), label)
            console.print(table)

    if not gate_ok:
        raise typer.Exit(code=1)
