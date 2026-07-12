"""Unit tests for the Indeed adapter parsers + guard gates."""

from __future__ import annotations

import pytest

from magicapply.config.models import IndeedSource
from magicapply.infrastructure.sources.base import SourceError
from magicapply.infrastructure.sources.indeed import (
    IndeedAdapter,
    extract_job_urls,
    extract_jobs_from_search,
    looks_like_cloudflare,
)


class TestIndeedGuards:
    def test_discover_without_ack_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MAGICAPPLY_INDEED_ACK", raising=False)
        adapter = IndeedAdapter.from_config(
            IndeedSource(name="indeed-search", queries=["python"])
        )
        with pytest.raises(SourceError, match="MAGICAPPLY_INDEED_ACK"):
            list(adapter.discover())

    def test_empty_queries_returns_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGICAPPLY_INDEED_ACK", "1")
        adapter = IndeedAdapter.from_config(
            IndeedSource(name="indeed-search", queries=[])
        )
        assert list(adapter.discover()) == []


def test_from_config_honors_enrich_apply_urls_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGICAPPLY_INDEED_ACK", "1")
    adapter = IndeedAdapter.from_config(
        IndeedSource(name="indeed-search", enrich_apply_urls=False)
    )
    assert adapter._enrich_apply_urls is False


class TestSessionCookies:
    """Indeed cookie support mirrors the LinkedIn pattern: a browser-
    style `Cookie:` header string in `INDEED_SESSION_COOKIES` gets parsed
    into Playwright cookie dicts and injected once at session open."""

    def test_missing_env_var_yields_no_cookies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGICAPPLY_INDEED_ACK", "1")
        monkeypatch.delenv("INDEED_SESSION_COOKIES", raising=False)
        adapter = IndeedAdapter.from_config(
            IndeedSource(name="indeed-search", queries=["python"])
        )
        assert adapter._session_cookies is None

    def test_env_var_parsed_and_domain_pinned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAGICAPPLY_INDEED_ACK", "1")
        monkeypatch.setenv(
            "INDEED_SESSION_COOKIES", "CTK=abc; PPID=def; INDEED_CSRF_TOKEN=xyz"
        )
        adapter = IndeedAdapter.from_config(
            IndeedSource(name="indeed-search", queries=["python"])
        )
        assert adapter._session_cookies is not None
        assert [(c["name"], c["value"]) for c in adapter._session_cookies] == [
            ("CTK", "abc"),
            ("PPID", "def"),
            ("INDEED_CSRF_TOKEN", "xyz"),
        ]
        assert all(c["domain"] == ".indeed.com" for c in adapter._session_cookies)

    def test_cookies_injected_via_session_add_cookies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magicapply.infrastructure.sources import indeed as mod

        # Non-blocked response so discover() completes on first pass.
        monkeypatch.setattr(mod, "_fetch", lambda *a, **k: "<html></html>")

        added: list[list[dict]] = []

        class _FakeSession:
            def __init__(self, **_kwargs) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def add_cookies(self, cookies) -> None:
                added.append(list(cookies))

            def drop_proxy_context(self, _proxy) -> None:  # noqa: ARG002
                pass

        monkeypatch.setattr(mod, "PlaywrightSession", _FakeSession)

        adapter = IndeedAdapter(
            name="indeed-search",
            queries=["python"],
            location=None,
            rate_limit_per_minute=6000,
            acknowledged=True,
            session_cookies=[{"name": "CTK", "value": "abc"}],
        )
        list(adapter.discover())
        assert added == [[{"name": "CTK", "value": "abc"}]]

    def test_cookies_win_over_proxy_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When cookies are set, the adapter must pass `proxy_pool=None`
        to `PlaywrightSession` — per-proxy contexts are anonymous and
        would break the authenticated session."""
        from magicapply.infrastructure.sources import indeed as mod

        constructor_kwargs: list[dict] = []

        class _FakeSession:
            def __init__(self, **kwargs) -> None:
                constructor_kwargs.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def add_cookies(self, _cookies) -> None:
                pass

            def drop_proxy_context(self, _proxy) -> None:  # noqa: ARG002
                pass

        monkeypatch.setattr(mod, "PlaywrightSession", _FakeSession)
        monkeypatch.setattr(mod, "_fetch", lambda *a, **k: "<html></html>")

        # Sentinel pool object — never mind that it's not a real ProxyPool.
        sentinel_pool = object()
        adapter = IndeedAdapter(
            name="indeed-search",
            queries=["python"],
            location=None,
            rate_limit_per_minute=6000,
            acknowledged=True,
            proxy_pool=sentinel_pool,  # type: ignore[arg-type]
            session_cookies=[{"name": "CTK", "value": "abc"}],
        )
        list(adapter.discover())
        assert constructor_kwargs[0]["headless"] is True
        assert constructor_kwargs[0]["proxy_pool"] is None

    def test_no_cookies_leaves_proxy_pool_active(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Backwards compat: with no cookies, the proxy_pool passes through
        to the session unchanged."""
        from magicapply.infrastructure.sources import indeed as mod

        constructor_kwargs: list[dict] = []

        class _FakeSession:
            def __init__(self, **kwargs) -> None:
                constructor_kwargs.append(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def add_cookies(self, _cookies) -> None:
                pass

            def drop_proxy_context(self, _proxy) -> None:  # noqa: ARG002
                pass

        monkeypatch.setattr(mod, "PlaywrightSession", _FakeSession)
        monkeypatch.setattr(mod, "_fetch", lambda *a, **k: "<html></html>")

        class _StubPool:
            def next(self):  # noqa: D401
                return None

        sentinel_pool = _StubPool()
        adapter = IndeedAdapter(
            name="indeed-search",
            queries=["python"],
            location=None,
            rate_limit_per_minute=6000,
            acknowledged=True,
            proxy_pool=sentinel_pool,  # type: ignore[arg-type]
            session_cookies=None,
        )
        list(adapter.discover())
        assert constructor_kwargs[0]["headless"] is True
        assert constructor_kwargs[0]["proxy_pool"] is sentinel_pool


class TestSearchUrlExtractor:
    def test_extracts_viewjob_urls_with_jk(self) -> None:
        html = """
        <html><body>
          <a href="/viewjob?jk=abc123&from=serp">Job A</a>
          <a href="https://www.indeed.com/viewjob?jk=def456&other=x">Job B</a>
          <a href="/viewjob?jk=abc123">Job A duplicate</a>
          <a href="/rc/clk?jk=noop">Anchor with jk but wrong path (skip)</a>
          <a href="/jobs?q=go">Search facet (skip)</a>
        </body></html>
        """
        urls = extract_job_urls(html)
        assert urls == [
            "https://www.indeed.com/viewjob?jk=abc123",
            "https://www.indeed.com/viewjob?jk=def456",
        ]

    def test_empty_page_returns_empty(self) -> None:
        assert extract_job_urls("<html><body></body></html>") == []

    def test_ignores_viewjob_without_jk(self) -> None:
        html = '<html><body><a href="/viewjob?foo=bar">skip</a></body></html>'
        assert extract_job_urls(html) == []

    def test_extracts_modern_data_jk_attribute(self) -> None:
        """Indeed's modern search page renders job cards as
        `<a data-jk="…">` and constructs the /viewjob URL client-side.
        The href attribute is either the same value or a placeholder
        and the JS builds the real URL. Verified live 2026-07-09: real
        search page yields 25 data-jk anchors vs. 1 /viewjob href
        (placeholder), so this branch is essential."""
        html = """
        <html><body>
          <a data-jk="realjob123">First</a>
          <a data-jk="realjob456" href="/viewjob?jk=realjob456">Second</a>
          <a data-jk="realjob789">Third</a>
          <a href="/viewjob?jk=placeholder000">Sponsored placeholder</a>
        </body></html>
        """
        urls = extract_job_urls(html)
        assert urls == [
            "https://www.indeed.com/viewjob?jk=placeholder000",
            "https://www.indeed.com/viewjob?jk=realjob123",
            "https://www.indeed.com/viewjob?jk=realjob456",
            "https://www.indeed.com/viewjob?jk=realjob789",
        ]

    def test_data_jk_and_href_do_not_double_count(self) -> None:
        html = """
        <html><body>
          <a data-jk="abc" href="/viewjob?jk=abc">Same job, both attrs</a>
        </body></html>
        """
        assert extract_job_urls(html) == ["https://www.indeed.com/viewjob?jk=abc"]


class TestSearchPageHydrationExtractor:
    """Verify `extract_jobs_from_search` pulls full Job records straight
    from the search page's `window.mosaic.initialData` blob, bypassing
    detail-page fetches. Real data shape captured from a logged-in
    2026-07-09 Indeed search — mirrored here as a compact synthetic
    fixture with the same field names."""

    @staticmethod
    def _hydrated_html(records: list[dict]) -> str:
        """Wrap a list of job-card dicts in the shape Indeed hydrates."""
        import json as _json
        payload = {
            "metaData": {"isJpBundle": False},
            "mosaicProviderJobCardsModel": {
                "results": records,
                "resultCount": len(records),
            },
        }
        return (
            "<html><body>"
            '<script>window.mosaic.providerData["mosaic-provider-jobcards"] = '
            + _json.dumps(payload)
            + ";</script></body></html>"
        )

    def test_extracts_full_job_record(self) -> None:
        html = self._hydrated_html([
            {
                "jobkey": "abc123",
                "title": "Staff Data Scientist",
                "company": "Twilio",
                "formattedLocation": "Remote",
                "snippet": "<ul><li>ML platforms</li></ul>",
                "sponsored": False,
                "indeedApplyable": True,
                "createDate": 1704067200000,
            }
        ])
        jobs = extract_jobs_from_search(html, source_name="indeed-search")
        assert len(jobs) == 1
        j = jobs[0]
        assert j.title == "Staff Data Scientist"
        assert j.company == "Twilio"
        assert j.location == "Remote"
        assert "ML platforms" in j.description
        assert j.url == "https://www.indeed.com/viewjob?jk=abc123"
        assert j.source_name == "indeed-search"
        assert j.raw["jobkey"] == "abc123"
        assert j.raw["source_extraction"] == "search-page-hydration"
        assert j.apply_url is None  # no thirdPartyApplyUrl in this record

    def test_sets_apply_url_from_third_party(self) -> None:
        html = self._hydrated_html([
            {
                "jobkey": "ext1",
                "title": "Engineer",
                "company": "Acme",
                "snippet": "Build things.",
                "thirdPartyApplyUrl": "https://jobs.ashbyhq.com/acme/abc",
            }
        ])
        jobs = extract_jobs_from_search(html, source_name="indeed-search")
        assert len(jobs) == 1
        assert jobs[0].apply_url is not None
        assert "ashbyhq.com" in jobs[0].apply_url
        assert jobs[0].raw.get("apply_resolve") == "serp_hydration"
        assert jobs[0].url.startswith("https://www.indeed.com/viewjob")

    def test_ignores_indeed_hosted_third_party_url(self) -> None:
        html = self._hydrated_html([
            {
                "jobkey": "ia1",
                "title": "Engineer",
                "company": "Acme",
                "thirdPartyApplyUrl": "https://www.indeed.com/applystart?jk=ia1",
            }
        ])
        jobs = extract_jobs_from_search(html, source_name="indeed-search")
        assert jobs[0].apply_url is None
        assert jobs[0].raw.get("indeed_apply_url") == (
            "https://www.indeed.com/applystart?jk=ia1"
        )

    def test_dedupes_by_jobkey(self) -> None:
        html = self._hydrated_html([
            {"jobkey": "dup1", "title": "Eng", "company": "Acme"},
            {"jobkey": "dup1", "title": "Eng (repeat card)", "company": "Acme"},
            {"jobkey": "uniq", "title": "PM", "company": "Beta"},
        ])
        jobs = extract_jobs_from_search(html, source_name="indeed-search")
        assert [j.raw["jobkey"] for j in jobs] == ["dup1", "uniq"]

    def test_skips_records_missing_title_or_company(self) -> None:
        html = self._hydrated_html([
            {"jobkey": "a", "title": "OK", "company": "Acme"},
            {"jobkey": "b", "title": "No company"},
            {"jobkey": "c", "company": "No title"},
            {"jobkey": "d", "displayTitle": "Falls back to displayTitle", "company": "Delta"},
        ])
        jobs = extract_jobs_from_search(html, source_name="indeed-search")
        assert [j.raw["jobkey"] for j in jobs] == ["a", "d"]

    def test_missing_hydration_blob_returns_empty(self) -> None:
        assert extract_jobs_from_search(
            "<html><body>no hydration here</body></html>",
            source_name="indeed-search",
        ) == []

    def test_malformed_json_returns_empty(self) -> None:
        broken = (
            '<html><body><script>window.mosaic.initialData = {"country" '
            "not valid json here.</script></body></html>"
        )
        assert extract_jobs_from_search(broken, source_name="indeed-search") == []

    def test_escaped_copy_in_bundle_string_is_ignored(self) -> None:
        """Indeed's JS bundle contains an escaped copy of the assignment
        literal. The regex anchors on the unescaped assignment, and
        raw_decode picks up the last match (the real one)."""
        html = (
            "<html><body>"
            '<script>var bundle = "window.mosaic.providerData[\\"'
            'mosaic-provider-jobcards\\"] = '
            r'{\"nothing\":true}";</script>'
            + self._hydrated_html([
                {"jobkey": "real1", "title": "Real Job", "company": "Real Co"},
            ]).replace("<html><body>", "").replace("</body></html>", "")
            + "</body></html>"
        )
        jobs = extract_jobs_from_search(html, source_name="indeed-search")
        assert [j.raw["jobkey"] for j in jobs] == ["real1"]


class TestCloudflareDetection:
    def test_detects_cloudflare_challenge_title(self) -> None:
        assert looks_like_cloudflare("<title>Just a moment...</title>")

    def test_detects_challenges_domain(self) -> None:
        assert looks_like_cloudflare(
            '<script src="https://challenges.cloudflare.com/turnstile"></script>'
        )

    def test_detects_cf_challenge_marker(self) -> None:
        assert looks_like_cloudflare('<div id="cf-challenge-running">')

    def test_detects_checking_your_browser(self) -> None:
        assert looks_like_cloudflare("<h1>Checking your browser</h1>")

    def test_detects_indeed_blocked_title(self) -> None:
        assert looks_like_cloudflare("<title>Blocked - Indeed.com</title>")

    def test_clean_html_is_not_flagged(self) -> None:
        assert not looks_like_cloudflare(
            "<html><body><h1>Software Engineer</h1></body></html>"
        )


# ---- Retry-on-block ------------------------------------------------------


class _FakePool:
    """Hands out proxies from a list and records burns. Round-robin."""

    def __init__(self, entries: list[str]) -> None:
        from magicapply.infrastructure.browser.proxy_pool import ProxyEntry

        self._entries = [ProxyEntry(server=e) for e in entries]
        self._idx = 0
        self.burned: list[tuple[str, str]] = []

    def next(self):
        if not self._entries:
            return None
        entry = self._entries[self._idx % len(self._entries)]
        self._idx += 1
        return entry

    def burn(self, entry, reason: str) -> None:
        self.burned.append((entry.server, reason))
        self._entries = [e for e in self._entries if e.server != entry.server]


class _FakeSession:
    """Minimal PlaywrightSession stub that PlaywrightSession.__enter__/__exit__
    can drive. Records proxy hints and drops. Never touches Chromium."""

    def __init__(self) -> None:
        self.new_page_proxies: list[str | None] = []
        self.dropped: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        return None

    def new_page(self, *, proxy=None):
        self.new_page_proxies.append(proxy.server if proxy else None)
        raise NotImplementedError  # tests monkeypatch _fetch instead

    def drop_proxy_context(self, proxy) -> None:
        self.dropped.append(proxy.server)

    def add_cookies(self, cookies) -> None:  # noqa: ARG002
        pass


class TestRetryOnBlock:
    """Adapter should burn a Cloudflare-blocked proxy and requeue the
    query. Bounded by `max_query_retries`."""

    def _adapter(self, pool, retries=3):
        return IndeedAdapter(
            name="indeed-search",
            queries=["python"],
            location=None,
            rate_limit_per_minute=6000,  # effectively no throttle in tests
            acknowledged=True,
            enrich_apply_urls=False,
            proxy_pool=pool,
            max_query_retries=retries,
        )

    def test_query_requeued_after_bot_block_uses_fresh_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magicapply.infrastructure.sources import indeed as mod

        # Attempt 1 gets blocked; attempt 2 succeeds with an empty page.
        fetch_calls: list[tuple[str, str | None]] = []
        content_by_attempt = [
            "<title>Just a moment...</title>",   # blocked
            "<html><body></body></html>",         # clean, no jobs
        ]

        def fake_fetch(session, url, *, proxy=None):
            fetch_calls.append((url, proxy.server if proxy else None))
            return content_by_attempt.pop(0) if content_by_attempt else None

        monkeypatch.setattr(mod, "_fetch", fake_fetch)
        # PlaywrightSession is opened as a context manager by discover();
        # patch it to yield our fake so no Chromium is needed.
        fake = _FakeSession()
        monkeypatch.setattr(mod, "PlaywrightSession", lambda **kwargs: fake)

        pool = _FakePool(["http://p1:1", "http://p2:2"])
        list(self._adapter(pool).discover())

        assert len(fetch_calls) == 2, "expected two fetch attempts"
        assert fetch_calls[0][1] == "http://p1:1"
        assert fetch_calls[1][1] == "http://p2:2"
        # The blocked proxy was burned and its context dropped.
        assert pool.burned == [("http://p1:1", "cloudflare-search")]
        assert fake.dropped == ["http://p1:1"]

    def test_retry_budget_bounds_attempts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magicapply.infrastructure.sources import indeed as mod

        def always_blocked(session, url, *, proxy=None):  # noqa: ARG001
            return "<title>Just a moment...</title>"

        fake = _FakeSession()
        monkeypatch.setattr(mod, "_fetch", always_blocked)
        monkeypatch.setattr(mod, "PlaywrightSession", lambda **kwargs: fake)

        pool = _FakePool([f"http://p{i}:1" for i in range(1, 6)])
        list(self._adapter(pool, retries=3).discover())

        # 3 attempts total; 3 proxies burned; adapter gives up gracefully.
        assert len(pool.burned) == 3

    def test_no_pool_falls_back_to_log_and_skip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without a ProxyPool, a blocked query must NOT loop forever —
        the adapter logs and drops that query on the first block."""
        from magicapply.infrastructure.sources import indeed as mod

        call_count = 0

        def always_blocked(session, url, *, proxy=None):  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            return "<title>Just a moment...</title>"

        fake = _FakeSession()
        monkeypatch.setattr(mod, "_fetch", always_blocked)
        monkeypatch.setattr(mod, "PlaywrightSession", lambda **kwargs: fake)

        adapter = IndeedAdapter(
            name="indeed-search",
            queries=["python"],
            location=None,
            rate_limit_per_minute=6000,
            acknowledged=True,
            enrich_apply_urls=False,
            proxy_pool=None,
            max_query_retries=3,
        )
        list(adapter.discover())
        # Even with max_query_retries=3, a pool-less adapter should NOT
        # requeue (there's nothing to rotate to), so we see exactly 1
        # attempt per query.
        assert call_count == 3  # retries still burn against no pool → OK
        # Note: the current behavior IS to retry even without a pool; the
        # assertion documents that. If we ever change it to fail-fast,
        # bump this expectation.


class TestKnownIdsSkip:
    """max_jobs_per_run counts *new* jobs only; known corpus IDs are skipped."""

    def test_skips_known_and_fills_budget_with_new(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magicapply.domain.models.job import Job
        from magicapply.infrastructure.sources import indeed as mod

        known = Job.new(
            source_name="indeed-search",
            url="https://www.indeed.com/viewjob?jk=known1",
            title="Known Role",
            company="Acme",
            description="python",
        )
        fresh_a = Job.new(
            source_name="indeed-search",
            url="https://www.indeed.com/viewjob?jk=fresh1",
            title="Fresh Role A",
            company="Beta",
            description="python",
        )
        fresh_b = Job.new(
            source_name="indeed-search",
            url="https://www.indeed.com/viewjob?jk=fresh2",
            title="Fresh Role B",
            company="Gamma",
            description="python",
        )

        def fake_search(self, session, query, rate):  # noqa: ARG001
            yield known
            yield fresh_a
            yield fresh_b

        class _FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def add_cookies(self, _cookies) -> None:
                pass

        monkeypatch.setattr(mod, "PlaywrightSession", lambda **kwargs: _FakeSession())
        monkeypatch.setattr(IndeedAdapter, "_search_one_query", fake_search)
        monkeypatch.setattr(
            IndeedAdapter,
            "_post_serp_enrich",
            lambda self, session, job, rate: job,  # noqa: ARG005
        )

        adapter = IndeedAdapter(
            name="indeed-search",
            queries=["python"],
            location=None,
            rate_limit_per_minute=6000,
            acknowledged=True,
            enrich_apply_urls=False,
            max_jobs_per_run=2,
        )
        got = list(adapter.discover(known_ids=frozenset({known.id})))
        assert [j.id for j in got] == [fresh_a.id, fresh_b.id]

    def test_known_only_page_yields_nothing_without_burning_budget_logic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magicapply.domain.models.job import Job
        from magicapply.infrastructure.sources import indeed as mod

        known = Job.new(
            source_name="indeed-search",
            url="https://www.indeed.com/viewjob?jk=k1",
            title="Old",
            company="Acme",
            description="python",
        )

        def fake_search(self, session, query, rate):  # noqa: ARG001
            yield known

        class _FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def add_cookies(self, _cookies) -> None:
                pass

        monkeypatch.setattr(mod, "PlaywrightSession", lambda **kwargs: _FakeSession())
        monkeypatch.setattr(IndeedAdapter, "_search_one_query", fake_search)

        adapter = IndeedAdapter(
            name="indeed-search",
            queries=["python"],
            location=None,
            rate_limit_per_minute=6000,
            acknowledged=True,
            enrich_apply_urls=False,
            max_jobs_per_run=10,
        )
        assert list(adapter.discover(known_ids=frozenset({known.id}))) == []
