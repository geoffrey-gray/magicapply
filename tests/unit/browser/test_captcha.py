"""Tests for CAPTCHA detection."""

from __future__ import annotations

import pytest

from magicapply.infrastructure.browser.captcha import (
    detect_blocking_captcha,
    detect_captcha,
)


@pytest.mark.parametrize(
    "html,expected_substr",
    [
        ('<iframe src="https://www.google.com/recaptcha/api2/"></iframe>', "captcha"),
        ('<div class="h-captcha" data-sitekey="x"></div>', "captcha"),
        (
            "<script src='https://challenges.cloudflare.com/turnstile/v0/api.js'></script>",
            "cloudflare",
        ),
        ("<html><body>Just a form</body></html>", None),
        (
            '"GOOGLE_RECAPTCHA_INVISIBLE_KEY":"6LfmcbcpAAAAAChNTbhUShzUOAMj_wY9LQIvLFX0"',
            None,
        ),
    ],
)
def test_detect(html: str, expected_substr: str | None) -> None:
    result = detect_captcha(html)
    if expected_substr is None:
        assert result is None
    else:
        assert result is not None
        assert expected_substr in result


@pytest.mark.parametrize(
    "html,expected_substr",
    [
        (
            '<iframe src="https://www.google.com/recaptcha/api2/bframe"></iframe>',
            "recaptcha",
        ),
        ('<div class="g-recaptcha" data-sitekey="x"></div>', None),
    ],
)
def test_detect_blocking(html: str, expected_substr: str | None) -> None:
    result = detect_blocking_captcha(html)
    if expected_substr is None:
        assert result is None
    else:
        assert result is not None
        assert expected_substr in result
