"""Custom ATS 80% coverage gate (PR7 acceptance).

Regression backstop for the custom (non–big-4) apply stack. Reads the versioned
corpus manifest at ``tests/corpus/custom_ats_manifest.yaml`` and asserts:

1. Every entry with a ``capture_dir`` on disk is treated as *offline-eligible*
   and must have ``status`` in ``{"pass", "fail"}`` (pending contradicts a
   promoted capture).
2. The offline pass rate — ``pass / (pass + fail)`` over offline-eligible
   entries — is ``>= 0.80`` (see [plan.md](../../plan.md) §6).
3. A minimum corpus size is enforced (>= 5 offline-eligible entries) so the
   gate can't be gamed by pruning to a single row.
4. Each ``capture_dir`` on disk contains a ``form.yaml`` (the composable-forms
   scan regression already lives in ``test_capture_regression.py``; this
   assertion is a shallow sanity check that the pointer is meaningful).
5. Every ``status: skipped`` entry carries a ``skip_reason``.

Live-gated entries (``live_gate: true`` without a promoted capture) are
tracked in the manifest but excluded from the offline gate — those run under
``tests/integration/e2e/test_e2e_custom_ats_live.py`` when
``MAGICAPPLY_CUSTOM_ATS_LIVE=1``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magicapply.infrastructure.corpus.custom_ats import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "tests" / "corpus" / "custom_ats_manifest.yaml"

MIN_ELIGIBLE = 5
PASS_RATE_THRESHOLD = 0.80


def _offline_eligible(entries: list[dict]) -> list[dict]:
    """Entries whose ``capture_dir`` resolves to a real directory on disk."""
    eligible: list[dict] = []
    for entry in entries:
        capture_dir = entry.get("capture_dir")
        if not capture_dir:
            continue
        if (REPO_ROOT / capture_dir).is_dir():
            eligible.append(entry)
    return eligible


def test_manifest_loads_and_has_minimum_corpus() -> None:
    entries = load_manifest(MANIFEST_PATH)
    eligible = _offline_eligible(entries)
    assert len(eligible) >= MIN_ELIGIBLE, (
        f"custom ATS corpus too small for a meaningful gate: "
        f"{len(eligible)} offline-eligible entries, need >= {MIN_ELIGIBLE}. "
        f"Promote more captures under tests/fixtures/captured/custom-*/."
    )


def test_offline_eligible_entries_are_tested_states() -> None:
    """A promoted capture must be marked pass or fail — pending is a bug."""
    eligible = _offline_eligible(load_manifest(MANIFEST_PATH))
    bad = [
        entry
        for entry in eligible
        if entry.get("status") not in {"pass", "fail"}
    ]
    assert not bad, (
        "Offline-eligible entries with non-tested status "
        f"(should be 'pass' or 'fail'): {[e.get('id') for e in bad]}"
    )


def test_capture_dirs_contain_form_yaml() -> None:
    eligible = _offline_eligible(load_manifest(MANIFEST_PATH))
    missing = [
        entry.get("id")
        for entry in eligible
        if not (REPO_ROOT / entry["capture_dir"] / "form.yaml").is_file()
    ]
    assert not missing, (
        f"capture_dir(s) missing form.yaml — cannot regress against them: {missing}"
    )


def test_skipped_entries_document_reason() -> None:
    entries = load_manifest(MANIFEST_PATH)
    unjustified = [
        entry.get("id")
        for entry in entries
        if entry.get("status") == "skipped" and not entry.get("skip_reason")
    ]
    assert not unjustified, (
        f"skipped entries must set skip_reason: {unjustified}"
    )


def test_custom_ats_offline_pass_rate_meets_80_percent() -> None:
    """The headline gate: >= 80% of tested custom-ATS entries must pass."""
    eligible = _offline_eligible(load_manifest(MANIFEST_PATH))
    if not eligible:
        pytest.skip("no offline-eligible custom-ATS entries")
    passes = [e for e in eligible if e.get("status") == "pass"]
    failures = [e for e in eligible if e.get("status") == "fail"]
    pass_rate = len(passes) / len(eligible)
    assert pass_rate >= PASS_RATE_THRESHOLD, (
        f"custom ATS pass rate {pass_rate:.0%} below "
        f"{PASS_RATE_THRESHOLD:.0%} gate — failures: "
        f"{[e.get('id') for e in failures]}"
    )
