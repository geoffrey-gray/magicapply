"""Tests for ATSTimeoutsConfig — config-driven ATS timeouts."""

import pytest
from pydantic import ValidationError

from magicapply.config.models import ATSTimeoutsConfig


class TestDefaultValues:
    """Defaults match old hardcoded constants."""

    def test_default_values_match_old_constants(self) -> None:
        config = ATSTimeoutsConfig()
        assert config.candidate_timeout_ms == 500
        assert config.wizard_click_timeout_ms == 8_000
        assert config.auth_fill_timeout_ms == 8_000
        assert config.page_load_wait_ms == 3_000
        assert config.step_transition_wait_ms == 2_500
        assert config.brief_wait_ms == 1_500
        assert config.ats_overrides == {}


class TestValidationBounds:
    """Min/max validation works."""

    def test_candidate_timeout_too_low(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 100"):
            ATSTimeoutsConfig(candidate_timeout_ms=50)

    def test_candidate_timeout_too_high(self) -> None:
        with pytest.raises(ValidationError, match="less than or equal to 10000"):
            ATSTimeoutsConfig(candidate_timeout_ms=15_000)

    def test_wizard_click_timeout_too_low(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 1000"):
            ATSTimeoutsConfig(wizard_click_timeout_ms=500)

    def test_wizard_click_timeout_too_high(self) -> None:
        with pytest.raises(ValidationError, match="less than or equal to 30000"):
            ATSTimeoutsConfig(wizard_click_timeout_ms=35_000)

    def test_page_load_wait_within_bounds(self) -> None:
        config = ATSTimeoutsConfig(page_load_wait_ms=5_000)
        assert config.page_load_wait_ms == 5_000


class TestCustomValues:
    """Can override individual fields."""

    def test_custom_values(self) -> None:
        config = ATSTimeoutsConfig(
            wizard_click_timeout_ms=15_000,
            page_load_wait_ms=5_000,
        )
        assert config.wizard_click_timeout_ms == 15_000
        assert config.page_load_wait_ms == 5_000
        # Other fields use defaults
        assert config.candidate_timeout_ms == 500
        assert config.auth_fill_timeout_ms == 8_000


class TestGetForATS:
    """get_for_ats applies ATS-specific overrides."""

    def test_get_for_ats_no_override_returns_self(self) -> None:
        config = ATSTimeoutsConfig()
        result = config.get_for_ats("greenhouse")
        assert result is config

    def test_get_for_ats_with_override(self) -> None:
        config = ATSTimeoutsConfig(
            ats_overrides={
                "workday": {
                    "wizard_click_timeout_ms": 15_000,
                    "page_load_wait_ms": 5_000,
                }
            }
        )
        result = config.get_for_ats("workday")
        assert result.wizard_click_timeout_ms == 15_000
        assert result.page_load_wait_ms == 5_000
        # Other fields preserve defaults
        assert result.candidate_timeout_ms == 500

    def test_get_for_ats_preserves_original(self) -> None:
        """Original config unchanged by get_for_ats."""
        config = ATSTimeoutsConfig(
            wizard_click_timeout_ms=8_000,
            ats_overrides={
                "workday": {"wizard_click_timeout_ms": 15_000}
            }
        )
        result = config.get_for_ats("workday")
        # Result has override
        assert result.wizard_click_timeout_ms == 15_000
        # Original unchanged
        assert config.wizard_click_timeout_ms == 8_000

    def test_get_for_ats_partial_override(self) -> None:
        """Partial override merges with base config."""
        config = ATSTimeoutsConfig(
            wizard_click_timeout_ms=10_000,
            page_load_wait_ms=4_000,
            ats_overrides={
                "workday": {"wizard_click_timeout_ms": 15_000}
            }
        )
        result = config.get_for_ats("workday")
        # Overridden field
        assert result.wizard_click_timeout_ms == 15_000
        # Non-overridden field from base
        assert result.page_load_wait_ms == 4_000


class TestWorkdayGetTimeoutsFallback:
    """_get_timeouts helper fallback works when None."""

    def test_fallback_returns_default_config(self) -> None:
        from magicapply.infrastructure.browser.ats.workday import _get_timeouts

        # Simple mock object with ats_timeouts attribute
        class MockData:
            ats_timeouts = None

        data = MockData()
        timeouts = _get_timeouts(data)  # type: ignore[arg-type]
        assert timeouts.wizard_click_timeout_ms == 8_000
        assert timeouts.candidate_timeout_ms == 500

    def test_fallback_uses_provided_config(self) -> None:
        from magicapply.infrastructure.browser.ats.workday import _get_timeouts

        custom = ATSTimeoutsConfig(wizard_click_timeout_ms=12_000)

        # Simple mock object with ats_timeouts attribute
        class MockData:
            ats_timeouts = custom

        data = MockData()
        timeouts = _get_timeouts(data)  # type: ignore[arg-type]
        assert timeouts.wizard_click_timeout_ms == 12_000
