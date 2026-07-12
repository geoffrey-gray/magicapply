"""Factory Method for selecting an ATSHandler from a job URL."""

from __future__ import annotations

from typing import ClassVar

from magicapply.infrastructure.browser.ats.ashby import AshbyHandler
from magicapply.infrastructure.browser.ats.base import ATSHandler
from magicapply.infrastructure.browser.ats.generic import GenericHandler
from magicapply.infrastructure.browser.ats.greenhouse import GreenhouseHandler
from magicapply.infrastructure.browser.ats.indeed import IndeedHandler
from magicapply.infrastructure.browser.ats.lever import LeverHandler
from magicapply.infrastructure.browser.ats.linkedin import LinkedInHandler
from magicapply.infrastructure.browser.ats.workday import WorkdayHandler


class ATSHandlerFactory:
    """Choke point for URL → handler resolution.

    Handlers self-declare via their `matches(url)` classmethod. Adding a new
    ATS is: implement the handler + append it here. No `if "greenhouse" in url`
    scattered through the pipeline layer.
    """

    _handler_classes: ClassVar[list[type[ATSHandler]]] = [
        GreenhouseHandler,
        WorkdayHandler,
        LeverHandler,
        AshbyHandler,
        IndeedHandler,
        LinkedInHandler,
        GenericHandler,  # last — matches any URL
    ]

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
