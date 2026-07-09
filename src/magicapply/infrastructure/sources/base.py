"""Job source Protocol + shared error type + cookie-string parser."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from magicapply.domain.models.job import Job


class SourceError(Exception):
    """Raised when a source fails structurally (bad response, wrong format, auth missing)."""


class JobSource(Protocol):
    """Adapter for one configured source. Yields Job instances one at a time.

    Instances bind to one config entry (by `name`). They own their own HTTP
    client and rate limiter — no shared mutable state between sources.
    """

    name: str

    def discover(self) -> Iterator[Job]: ...


def parse_cookie_string(cookie_string: str, *, domain: str) -> list[dict]:
    """Parse a browser Cookie-header string into Playwright cookie dicts.

    Accepts the shape browsers emit in the ``Cookie:`` request header:

        ``"name1=value1; name2=value2; ..."``

    Returns a list of Playwright cookie dicts pinned to ``domain`` with
    the same safe defaults the single-cookie helpers use
    (``sources/linkedin.py::_li_at_cookie`` and
    ``sources/glassdoor.py::_session_cookie``): ``path='/'``,
    ``httpOnly=True``, ``secure=True``, ``sameSite='None'``. Empty and
    malformed pairs are skipped silently — the operator's paste from
    DevTools may include an occasional garbage line, and one bad cookie
    should never sink the whole session.

    Zero-friction copy/paste from DevTools' Network tab is the goal;
    parsing anything more structured (JSON, RFC 6265 with attributes)
    would only make the operator's life harder.
    """
    out: list[dict] = []
    for raw in cookie_string.split(";"):
        pair = raw.strip()
        if not pair or "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        name = name.strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "value": value.strip(),
                "domain": domain,
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        )
    return out
