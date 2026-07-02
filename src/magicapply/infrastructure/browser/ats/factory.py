"""Factory Method for selecting an ATSHandler from a job URL."""

from __future__ import annotations

from typing import ClassVar

from magicapply.infrastructure.browser.ats.base import ATSHandler
from magicapply.infrastructure.browser.ats.greenhouse import GreenhouseHandler


class ATSHandlerFactory:
    """Choke point for URL → handler resolution.

    Handlers self-declare via their `matches(url)` classmethod. Adding a new
    ATS is: implement the handler + append it here. No `if "greenhouse" in url`
    scattered through the pipeline layer.
    """

    _handler_classes: ClassVar[list[type[ATSHandler]]] = [GreenhouseHandler]

    @classmethod
    def for_url(cls, url: str) -> ATSHandler | None:
        for handler_cls in cls._handler_classes:
            if handler_cls.matches(url):
                return handler_cls()  # type: ignore[abstract]
        return None

    @classmethod
    def register(cls, handler_cls: type[ATSHandler]) -> None:
        """For tests and future ATS handlers."""
        cls._handler_classes.append(handler_cls)
