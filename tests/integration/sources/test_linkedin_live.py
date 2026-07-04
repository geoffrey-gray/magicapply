"""Live-gated LinkedIn adapter smoke test.

Runs a real authenticated LinkedIn search when all three env vars are set:

  MAGICAPPLY_LIVE_TESTS=1        -- unblocks the integration marker
  MAGICAPPLY_LINKEDIN_TESTS=1    -- unblocks the linkedin marker
  LINKEDIN_LI_AT=<cookie>         -- your logged-in li_at session cookie
  MAGICAPPLY_LINKEDIN_ACK=1       -- explicit ToS-risk acknowledgement

Missing any of the above skips cleanly. This test never posts anything;
it just proves the search-page walker returns at least one Job for a
common query.
"""

from __future__ import annotations

import os

import pytest

from magicapply.config.models import LinkedInSource
from magicapply.infrastructure.sources.linkedin import LinkedInAdapter


@pytest.mark.integration
@pytest.mark.linkedin
def test_live_search_returns_jobs() -> None:
    if not os.environ.get("LINKEDIN_LI_AT"):
        pytest.skip("set LINKEDIN_LI_AT to your li_at cookie value")
    if os.environ.get("MAGICAPPLY_LINKEDIN_ACK") != "1":
        pytest.skip("set MAGICAPPLY_LINKEDIN_ACK=1 to acknowledge ToS risk")

    adapter = LinkedInAdapter.from_config(
        LinkedInSource(
            name="live-linkedin",
            queries=["python engineer"],
            rate_limit_per_minute=6,
        )
    )
    jobs = list(adapter.discover())
    # A common query should return at least one job; if it does not, either
    # LinkedIn started blocking us or the parser drifted -- either warrants
    # investigation. Print for the human reviewer either way.
    print(f"LinkedIn live returned {len(jobs)} jobs")
    assert jobs, "expected at least one job for a broad query"
    # Sanity-check that at least one has the expected identity fields.
    first = jobs[0]
    assert first.title
    assert first.company
    assert first.url.startswith("https://")
