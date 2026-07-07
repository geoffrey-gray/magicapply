"""Unit tests for capture promotion (CF.6)."""

from __future__ import annotations

from pathlib import Path

import yaml

from magicapply.infrastructure.browser.forms.capture_loader import (
    captured_fixtures_root,
    load_capture,
    promote_observed_capture,
)


def test_promote_observed_capture_copies_dom_and_form(tmp_path: Path) -> None:
    observed = tmp_path / "20260707-120000-boards-greenhouse-io-acme-jobs-1"
    observed.mkdir()
    (observed / "dom.html").write_text("<html><body>capture</body></html>")
    (observed / "form.yaml").write_text(
        yaml.safe_dump(
            {
                "capture": {"ats": "greenhouse", "form_selectors": ["form"]},
                "app_id": "live-run",
                "fields": [],
            }
        )
    )
    (observed / "screenshot.png").write_bytes(b"png")

    dest_root = tmp_path / "fixtures"
    promoted = promote_observed_capture(
        observed,
        capture_id="greenhouse-live-promoted",
        dest_root=dest_root,
    )
    assert promoted == dest_root / "greenhouse-live-promoted"
    assert (promoted / "dom.html").read_text() == "<html><body>capture</body></html>"
    assert (promoted / "form.yaml").exists()
    assert (promoted / "screenshot.png").exists()

    bundle = load_capture(promoted)
    assert bundle.meta.ats == "greenhouse"
    assert bundle.app_id == "live-run"


def test_captured_fixtures_root_points_at_tests_fixtures() -> None:
    root = captured_fixtures_root()
    assert root.name == "captured"
    assert (root / "greenhouse-acme-20260707" / "dom.html").exists()
    assert (root / "workday-circle-staff-ds-20260707" / "dom.html").exists()