"""Live-gated Indeed adapter smoke test.

Runs a real Indeed search when both env gates are set:

  MAGICAPPLY_LIVE_TESTS=1   -- unblocks the integration marker
  MAGICAPPLY_INDEED_ACK=1   -- explicit ToS-risk acknowledgement

Skips cleanly otherwise. Indeed's Cloudflare defence often blocks headless
Chromium; when that happens the adapter returns zero jobs and this test
prints the Cloudflare status for the human reviewer rather than failing.
"""

from __future__ import annotations

import os

import pytest

from magicapply.config.models import IndeedSource
from magicapply.infrastructure.sources.indeed import IndeedAdapter


@pytest.mark.integration
def test_live_search() -> None:
    if os.environ.get("MAGICAPPLY_INDEED_ACK") != "1":
        pytest.skip("set MAGICAPPLY_INDEED_ACK=1 to acknowledge Indeed ToS risk")

    adapter = IndeedAdapter.from_config(
        IndeedSource(
            name="live-indeed",
            queries=["python engineer"],
            rate_limit_per_minute=3,
        )
    )
    jobs = list(adapter.discover())
    print(f"Indeed live returned {len(jobs)} jobs")
    # Not asserting >= 1: Cloudflare frequently blocks headless Chromium
    # and the adapter surfaces that as an empty result. The value here is
    # verifying the adapter runs end-to-end without crashing.
    for job in jobs[:3]:
        print(f"  - {job.title} @ {job.company}")
