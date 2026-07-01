"""Unit tests for config Pydantic models — round-trip, defaults, strict rejection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from magicapply.config.models import (
    BaseConfig,
    CareerPageSource,
    LinkedInSource,
    Profile,
    ScoringConfig,
    StaticAnswers,
)


def _valid_static_answers() -> dict:
    return {"full_name": "Test User", "email": "test@example.com"}


def _valid_base_dict() -> dict:
    return {
        "static_answers": _valid_static_answers(),
        "sources": [{"type": "career_page", "name": "acme", "urls": []}],
    }


class TestBaseConfig:
    def test_minimal_valid(self) -> None:
        cfg = BaseConfig.model_validate(_valid_base_dict())
        assert cfg.version == 1
        assert cfg.llm.provider == "anthropic"
        assert cfg.scoring.threshold == 70
        assert len(cfg.sources) == 1
        assert isinstance(cfg.sources[0], CareerPageSource)

    def test_static_answers_required(self) -> None:
        with pytest.raises(ValidationError, match="static_answers"):
            BaseConfig.model_validate({})

    def test_extra_field_rejected(self) -> None:
        raw = _valid_base_dict()
        raw["unknown_key"] = "oops"
        with pytest.raises(ValidationError, match="unknown_key"):
            BaseConfig.model_validate(raw)


class TestSources:
    def test_discriminator_selects_right_class(self) -> None:
        raw = {
            "static_answers": _valid_static_answers(),
            "sources": [
                {"type": "career_page", "name": "a", "urls": ["https://a.example"]},
                {"type": "linkedin", "name": "b", "queries": ["python"]},
                {"type": "job_url", "name": "c", "urls": []},
            ],
        }
        cfg = BaseConfig.model_validate(raw)
        assert isinstance(cfg.sources[0], CareerPageSource)
        assert isinstance(cfg.sources[1], LinkedInSource)
        assert cfg.sources[1].enabled is False  # LinkedIn defaults to opt-in

    def test_unknown_source_type_rejected(self) -> None:
        raw = {
            "static_answers": _valid_static_answers(),
            "sources": [{"type": "monster", "name": "x"}],
        }
        with pytest.raises(ValidationError, match=r"monster|type"):
            BaseConfig.model_validate(raw)

    def test_blank_source_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="blank"):
            CareerPageSource.model_validate({"type": "career_page", "name": "  "})

    def test_source_names_unique_helper(self) -> None:
        cfg = BaseConfig.model_validate(
            {
                "static_answers": _valid_static_answers(),
                "sources": [
                    {"type": "career_page", "name": "a"},
                    {"type": "job_url", "name": "b"},
                ],
            }
        )
        assert cfg.source_names() == {"a", "b"}


class TestScoring:
    def test_threshold_bounds(self) -> None:
        with pytest.raises(ValidationError):
            ScoringConfig.model_validate({"threshold": -1})
        with pytest.raises(ValidationError):
            ScoringConfig.model_validate({"threshold": 101})
        assert ScoringConfig.model_validate({"threshold": 0}).threshold == 0
        assert ScoringConfig.model_validate({"threshold": 100}).threshold == 100


class TestStaticAnswers:
    def test_optional_fields_default_none(self) -> None:
        sa = StaticAnswers.model_validate(_valid_static_answers())
        assert sa.phone is None
        assert sa.linkedin_url is None
        assert sa.gender is None


class TestProfile:
    def test_minimal_valid(self) -> None:
        p = Profile.model_validate({"name": "swe", "base_resume": "swe.yaml"})
        assert p.apply.auto_apply is True
        assert p.apply.narrative_style == "concise"

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="blank"):
            Profile.model_validate({"name": "", "base_resume": "swe.yaml"})
