"""Tests for the shared parse_cookie_string helper."""

from __future__ import annotations

import pytest

from magicapply.infrastructure.sources.base import parse_cookie_string


class TestParseCookieString:
    def test_single_pair(self) -> None:
        cookies = parse_cookie_string("name=value", domain=".indeed.com")
        assert cookies == [
            {
                "name": "name",
                "value": "value",
                "domain": ".indeed.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        ]

    def test_multiple_pairs(self) -> None:
        cookies = parse_cookie_string(
            "CTK=abc; PPID=def; INDEED_CSRF_TOKEN=xyz",
            domain=".indeed.com",
        )
        assert [(c["name"], c["value"]) for c in cookies] == [
            ("CTK", "abc"),
            ("PPID", "def"),
            ("INDEED_CSRF_TOKEN", "xyz"),
        ]
        assert all(c["domain"] == ".indeed.com" for c in cookies)

    def test_whitespace_tolerated_around_pairs_and_names(self) -> None:
        cookies = parse_cookie_string(
            "  CTK = abc ;   PPID = def  ",
            domain=".indeed.com",
        )
        assert [(c["name"], c["value"]) for c in cookies] == [
            ("CTK", "abc"),
            ("PPID", "def"),
        ]

    def test_empty_pairs_skipped(self) -> None:
        # Trailing / leading / doubled semicolons happen when operators
        # copy-paste and trim inconsistently.
        cookies = parse_cookie_string(
            "; CTK=abc;;PPID=def;;;",
            domain=".indeed.com",
        )
        assert [c["name"] for c in cookies] == ["CTK", "PPID"]

    def test_malformed_pair_missing_equals_skipped(self) -> None:
        cookies = parse_cookie_string(
            "CTK=abc; garbage_no_equals; PPID=def",
            domain=".indeed.com",
        )
        assert [c["name"] for c in cookies] == ["CTK", "PPID"]

    def test_pair_with_empty_value_kept(self) -> None:
        # `NAME=` is a legitimate shape (deletes the cookie on some sites).
        cookies = parse_cookie_string("SURF=", domain=".indeed.com")
        assert cookies[0]["value"] == ""

    def test_pair_with_empty_name_dropped(self) -> None:
        cookies = parse_cookie_string("=orphan_value; CTK=abc", domain=".indeed.com")
        assert [c["name"] for c in cookies] == ["CTK"]

    def test_empty_string_yields_empty_list(self) -> None:
        assert parse_cookie_string("", domain=".indeed.com") == []

    def test_value_containing_equals_preserved(self) -> None:
        # Base64-style values often contain `=` padding.
        cookies = parse_cookie_string(
            "SURF=YWJjPT09; PPID=xyz==",
            domain=".indeed.com",
        )
        assert cookies[0]["value"] == "YWJjPT09"
        assert cookies[1]["value"] == "xyz=="

    def test_domain_pinned_regardless_of_cookie_content(self) -> None:
        cookies = parse_cookie_string(
            "gdSession=xyz", domain=".glassdoor.com"
        )
        assert cookies[0]["domain"] == ".glassdoor.com"

    def test_defaults_match_existing_single_cookie_helpers(self) -> None:
        """The path/httpOnly/secure/sameSite defaults must match what
        `_li_at_cookie` and `_session_cookie` already emit — otherwise
        Playwright treats the new cookies differently and auth breaks."""
        c = parse_cookie_string("x=y", domain=".example.com")[0]
        assert c["path"] == "/"
        assert c["httpOnly"] is True
        assert c["secure"] is True
        assert c["sameSite"] == "None"
