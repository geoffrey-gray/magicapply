"""Integration test gates.

`integration`-marked tests only run when MAGICAPPLY_LIVE_TESTS=1.
`anthropic`-marked tests additionally require ANTHROPIC_API_KEY.
`linkedin`-marked tests additionally require MAGICAPPLY_LINKEDIN_TESTS=1
(kept separate because LinkedIn scraping is ToS-sensitive — see .env.example).
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import pytest


def _truthy(name: str) -> bool:
    return os.environ.get(name, "0").lower() in {"1", "true", "yes", "on"}


def pytest_collection_modifyitems(config: pytest.Config, items: Iterable[pytest.Item]) -> None:
    live = _truthy("MAGICAPPLY_LIVE_TESTS")
    linkedin_ok = _truthy("MAGICAPPLY_LINKEDIN_TESTS")
    have_anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

    skip_integration = pytest.mark.skip(reason="set MAGICAPPLY_LIVE_TESTS=1 to run")
    skip_anthropic = pytest.mark.skip(
        reason="set MAGICAPPLY_LIVE_TESTS=1 and ANTHROPIC_API_KEY to run"
    )
    skip_linkedin = pytest.mark.skip(
        reason="set MAGICAPPLY_LINKEDIN_TESTS=1 to run (ToS-sensitive; see .env.example)"
    )

    for item in items:
        if "integration" in item.keywords and not live:
            item.add_marker(skip_integration)
        if "anthropic" in item.keywords and not (live and have_anthropic_key):
            item.add_marker(skip_anthropic)
        if "linkedin" in item.keywords and not linkedin_ok:
            item.add_marker(skip_linkedin)
