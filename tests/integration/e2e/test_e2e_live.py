"""Live-gated end-to-end: runs the real pipeline against the operator's own
configured sources, stopping one click short of Submit.

Reuses the existing ``integration`` marker (already gated on
``MAGICAPPLY_LIVE_TESTS=1`` by ``tests/integration/conftest.py``) and adds
one more in-body gate on ``MAGICAPPLY_LIVE_APPLY=1`` so the live-apply flow
never runs by accident — even under the ``integration`` opt-in.

Required environment (all four must be set for the test to actually execute):

- ``MAGICAPPLY_LIVE_TESTS=1``            — unblocks the ``integration`` marker.
- ``MAGICAPPLY_LIVE_APPLY=1``            — extra opt-in specific to this test.
- ``MAGICAPPLY_LIVE_APPLY_PROFILE=<name>`` — which profile to run.
- ``MAGICAPPLY_LIVE_APPLY_ROOT=<path>``  — optional; --root override. Falls back
  to the default lookup (``./configs``, ``$MAGICAPPLY_HOME``, ``~/.config``).

``--yes-submit`` is deliberately not wired into this test. Running a real
submission requires the operator to invoke the CLI by hand with the flag.
"""

from __future__ import annotations

import os
import shlex

import pytest
from typer.testing import CliRunner

from magicapply.cli.main import app


@pytest.mark.integration
@pytest.mark.slow
def test_live_pipeline_no_submit(capsys: pytest.CaptureFixture[str]) -> None:
    if os.environ.get("MAGICAPPLY_LIVE_APPLY") != "1":
        pytest.skip("set MAGICAPPLY_LIVE_APPLY=1 to enable the live-apply E2E")

    profile = os.environ.get("MAGICAPPLY_LIVE_APPLY_PROFILE")
    if not profile:
        pytest.skip(
            "set MAGICAPPLY_LIVE_APPLY_PROFILE=<profile-name> to pick which "
            "profile to exercise (the live test does not guess)"
        )

    args = ["run", profile, "--no-submit", "--headless"]
    root_override = os.environ.get("MAGICAPPLY_LIVE_APPLY_ROOT")
    if root_override:
        args.extend(["--root", root_override])

    runner = CliRunner()
    result = runner.invoke(app, args)

    # Emit through capsys so pytest -s prints the CLI output for a human
    # reviewer; this is the whole point of the live test.
    with capsys.disabled():
        print(f"$ magicapply {shlex.join(args)}")
        print(result.stdout)

    # Smoke check: the pipeline ran end-to-end without a Python-side crash.
    # Any terminal state per Application is acceptable — dry-run APPLIED,
    # NEEDS_INTERVENTION (CAPTCHA on a real ATS), or FAILED (unsupported
    # ATS on a non-Greenhouse job discovered by a real source). The
    # dry-run guard in BaseATSHandler.apply ensures no live submission
    # regardless of what the ATS handler does.
    assert result.exit_code == 0, result.stdout
