"""Live-gated LinkedIn adapter smoke test.

Runs a real authenticated LinkedIn search when live gates are set and
auth is available via any of:

  - data/auth/linkedin_storage_state.json  (from ``magicapply auth login``)
  - LINKEDIN_SESSION_COOKIES
  - LINKEDIN_LI_AT

Required env:

  MAGICAPPLY_LIVE_TESTS=1        -- unblocks the integration marker
  MAGICAPPLY_LINKEDIN_TESTS=1    -- unblocks the linkedin marker
  MAGICAPPLY_LINKEDIN_ACK=1      -- explicit ToS-risk acknowledgement

Optional:

  MAGICAPPLY_LIVE_APPLY_ROOT     -- config root (defaults to ``configs``)

Missing ack or auth skips cleanly. This test never posts anything; it
proves the search-page walker returns at least one Job for a common query
with max_pages=1 (safe drip).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from magicapply.config.models import LinkedInSource
from magicapply.infrastructure.browser.auth_session import resolve_session_auth
from magicapply.infrastructure.sources.linkedin import LinkedInAdapter


def _resolve_data_dir() -> Path | None:
    root = os.environ.get("MAGICAPPLY_LIVE_APPLY_ROOT", "configs")
    root_path = Path(root)
    if not root_path.is_dir():
        return None
    try:
        from magicapply.config import load_config

        return load_config(root_path.resolve()).data_dir()
    except Exception:  # noqa: BLE001 — live test falls back to ../data
        candidate = root_path.resolve().parent / "data"
        return candidate if candidate.is_dir() else None


@pytest.mark.integration
@pytest.mark.linkedin
def test_live_search_returns_jobs() -> None:
    if os.environ.get("MAGICAPPLY_LINKEDIN_ACK") != "1":
        pytest.skip("set MAGICAPPLY_LINKEDIN_ACK=1 to acknowledge ToS risk")

    data_dir = _resolve_data_dir()
    auth = resolve_session_auth("linkedin", data_dir)
    if auth.source == "none":
        pytest.skip(
            "LinkedIn auth missing — run `magicapply auth login linkedin` "
            "or set LINKEDIN_SESSION_COOKIES / LINKEDIN_LI_AT"
        )

    adapter = LinkedInAdapter.from_config(
        LinkedInSource(
            name="live-linkedin",
            queries=["python engineer"],
            rate_limit_per_minute=3,
            max_pages=1,
            enrich_apply_urls=False,
            enabled=True,
        ),
        data_dir=data_dir,
    )
    jobs = list(adapter.discover())
    # A common query should return at least one job; if it does not, either
    # LinkedIn started blocking us or the parser drifted -- either warrants
    # investigation. Print for the human reviewer either way.
    print(f"LinkedIn live returned {len(jobs)} jobs (auth={auth.source})")
    assert jobs, "expected at least one job for a broad query"
    # Sanity-check that at least one has the expected identity fields.
    first = jobs[0]
    assert first.title
    assert first.company
    assert first.url.startswith("https://")
