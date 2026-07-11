"""Tests for resume → StaticAnswers overlay (phone, current employer)."""

from __future__ import annotations

from magicapply.config.models import StaticAnswers
from magicapply.domain.models.resume import BaseResume, ExperienceEntry
from magicapply.domain.resumes.static_overlay import (
    current_employer_from_resume,
    overlay_static_from_resume,
)


def _resume(**kwargs: object) -> BaseResume:
    base = {
        "name": "Geoffrey M. Gray",
        "email": "g@example.com",
        "phone": "321-279-2047",
        "experience": [
            ExperienceEntry(
                company="Intrinsic",
                title="Sr. AI Engineer",
                start="2025-10",
                end=None,
            ),
            ExperienceEntry(
                company="Kaufman Hall",
                title="AVP Data Science",
                start="2024-08",
                end="2025-09",
            ),
        ],
    }
    base.update(kwargs)
    return BaseResume.model_validate(base)


class TestCurrentEmployerFromResume:
    def test_prefers_open_ended_role(self) -> None:
        assert current_employer_from_resume(_resume()) == "Intrinsic"

    def test_present_marker(self) -> None:
        r = _resume(
            experience=[
                ExperienceEntry(
                    company="Acme", title="Eng", start="2020-01", end="present"
                )
            ]
        )
        assert current_employer_from_resume(r) == "Acme"

    def test_falls_back_to_first_when_all_ended(self) -> None:
        r = _resume(
            experience=[
                ExperienceEntry(
                    company="OldCo", title="Eng", start="2018-01", end="2020-01"
                ),
                ExperienceEntry(
                    company="OlderCo", title="Eng", start="2015-01", end="2018-01"
                ),
            ]
        )
        assert current_employer_from_resume(r) == "OldCo"

    def test_empty_experience(self) -> None:
        assert current_employer_from_resume(_resume(experience=[])) is None


class TestOverlayStaticFromResume:
    def test_fills_phone_and_employer_when_static_empty(self) -> None:
        static = StaticAnswers(full_name="G", email="g@example.com")
        out = overlay_static_from_resume(static, _resume())
        assert out.phone == "321-279-2047"
        assert out.current_employer == "Intrinsic"

    def test_static_phone_wins_over_resume(self) -> None:
        static = StaticAnswers(
            full_name="G", email="g@example.com", phone="000-000-0000"
        )
        out = overlay_static_from_resume(static, _resume())
        assert out.phone == "000-000-0000"

    def test_static_employer_wins(self) -> None:
        static = StaticAnswers(
            full_name="G",
            email="g@example.com",
            current_employer="Manual Co",
        )
        out = overlay_static_from_resume(static, _resume())
        assert out.current_employer == "Manual Co"
