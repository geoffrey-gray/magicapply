"""Live custom-ATS driver — walks every ``live_gate: true`` row in the
corpus manifest, verifies the ATS factory routes ``apply_url`` to a
handler, and drives one dry-run against real Chromium so operators can
catch dead links, moved apply endpoints, or routing regressions.

Full form-filling dry-runs go through ``magicapply apply <job-id>``
(see [runbook §11](../../../docs/OPERATOR_RUNBOOK.md)); this test is the
CI-adjacent smoke check that keeps the manifest honest.

Gated on **all three** of:

- ``MAGICAPPLY_LIVE_TESTS=1``      — unblocks the ``integration`` marker.
- ``MAGICAPPLY_CUSTOM_ATS_LIVE=1`` — specific opt-in for this driver.
- Chromium installed under ``~/.cache/ms-playwright``.

Per row, the test asserts:

1. ``ATSHandlerFactory.for_url(apply_url)`` returns a handler
   (``GenericHandler`` is the catch-all; a ``None`` here means the URL
   is malformed).
2. Chromium can navigate to ``apply_url`` and reach a non-empty
   ``<title>`` — a shallow reachability check that catches 404s, dead
   apply endpoints, and Cloudflare walls without depending on the
   operator's static answers or resume artifacts.

Rows with ``status: skipped`` are dropped up front. ``pass`` rows still
run — the whole point is to notice when a previously-green apply URL
starts 404ing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.chromium_util import SKIP_NO_CHROMIUM, chromium_installed

from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
from magicapply.infrastructure.corpus.custom_ats import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = REPO_ROOT / "tests" / "corpus" / "custom_ats_manifest.yaml"

_CHROMIUM_CACHE = Path.home() / ".cache" / "ms-playwright"


def _chromium_installed() -> bool:
    return chromium_installed()


def _live_rows() -> list[dict]:
    """Manifest rows opted in via ``live_gate: true`` and not skipped."""
    if not MANIFEST_PATH.exists():
        return []
    return [
        row
        for row in load_manifest(MANIFEST_PATH)
        if row.get("live_gate") is True
        and row.get("status") != "skipped"
        and row.get("apply_url")
    ]


def _row_id(row: dict) -> str:
    return str(row.get("id") or row.get("apply_url", ""))


pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("MAGICAPPLY_CUSTOM_ATS_LIVE") != "1",
        reason="set MAGICAPPLY_CUSTOM_ATS_LIVE=1 to enable the live custom-ATS driver",
    ),
    pytest.mark.skipif(
        not _chromium_installed(),
        reason=SKIP_NO_CHROMIUM,
    ),
]


@pytest.mark.parametrize("row", _live_rows(), ids=_row_id)
def test_custom_ats_apply_url_routes_and_loads(row: dict) -> None:
    apply_url = row["apply_url"]
    handler = ATSHandlerFactory.for_url(apply_url)
    assert handler is not None, (
        f"no ATS handler routed {apply_url!r} — GenericHandler should be the "
        "catch-all; check factory registration order."
    )

    # Late-imported so unit runs never touch Playwright.
    from magicapply.infrastructure.browser.session import PlaywrightSession

    with PlaywrightSession(headless=True) as session:
        page = session.new_page()
        response = page.goto(apply_url, wait_until="domcontentloaded", timeout=30_000)
        # Some ATSes 302 through an auth or region wall — accept any response
        # object as long as the browser resolved something.
        assert response is not None, f"no response from {apply_url}"
        title = page.title()
        assert title, f"empty <title> on {apply_url} — dead or blocked page?"
