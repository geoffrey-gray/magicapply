"""Local HTTP fixture that serves captured DOM fixtures for hermetic E2E.

Routes:

- ``GET /careers`` — JSON-LD listing for the Greenhouse pipeline test.
- ``GET /careers-cross-ats`` — four ATS JSON-LD blocks for acceptance.
- ``GET <live-capture-path>`` — promoted W.4 DOM snapshots (dry-run E2E).
- ``GET <e2e-smoke-path>`` — submittable HTML forms for yes-submit tests.
- ``POST /submit`` — records multipart submissions on ``.submissions``.

E2E-smoke captures live under ``tests/fixtures/captured/*-e2e-smoke-*``.
Live W.4 captures are promoted snapshots under ``tests/fixtures/captured/*``.
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
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_CAPTURED_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "captured"

# Submittable minimal forms — POST /submit assertions in E2E yes-submit tests.
_E2E_SMOKE_CAPTURES: dict[str, str] = {
    "greenhouse": "greenhouse-e2e-smoke-20260707",
    "workday": "workday-e2e-smoke-20260707",
    "lever": "lever-e2e-smoke-20260707",
    "ashby": "ashby-e2e-smoke-20260707",
    "generic": "custom-e2e-smoke-20260707",
}

# Promoted W.4 live DOM snapshots — dry-run E2E only (no traditional form POST).
_LIVE_CAPTURE_ROUTES: list[tuple[str, str]] = [
    (
        "greenhouse-reddit-20260707",
        "/job-boards.greenhouse.io/reddit/jobs/7772274",
    ),
    (
        "lever-foodsmart-20260707",
        "/jobs.lever.co/foodsmart/c711b611-ac13-4167-8b60-5c0adb32af26",
    ),
    (
        "ashby-trm-20260707",
        "/jobs.ashbyhq.com/trm-labs/b20af02a-0701-415e-9279-65ea5c2b6f12",
    ),
    (
        "workday-circle-staff-ds-20260707",
        "/circle.wd1.myworkdayjobs.com/en-US/Circle/job/Staff-Data-Scientist---Digital-Assets_JR101068",
    ),
]


def _load_captured_dom(capture_id: str) -> str:
    return (_CAPTURED_ROOT / capture_id / "dom.html").read_text(encoding="utf-8")


def _normalize_path(path: str) -> str:
    parsed = urlparse(path)
    return parsed.path.rstrip("/") or "/"


# --- JSON-LD careers fixtures (discovery tests) --------------------------------

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
        "description": "Design beautiful iOS surfaces. Portfolio required.",
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


_CROSS_ATS_JOBS: list[dict[str, Any]] = [
    {
        "path": "/greenhouse.io/senior-backend/apply",
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "description": "Python and distributed systems.",
    },
    {
        "path": "/myworkdayjobs.com/senior-platform/apply",
        "title": "Senior Platform Engineer",
        "company": "Beta",
        "location": "Remote",
        "description": "Distributed platform work.",
    },
    {
        "path": "/jobs.lever.co/senior-sre/apply",
        "title": "Senior SRE",
        "company": "Gamma",
        "location": "Remote",
        "description": "Reliability engineering.",
    },
    {
        "path": "/jobs.ashbyhq.com/staff-swe/application",
        "title": "Staff Software Engineer",
        "company": "Delta",
        "location": "Remote",
        "description": "Backend + platform.",
    },
]


def _cross_ats_careers_html(base_url: str) -> str:
    scripts = []
    for job in _CROSS_ATS_JOBS:
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
            "url": f"{base_url}{job['path']}",
            "description": job["description"],
        }
        scripts.append(
            f'<script type="application/ld+json">{json.dumps(payload)}</script>'
        )
    return (
        "<!doctype html><html><head><title>Cross-ATS Careers</title></head>"
        f"<body><h1>Cross-ATS Careers</h1>{''.join(scripts)}</body></html>"
    )


_THANK_YOU_HTML = "<!doctype html><html><body><h1>Thanks!</h1></body></html>"


def _resolve_capture_html(path: str) -> str | None:
    """Map a request path to a captured DOM, if any."""
    norm = _normalize_path(path)

    for capture_id, prefix in _LIVE_CAPTURE_ROUTES:
        if norm == prefix or norm.startswith(f"{prefix}/"):
            return _load_captured_dom(capture_id)

    if norm.startswith("/greenhouse.io/") and norm.endswith("/apply"):
        return _load_captured_dom(_E2E_SMOKE_CAPTURES["greenhouse"])
    if norm.startswith("/myworkdayjobs.com/") and norm.endswith("/apply"):
        return _load_captured_dom(_E2E_SMOKE_CAPTURES["workday"])
    if norm.startswith("/jobs.lever.co/") and norm.endswith("/apply"):
        return _load_captured_dom(_E2E_SMOKE_CAPTURES["lever"])
    if norm.startswith("/jobs.ashbyhq.com/") and (
        norm.endswith("/apply") or norm.endswith("/application")
    ):
        return _load_captured_dom(_E2E_SMOKE_CAPTURES["ashby"])
    if norm.startswith("/careers.example-custom.com/") and norm.endswith("/apply"):
        return _load_captured_dom(_E2E_SMOKE_CAPTURES["generic"])

    return None


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
                if self.path == "/careers-cross-ats":
                    self._html(_cross_ats_careers_html(base_url))
                    return
                captured = _resolve_capture_html(self.path)
                if captured is not None:
                    self._html(captured)
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


def live_capture_path(capture_id: str) -> str:
    """URL path prefix for a promoted live capture on the fixture server."""
    for cid, prefix in _LIVE_CAPTURE_ROUTES:
        if cid == capture_id:
            return prefix
    raise KeyError(f"unknown live capture_id: {capture_id!r}")


def _parse_multipart(
    content_type: str, body: bytes
) -> tuple[dict[str, str], dict[str, bytes]]:
    """Return (fields, files) parsed from a multipart/form-data POST."""
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