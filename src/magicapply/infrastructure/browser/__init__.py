"""Browser automation: Playwright session + ATS strategies.

See docs/GOF_PATTERNS.md — ATSHandler uses Strategy, BaseATSHandler uses
Template Method, ATSHandlerFactory uses Factory Method.
"""

from magicapply.infrastructure.browser.ats import (
    ApplicationData,
    ApplicationResult,
    ATSHandler,
    ATSHandlerFactory,
    BaseATSHandler,
    GreenhouseHandler,
)
from magicapply.infrastructure.browser.captcha import detect_captcha

__all__ = [
    "ATSHandler",
    "ATSHandlerFactory",
    "ApplicationData",
    "ApplicationResult",
    "BaseATSHandler",
    "GreenhouseHandler",
    "detect_captcha",
]
