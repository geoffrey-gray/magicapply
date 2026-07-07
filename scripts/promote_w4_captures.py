#!/usr/bin/env python3
"""Promote live W.4 observed-form captures into tests/fixtures/captured/ (W.7)."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from magicapply.infrastructure.browser.forms.capture_loader import promote_observed_capture

# (observed_forms dir name suffix, fixture capture_id, capture meta overrides)
_PROMOTIONS: list[tuple[str, str, dict]] = [
    (
        "20260707-142422-job-boards.greenhouse.io-jobs-7772274",
        "greenhouse-reddit-20260707",
        {
            "ats": "greenhouse",
            "live": True,
            "form_selectors": ["form", "#application-form"],
            "schema_id": "greenhouse_reddit_7772274",
            "job_url": "https://job-boards.greenhouse.io/reddit/jobs/7772274",
        },
    ),
    (
        "20260707-143750-jobs.lever.co-foodsmart-c711b611-ac13-4167-8b60-5c0adb32af26",
        "lever-foodsmart-20260707",
        {
            "ats": "lever",
            "live": True,
            "form_selectors": ["form.posting-form", "form"],
            "schema_id": "lever_foodsmart",
            "job_url": "https://jobs.lever.co/foodsmart/c711b611-ac13-4167-8b60-5c0adb32af26",
        },
    ),
    (
        "20260707-151625-jobs.ashbyhq.com-trm-labs-b20af02a-0701-415e-9279-65ea5c2b6f12",
        "ashby-trm-20260707",
        {
            "ats": "ashby",
            "live": True,
            "form_selectors": ["form", "[data-testid='application-form']", "main"],
            "schema_id": "ashby_trm_labs",
            "job_url": "https://jobs.ashbyhq.com/trm-labs/b20af02a-0701-415e-9279-65ea5c2b6f12/application",
        },
    ),
]


def _find_observed(data_dir: Path, suffix: str) -> Path:
    root = data_dir / "observed_forms"
    exact = root / suffix
    if exact.is_dir():
        return exact
    matches = sorted(
        p for p in root.iterdir() if p.is_dir() and p.name.endswith(suffix)
    )
    if not matches:
        raise FileNotFoundError(f"no observed capture matching {suffix!r} under {root}")
    return matches[-1]


def _merge_capture_meta(dest: Path, meta: dict) -> None:
    form_path = dest / "form.yaml"
    payload = yaml.safe_load(form_path.read_text()) or {}
    capture = dict(payload.get("capture") or {})
    capture.update(meta)
    payload["capture"] = capture
    if meta.get("job_url"):
        payload["job_url"] = meta["job_url"]
    form_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="MagicApply data dir containing observed_forms/",
    )
    args = parser.parse_args()

    for suffix, capture_id, meta in _PROMOTIONS:
        observed = _find_observed(args.data_dir, suffix)
        dest = promote_observed_capture(observed, capture_id=capture_id)
        _merge_capture_meta(dest, meta)
        print(f"promoted {observed.name} -> {dest}")


if __name__ == "__main__":
    main()