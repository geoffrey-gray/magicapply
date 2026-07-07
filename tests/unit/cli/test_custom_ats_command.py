"""Tests for `magicapply custom-ats report`."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from magicapply.cli.main import app

runner = CliRunner()


def _write_manifest(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")


def _make_capture(dir_path: Path) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "form.yaml").write_text("fields: []\n", encoding="utf-8")


class TestCustomAtsReport:
    def test_prints_summary_and_pass_rate_and_exits_zero(self, tmp_path: Path) -> None:
        cap = tmp_path / "cap"
        _make_capture(cap)
        manifest = tmp_path / "manifest.yaml"
        _write_manifest(
            manifest,
            [
                {
                    "id": "a",
                    "platform": "eightfold",
                    "source": "linkedin-search",
                    "status": "pass",
                    "capture_dir": str(cap),
                },
                {
                    "id": "b",
                    "platform": "eightfold",
                    "source": "linkedin-search",
                    "status": "pending",
                },
            ],
        )

        result = runner.invoke(
            app,
            ["custom-ats", "report", "--manifest", str(manifest)],
        )

        assert result.exit_code == 0, result.stdout
        assert "entries: 2" in result.stdout
        assert "eightfold" in result.stdout
        assert "linkedin-search" in result.stdout
        assert "100%" in result.stdout

    def test_exit_1_when_pass_rate_below_gate(self, tmp_path: Path) -> None:
        cap_pass = tmp_path / "cap_pass"
        cap_fail_1 = tmp_path / "cap_fail_1"
        cap_fail_2 = tmp_path / "cap_fail_2"
        for c in (cap_pass, cap_fail_1, cap_fail_2):
            _make_capture(c)
        manifest = tmp_path / "manifest.yaml"
        _write_manifest(
            manifest,
            [
                {"id": "p", "platform": "phenom", "status": "pass", "capture_dir": str(cap_pass)},
                {"id": "f1", "platform": "phenom", "status": "fail", "capture_dir": str(cap_fail_1)},
                {"id": "f2", "platform": "phenom", "status": "fail", "capture_dir": str(cap_fail_2)},
            ],
        )

        result = runner.invoke(
            app,
            ["custom-ats", "report", "--manifest", str(manifest)],
        )

        # 1 pass / 3 eligible = 33% — below 80% gate.
        assert result.exit_code == 1, result.stdout
        assert "BELOW GATE" in result.stdout

    def test_missing_manifest_exits_1(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["custom-ats", "report", "--manifest", str(tmp_path / "nope.yaml")],
        )
        assert result.exit_code == 1
        assert "manifest not found" in result.stdout

    def test_no_offline_eligible_exits_1(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.yaml"
        _write_manifest(
            manifest,
            [
                {"id": "x", "platform": "eightfold", "status": "pending", "live_gate": True},
            ],
        )
        result = runner.invoke(
            app,
            ["custom-ats", "report", "--manifest", str(manifest)],
        )
        assert result.exit_code == 1
        assert "no offline-eligible" in result.stdout
