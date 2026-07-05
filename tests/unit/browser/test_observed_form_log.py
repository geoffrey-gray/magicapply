"""Unit tests for the Template-Method observed-form log writer."""

from __future__ import annotations

from pathlib import Path

import yaml

from magicapply.infrastructure.browser.ats.answer_router import ResolvedAnswer
from magicapply.infrastructure.browser.ats.form_scan import FormField
from magicapply.infrastructure.browser.ats.observed_form_log import (
    ResolvedField,
    log_observed_form,
)


def _field(label: str, kind: str = "text", selector: str = "#x") -> FormField:
    return FormField(selector=selector, label=label, kind=kind)


def _rf(label: str, strategy: str, value: str = "") -> ResolvedField:
    return ResolvedField(
        field=_field(label),
        answer=ResolvedAnswer(strategy=strategy, value=value),
    )


class TestWriteYaml:
    def test_writes_yaml_with_expected_fields(self, tmp_path: Path) -> None:
        resolutions = [
            _rf("Email", "static", "test@example.test"),
            _rf("Cover letter", "unhandled"),
            _rf("Why us?", "narrative", "because."),
        ]
        out = log_observed_form(
            app_id="abc",
            job_url="https://boards.greenhouse.io/acme/jobs/123",
            resolutions=resolutions,
            data_dir=tmp_path,
        )
        assert out is not None
        payload = yaml.safe_load(out.read_text())
        assert payload["app_id"] == "abc"
        assert payload["job_url"].startswith("https://boards.greenhouse.io/")
        assert len(payload["fields"]) == 3
        by_label = {f["label"]: f for f in payload["fields"]}
        # Identity values are redacted so PII does not leak into logs.
        assert by_label["Email"]["resolved_strategy"] == "static"
        assert by_label["Email"]["resolved_value"] == "<REDACTED>"
        # Narrative / unhandled surface as-is for review.
        assert by_label["Why us?"]["resolved_strategy"] == "narrative"
        assert by_label["Why us?"]["resolved_value"] == "because."
        assert by_label["Cover letter"]["resolved_strategy"] == "unhandled"


class TestProposals:
    def test_narrative_and_unhandled_appended(self, tmp_path: Path) -> None:
        resolutions = [
            _rf("Email", "static", "test@example.test"),
            _rf("Why us?", "narrative", "because."),
            _rf("Cover letter", "unhandled"),
        ]
        log_observed_form(
            app_id="abc",
            job_url="https://boards.greenhouse.io/acme/jobs/123",
            resolutions=resolutions,
            data_dir=tmp_path,
        )
        prop_path = tmp_path / "answer_proposals.yaml"
        assert prop_path.exists()
        payload = yaml.safe_load(prop_path.read_text())
        by_q = {p["question"]: p for p in payload["proposals"]}
        # Static answer never becomes a proposal.
        assert "Email" not in by_q
        assert by_q["Why us?"]["tentative_answer"] == "because."
        assert by_q["Cover letter"]["tentative_answer"] == ""

    def test_dedup_seen_on_across_multiple_runs(self, tmp_path: Path) -> None:
        # Same question hit twice on the same URL → seen_on has one entry.
        for _ in range(2):
            log_observed_form(
                app_id="abc",
                job_url="https://boards.greenhouse.io/acme/jobs/123",
                resolutions=[_rf("Why?", "narrative", "reasons")],
                data_dir=tmp_path,
            )
        payload = yaml.safe_load((tmp_path / "answer_proposals.yaml").read_text())
        entry = payload["proposals"][0]
        assert entry["seen_on"] == ["https://boards.greenhouse.io/acme/jobs/123"]

    def test_new_url_extends_seen_on(self, tmp_path: Path) -> None:
        log_observed_form(
            app_id="abc",
            job_url="https://boards.greenhouse.io/acme/jobs/123",
            resolutions=[_rf("Why?", "narrative", "reasons")],
            data_dir=tmp_path,
        )
        log_observed_form(
            app_id="def",
            job_url="https://jobs.lever.co/foo/456",
            resolutions=[_rf("Why?", "narrative", "reasons")],
            data_dir=tmp_path,
        )
        payload = yaml.safe_load((tmp_path / "answer_proposals.yaml").read_text())
        entry = payload["proposals"][0]
        assert len(entry["seen_on"]) == 2


class TestErrorHandling:
    def test_write_failure_returns_none(self, tmp_path: Path) -> None:
        # Data dir is a regular file — mkdir(parents=True) succeeds by
        # creating siblings but subsequent operations fail. Simulate by
        # pointing at a nonexistent, un-creatable path.
        bad_dir = Path("/proc/self/mem/nope")
        result = log_observed_form(
            app_id="abc",
            job_url="https://greenhouse.io/acme/jobs/1",
            resolutions=[_rf("Q?", "narrative", "A")],
            data_dir=bad_dir,
        )
        assert result is None
