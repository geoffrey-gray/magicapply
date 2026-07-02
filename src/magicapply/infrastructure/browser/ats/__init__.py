"""ATS-specific application handlers (Strategy + Template Method + Factory)."""

from magicapply.infrastructure.browser.ats.base import (
    ApplicationData,
    ApplicationResult,
    ATSHandler,
    BaseATSHandler,
)
from magicapply.infrastructure.browser.ats.factory import ATSHandlerFactory
from magicapply.infrastructure.browser.ats.greenhouse import GreenhouseHandler

__all__ = [
    "ATSHandler",
    "ATSHandlerFactory",
    "ApplicationData",
    "ApplicationResult",
    "BaseATSHandler",
    "GreenhouseHandler",
]
