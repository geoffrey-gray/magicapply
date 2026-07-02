"""CAPTCHA detection helper.

Both an explicit vendor sniff (reCAPTCHA / hCaptcha iframes and known DOM ids)
and a very loose fallback string match. Called at both entry and post-fill in
BaseATSHandler.apply — a CAPTCHA appearing mid-flow drops the application into
NEEDS_INTERVENTION so the user can finish manually.
"""

from __future__ import annotations

import re

# Loose but reasonable: matches iframe src or vendor script hosts.
_CAPTCHA_MARKERS = re.compile(
    r"""(
        recaptcha
        | h-?captcha            # both hcaptcha and h-captcha class
        | funcaptcha
        | arkoselabs
        | turnstile             # Cloudflare Turnstile
        | cf-challenge          # Cloudflare interstitial
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def detect_captcha(html: str) -> str | None:
    """Return a short label describing the CAPTCHA vendor found, or None."""
    m = _CAPTCHA_MARKERS.search(html)
    return m.group(1).lower() if m else None
