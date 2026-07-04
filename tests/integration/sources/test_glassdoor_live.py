"""Live-gated Glassdoor adapter smoke test.

Runs when MAGICAPPLY_LIVE_TESTS=1 + MAGICAPPLY_GLASSDOOR_ACK=1.
Optionally uses GLASSDOOR_SESSION if set. Glassdoor's Cloudflare defence
frequently blocks headless Chromium; the assertion is "runs without
crashing", not "returns >=1 job".
"""

from __future__ import annotations

import os

import pytest

from magicapply.config.models import GlassdoorSource
from magicapply.infrastructure.sources.glassdoor import GlassdoorAdapter


@pytest.mark.integration
def test_live_search() -> None:
    if os.environ.get("MAGICAPPLY_GLASSDOOR_ACK") != "1":
        pytest.skip("set MAGICAPPLY_GLASSDOOR_ACK=1 to acknowledge Glassdoor ToS risk")

    adapter = GlassdoorAdapter.from_config(
        GlassdoorSource(
            name="live-glassdoor",
            queries=["python engineer"],
            rate_limit_per_minute=3,
        )
    )
    jobs = list(adapter.discover())
    print(f"Glassdoor live returned {len(jobs)} jobs")
    for job in jobs[:3]:
        print(f"  - {job.title} @ {job.company}")
