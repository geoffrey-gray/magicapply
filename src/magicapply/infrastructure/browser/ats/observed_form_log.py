"""Persist a per-form observation record so the operator can review + grow
the answer library from real employer runs.

Called from the Template Method (``BaseATSHandler.apply``) after
``_fill_dynamic`` — see ``final_dod_plan.md`` W.3 and the "extend, don't
multiply" GoF note. Handlers accumulate resolutions into
``ApplicationData.resolutions_log``; the template calls this once so the
observation is a template-level invariant, not a per-handler duplication.

Every field that resolves via ``narrative`` OR ``unhandled`` also gets
appended to ``data/answer_proposals.yaml`` so the operator can promote
tentative answers into ``configs/answer_library.yaml``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

from typing import TYPE_CHECKING

from magicapply.infrastructure.browser.ats.answer_router import ResolvedAnswer
from magicapply.infrastructure.browser.ats.form_scan import FormField

if TYPE_CHECKING:
    from magicapply.infrastructure.browser.ats.base import PageDriver

logger = logging.getLogger(__name__)


@dataclass
class ResolvedField:
    """One (form field, resolution) pair captured during ``_fill_dynamic``.

    Accumulates on ``ApplicationData.resolutions_log`` so the Template
    Method can persist the full observation in one shot after each
    handler run.
    """

    field: FormField
    answer: ResolvedAnswer


# Values from identity fields we do not want persisted in cleartext.
_REDACTED_STRATEGIES = {"static"}


def log_observed_form(
    *,
    app_id: str,
    job_url: str,
    resolutions: list[ResolvedField],
    data_dir: Path,
    page: PageDriver | None = None,
) -> Path | None:
    """Write the per-form observation to disk. Returns the file path.

    Also appends narrative + unhandled resolutions to
    ``<data_dir>/answer_proposals.yaml`` so the operator can review
    later.

    Returns ``None`` and logs a warning on any write failure — the
    observation log is best-effort; it must never crash the apply flow.
    """
    try:
        ts = datetime.now(timezone.utc)
        slug = _url_slug(job_url)
        out_dir = data_dir / "observed_forms" / f"{ts.strftime('%Y%m%d-%H%M%S')}-{slug}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "form.yaml"

        payload = {
            "app_id": app_id,
            "job_url": job_url,
            "timestamp": ts.isoformat(),
            "fields": [_field_dict(rf) for rf in resolutions],
        }
        out_path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        )

        if page is not None:
            _capture_page_artifacts(page, out_dir)

        _append_proposals(data_dir, job_url, resolutions)
        return out_path
    except Exception as exc:  # noqa: BLE001
        logger.warning("observation log write failed: %s", exc)
        return None


def _capture_page_artifacts(page: PageDriver, out_dir: Path) -> None:
    """Persist DOM + screenshot at the pre-submit observation moment (W.4)."""
    try:
        (out_dir / "dom.html").write_text(page.content(), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("dom capture failed: %s", exc)
    screenshot = getattr(page, "screenshot", None)
    if callable(screenshot):
        try:
            screenshot(path=str(out_dir / "screenshot.png"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("screenshot capture failed: %s", exc)


def _field_dict(rf: ResolvedField) -> dict:
    resolved_value = rf.answer.value
    if rf.answer.strategy in _REDACTED_STRATEGIES:
        resolved_value = "<REDACTED>"
    out: dict = {
        "label": rf.field.label,
        "kind": rf.field.kind,
        "selector": rf.field.selector,
        "options": list(rf.field.options) if rf.field.options else [],
        "required": rf.field.required,
        "resolved_strategy": rf.answer.strategy,
        "resolved_value": resolved_value,
    }
    if rf.field.variant:
        out["variant"] = rf.field.variant
    if rf.field.step_id:
        out["step_id"] = rf.field.step_id
    if rf.field.widget_id:
        out["widget_id"] = rf.field.widget_id
    return out


def _url_slug(job_url: str) -> str:
    try:
        parsed = urlparse(job_url)
        host = (parsed.netloc or "unknown").replace(":", "_")
        # Take the last two path segments as a rough identifier
        # ("<company>/<job-id>" is the shape at every ATS we support).
        parts = [p for p in parsed.path.split("/") if p][-2:]
        tail = "-".join(parts) if parts else "root"
        return f"{host}-{tail}"[:80]
    except Exception:  # noqa: BLE001
        return "unknown"


def _append_proposals(
    data_dir: Path,
    job_url: str,
    resolutions: list[ResolvedField],
) -> None:
    """Grow ``data/answer_proposals.yaml`` with narrative + unhandled
    fields. Duplicate questions extend their ``seen_on`` list; a new
    question adds a fresh entry.
    """
    proposals_path = data_dir / "answer_proposals.yaml"
    proposals = _load_proposals(proposals_path)
    seen_marker = f"{job_url}"

    for rf in resolutions:
        strategy = rf.answer.strategy
        if strategy not in {"narrative", "unhandled"}:
            continue
        question = rf.field.label.strip()
        if not question:
            continue
        entry = next(
            (e for e in proposals if e.get("question", "").strip() == question),
            None,
        )
        tentative = rf.answer.value if strategy == "narrative" else ""
        if entry is None:
            proposals.append(
                {
                    "question": question,
                    "tentative_answer": tentative,
                    "resolved_strategy": strategy,
                    "seen_on": [seen_marker],
                }
            )
        else:
            if seen_marker not in entry.get("seen_on", []):
                entry.setdefault("seen_on", []).append(seen_marker)
            # Refresh tentative when a new narrative-generated answer
            # comes in; unhandled leaves the earlier tentative alone.
            if strategy == "narrative" and tentative:
                entry["tentative_answer"] = tentative

    proposals_path.parent.mkdir(parents=True, exist_ok=True)
    proposals_path.write_text(
        yaml.safe_dump({"proposals": proposals}, sort_keys=False, allow_unicode=True)
    )


def _load_proposals(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return []
    return list(raw.get("proposals", []))
