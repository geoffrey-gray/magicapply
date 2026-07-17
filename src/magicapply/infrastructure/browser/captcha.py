"""CAPTCHA detection helper.

Both an explicit vendor sniff (reCAPTCHA / hCaptcha iframes and known DOM ids)
and a very loose fallback string match. Called at both entry and post-fill in
BaseATSHandler.apply — a CAPTCHA appearing mid-flow drops the application into
NEEDS_INTERVENTION so the user can finish manually.
"""

from __future__ import annotations

import re

# Match live challenge widgets/scripts — not bare "recaptcha" in page config
# (Greenhouse embeds GOOGLE_RECAPTCHA_INVISIBLE_KEY in window.ENV without
# presenting a challenge on load).
_CAPTCHA_MARKERS = re.compile(
    r"""(
        google\.com/recaptcha
        | recaptcha/api
        | g-recaptcha
        | hcaptcha\.com
        | h-captcha
        | funcaptcha
        | arkoselabs
        | turnstile              # before challenges.cloudflare — same script URL
        | cf-challenge
        | challenges\.cloudflare
    )""",
    re.IGNORECASE | re.VERBOSE,
)


# Page-level interstitials that block navigation before any form work.
# Do NOT match bare google.com/recaptcha/api — dormant/invisible widgets on
# Indeed (and others) embed that script without presenting a challenge.
_BLOCKING_MARKERS = re.compile(
    r"""(
        recaptcha/api2/bframe
        | cf-challenge
        | challenges\.cloudflare
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def detect_blocking_captcha(html: str) -> str | None:
    """Return a label when the page itself is blocked by a challenge, or None."""
    m = _BLOCKING_MARKERS.search(html)
    return m.group(1).lower() if m else None


def detect_captcha(html: str) -> str | None:
    """Return a short label describing any CAPTCHA widget found, or None."""
    m = _CAPTCHA_MARKERS.search(html)
    return m.group(1).lower() if m else None
