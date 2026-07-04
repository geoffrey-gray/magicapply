"""Local HTTP fixture that impersonates a careers page + Greenhouse form.

The server:

- Serves ``GET /careers`` — a listing page with three ``JobPosting`` JSON-LD
  blocks. The URLs contain ``greenhouse.io`` as a path segment so
  ``GreenhouseHandler.matches`` accepts them without any handler-side change
  (see ``infrastructure/browser/ats/greenhouse.py``:``_MATCH_HOSTS``, which
  does a substring check on the full URL).
- Serves ``GET /greenhouse.io/<slug>/apply`` — a form with the exact
  selectors ``GreenhouseHandler._fill_static`` / ``_submit`` use.
- Records every ``POST /submit`` payload on ``.submissions`` for
  assertion by the test.

Deliberately does not include any of the substrings ``captcha.detect_captcha``
looks for; the CAPTCHA branch in ``BaseATSHandler.apply`` never fires.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
from collections.abc import Iterator
from email import message_from_bytes
from email.policy import HTTP as EMAIL_HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


# --- The three JSON-LD JobPostings --------------------------------------------

_JOBS: list[dict[str, Any]] = [
    {
        "slug": "senior-backend",
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": (
            "We are hiring a Senior Backend Engineer to work on distributed "
            "systems in Python. Strong ownership and delivery track record."
        ),
    },
    {
        "slug": "ios-designer",
        "title": "iOS Designer",
        "company": "Beta",
        "location": "San Francisco",
        "description": (
            "Design beautiful iOS surfaces. Portfolio required."
        ),
    },
    {
        "slug": "director",
        "title": "Director of Engineering",
        "company": "Gamma",
        "location": "Remote",
        "description": (
            "Lead engineering teams across the platform group. "
            "You will partner with product and design."
        ),
    },
]


def _job_posting_jsonld(base_url: str, job: dict[str, Any]) -> str:
    payload = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": job["title"],
        "hiringOrganization": {
            "@type": "Organization",
            "name": job["company"],
        },
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": job["location"],
            },
        },
        "url": f"{base_url}/greenhouse.io/{job['slug']}/apply",
        "description": job["description"],
    }
    return f'<script type="application/ld+json">{json.dumps(payload)}</script>'


def _careers_html(base_url: str) -> str:
    scripts = "\n".join(_job_posting_jsonld(base_url, j) for j in _JOBS)
    return (
        "<!doctype html><html><head><title>Fixture Careers</title></head>"
        "<body><h1>Fixture Careers</h1>"
        f"{scripts}"
        "</body></html>"
    )


_APPLY_FORM_HTML = """\
<!doctype html>
<html>
  <head><title>Apply</title></head>
  <body>
    <h1>Apply</h1>
    <form method="POST" action="/submit" enctype="multipart/form-data">
      <label>First name <input id="first_name" name="first_name"></label>
      <label>Last name <input id="last_name" name="last_name"></label>
      <label>Email <input id="email" name="email"></label>
      <label>Phone <input id="phone" name="phone"></label>
      <label>LinkedIn <input name="linkedin_url"></label>
      <label>Cover letter <textarea name="cover_letter_text"></textarea></label>
      <label>Resume <input type="file" name="resume"></label>
      <label>Are you authorized to work in the US?
        <select name="authorized">
          <option value="">--</option>
          <option value="Yes">Yes</option>
          <option value="No">No</option>
        </select>
      </label>
      <label>Why do you want to work at Acme?
        <textarea name="why_acme"></textarea>
      </label>
      <input type="submit" value="Apply">
    </form>
  </body>
</html>
"""

# Workday-shaped single-page form: data-automation-id selectors matching the
# real Workday wizard's convention. Single page (no multi-step navigation)
# because a fixture-side wizard would just be JavaScript we do not need to
# ship. The handler's Next-button loop terminates because there is no Next
# button on this page -- exactly the "you have reached the Review step"
# terminal state.
_WORKDAY_FORM_HTML = """\
<!doctype html>
<html>
  <head><title>Workday Apply</title></head>
  <body>
    <h1>Workday Apply</h1>
    <form method="POST" action="/submit" enctype="multipart/form-data">
      <label>First name
        <input data-automation-id="legalNameSection_firstName"
               name="first_name">
      </label>
      <label>Last name
        <input data-automation-id="legalNameSection_lastName"
               name="last_name">
      </label>
      <label>Email
        <input data-automation-id="email" name="email">
      </label>
      <label>Phone
        <input data-automation-id="phone-number" name="phone">
      </label>
      <label>Resume
        <input type="file" data-automation-id="file-upload-input-ref"
               name="resume">
      </label>
      <button type="submit"
              data-automation-id="submitApplication">Submit</button>
    </form>
  </body>
</html>
"""

_LEVER_FORM_HTML = """\
<!doctype html>
<html>
  <head><title>Lever Apply</title></head>
  <body>
    <h1>Lever Apply</h1>
    <form method="POST" action="/submit" enctype="multipart/form-data"
          class="posting-form">
      <label>Full name <input name="name"></label>
      <label>Email <input name="email"></label>
      <label>Phone <input name="phone"></label>
      <label>LinkedIn <input name="urls[LinkedIn]"></label>
      <label>Cover letter <textarea name="comments"></textarea></label>
      <label>Resume <input type="file" name="resume"></label>
      <button type="submit">Apply</button>
    </form>
  </body>
</html>
"""

_ASHBY_FORM_HTML = """\
<!doctype html>
<html>
  <head><title>Ashby Apply</title></head>
  <body>
    <h1>Ashby Apply</h1>
    <form method="POST" action="/submit" enctype="multipart/form-data">
      <label>Full name <input name="_systemfield_name"></label>
      <label>Email <input name="_systemfield_email"></label>
      <label>Phone <input name="_systemfield_phone"></label>
      <label>LinkedIn <input name="_systemfield_linkedin"></label>
      <label>Location <input name="_systemfield_location"></label>
      <label>Resume <input type="file" name="_systemfield_resume"></label>
      <button type="submit">Submit application</button>
    </form>
  </body>
</html>
"""

_THANK_YOU_HTML = "<!doctype html><html><body><h1>Thanks!</h1></body></html>"


class FixtureServer:
    """Threaded HTTP server serving the fixture routes."""

    def __init__(self) -> None:
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.submissions: list[dict[str, str]] = []

    def start(self) -> str:
        submissions = self.submissions

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence stderr chatter
                pass

            def do_GET(self) -> None:  # noqa: N802
                base_url = f"http://{self.headers.get('Host', 'localhost')}"
                if self.path == "/careers":
                    self._html(_careers_html(base_url))
                    return
                if self.path.startswith("/greenhouse.io/") and self.path.endswith("/apply"):
                    self._html(_APPLY_FORM_HTML)
                    return
                if self.path.startswith("/myworkdayjobs.com/") and self.path.endswith("/apply"):
                    self._html(_WORKDAY_FORM_HTML)
                    return
                if self.path.startswith("/jobs.lever.co/") and self.path.endswith("/apply"):
                    self._html(_LEVER_FORM_HTML)
                    return
                if self.path.startswith("/jobs.ashbyhq.com/") and (
                    self.path.endswith("/apply") or self.path.endswith("/application")
                ):
                    self._html(_ASHBY_FORM_HTML)
                    return
                if self.path == "/submit" or self.path == "/thanks":
                    self._html(_THANK_YOU_HTML)
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                if self.path == "/submit":
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length)
                    content_type = self.headers.get("Content-Type", "")
                    if content_type.startswith("multipart/form-data"):
                        fields, files = _parse_multipart(content_type, body)
                        submissions.append({**fields, "_files": files})
                    else:
                        parsed = urllib.parse.parse_qs(
                            body.decode("utf-8"), keep_blank_values=True
                        )
                        submissions.append(
                            {k: (v[0] if v else "") for k, v in parsed.items()}
                        )
                    self._html(_THANK_YOU_HTML)
                    return
                self.send_response(404)
                self.end_headers()

            def _html(self, html: str) -> None:
                data = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        # Bind to an ephemeral port so parallel test runs don't collide.
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def fixture_server() -> Iterator[FixtureServer]:
    """Pytest-style context: start a fresh server for each test, tear it down."""
    server = FixtureServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _parse_multipart(
    content_type: str, body: bytes
) -> tuple[dict[str, str], dict[str, bytes]]:
    """Return (fields, files) parsed from a multipart/form-data POST.

    Uses stdlib `email` because Python 3.11 removed `cgi.parse_multipart`.
    """
    header_bytes = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    msg = message_from_bytes(header_bytes + body, policy=EMAIL_HTTP)
    fields: dict[str, str] = {}
    files: dict[str, bytes] = {}
    if not msg.is_multipart():
        return fields, files
    for part in msg.iter_parts():
        cd = part.get("Content-Disposition", "")
        name_match = re.search(r'name="([^"]+)"', cd)
        filename_match = re.search(r'filename="([^"]*)"', cd)
        if not name_match:
            continue
        name = name_match.group(1)
        payload = part.get_payload(decode=True) or b""
        if filename_match and filename_match.group(1):
            files[name] = payload
        else:
            try:
                fields[name] = payload.decode("utf-8")
            except UnicodeDecodeError:
                fields[name] = ""
    return fields, files
