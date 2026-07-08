"""Rotating proxy pool for anti-bot discovery scraping.

`ProxyPool` holds a health-checked, cooldown-aware set of proxy servers.
Adapters ask for `pool.next()` before each protected fetch; on a bot-block
response they call `pool.burn(entry, reason)` and the pool refuses to hand
that proxy out again until its cooldown expires. `refresh()` re-loads
entries from the underlying `ProxyProvider` and health-checks them in
parallel.

The provider abstraction is intentionally small — one method, one string
name. That keeps the door open for future backends (self-hosted VPS pool,
commercial rotating residential proxies) without touching the pool code.
`FallbackProvider` walks children in order and returns the first non-empty
result — that's how the operator declares priority ("VPS first, then free
list, then hand-curated static list") without invoking pattern jargon.

Design references:

- ``docs/GOF_PATTERNS.md`` §Strategy — `ProxyProvider` Protocol mirrors
  `LLMClient` / `ATSHandler` Protocol shape.
- ``ARCHITECTURE.md`` §9 — proxy source URLs, health-check target, and
  cooldown live in `configs/base_config.yaml::proxies` (config over code).
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ---- ProxyEntry -----------------------------------------------------------


class ProxyEntry(BaseModel):
    """One proxy target. Serialises to Playwright's `{server, username?, password?}`
    dict via ``as_playwright_proxy()``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    server: str = Field(..., description="e.g. 'http://1.2.3.4:8080' or 'socks5://…'")
    username: str | None = None
    password: str | None = None

    def as_playwright_proxy(self) -> dict[str, str]:
        payload: dict[str, str] = {"server": self.server}
        if self.username:
            payload["username"] = self.username
        if self.password:
            payload["password"] = self.password
        return payload

    def as_httpx_proxy(self) -> str:
        """Return the URL form httpx wants (username/password inline)."""
        if not self.username:
            return self.server
        # `http://user:pass@host:port` shape
        scheme, sep, rest = self.server.partition("://")
        if not sep:
            return self.server
        auth = f"{self.username}:{self.password or ''}"
        return f"{scheme}://{auth}@{rest}"


# ---- Providers -----------------------------------------------------------


class ProxyProvider(Protocol):
    def name(self) -> str: ...
    def load(self) -> list[ProxyEntry]: ...


class StaticListProvider:
    """Operator-curated list from YAML config. The escape hatch for hand-
    verified proxies — always available regardless of external source
    status. Empty by default in the shipped `base_config.example.yaml`."""

    def __init__(self, entries: Sequence[str] | Sequence[ProxyEntry]) -> None:
        self._entries: list[ProxyEntry] = []
        for entry in entries:
            if isinstance(entry, ProxyEntry):
                self._entries.append(entry)
            elif isinstance(entry, str) and entry.strip():
                self._entries.append(_parse_proxy_string(entry))

    def name(self) -> str:
        return f"static({len(self._entries)})"

    def load(self) -> list[ProxyEntry]:
        return list(self._entries)


class FreeListScraperProvider:
    """Downloads one or more publicly-hosted proxy lists (raw text, one
    proxy per line) and parses them. Defaults ship three well-known
    community-maintained sources (TheSpeedX / roosterkid / monosans on
    GitHub) plus proxyscrape's free tier — all permissive-licensed.

    Non-`http(s)` schemes are prefixed with `http://` so free-list feeds
    that just publish `ip:port` still work. Malformed lines are skipped
    with a debug log; the health-check phase in `ProxyPool.refresh` is
    what actually drops dead endpoints.

    `max_entries` caps the returned pool size — free lists ship
    thousands of entries and health-checking them all can take minutes.
    Default 200 hits a sweet spot: at ~10% alive rate that yields ~20
    working proxies (enough for a full discover run without stalling
    the CLI at startup). Set to 0 for uncapped."""

    _DEFAULT_SOURCES: tuple[str, ...] = (
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=US",
    )

    def __init__(
        self,
        sources: Sequence[str] | None = None,
        *,
        http: httpx.Client | None = None,
        fetch_timeout_seconds: float = 10.0,
        max_entries: int = 200,
    ) -> None:
        self._sources: tuple[str, ...] = tuple(sources) if sources else self._DEFAULT_SOURCES
        self._http = http or httpx.Client(timeout=fetch_timeout_seconds, follow_redirects=True)
        self._max_entries = max(0, int(max_entries))

    def name(self) -> str:
        return f"free_list({len(self._sources)})"

    def load(self) -> list[ProxyEntry]:
        seen: set[str] = set()
        out: list[ProxyEntry] = []
        for url in self._sources:
            try:
                resp = self._http.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("proxy source %s unreachable: %s", url, exc)
                continue
            for line in resp.text.splitlines():
                try:
                    entry = _parse_proxy_line(line)
                except ValueError:
                    # Malformed line in a free-list feed is expected — the
                    # scraper skips and the health-check phase catches any
                    # syntactically-valid-but-dead endpoint.
                    continue
                if entry is None:
                    continue
                if entry.server in seen:
                    continue
                seen.add(entry.server)
                out.append(entry)
                if self._max_entries and len(out) >= self._max_entries:
                    return out
        return out


class FallbackProvider:
    """ProxyProvider that walks children in order and returns the first
    non-empty result. Failing / empty children are skipped so a single
    provider outage never starves the pool. Children may themselves be
    `FallbackProvider`s for nested priority trees.

    Pattern: hybrid of Composite (many-treat-as-one) and Chain of
    Responsibility (linear fallback). Kept in `infrastructure/browser/`
    because it's purely an infrastructure concern — different from the
    Composite / CoR uses deferred in ``docs/GOF_PATTERNS.md`` lines 75-81
    (form-field resolution / resume trees)."""

    def __init__(self, children: Sequence[ProxyProvider]) -> None:
        self._children: tuple[ProxyProvider, ...] = tuple(children)

    def name(self) -> str:
        return f"fallback({','.join(c.name() for c in self._children)})"

    def load(self) -> list[ProxyEntry]:
        for child in self._children:
            try:
                entries = child.load()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fallback child %s raised %s; falling through", child.name(), exc
                )
                continue
            if entries:
                logger.info(
                    "fallback yielded %d entries from %s", len(entries), child.name()
                )
                return entries
        return []


# ---- Pool -----------------------------------------------------------------


class ProxyPool:
    """Health-checked rotating pool. Thread-safe for `next()` / `burn()` —
    the discovery pipeline is single-threaded today but adapters may hand
    proxies to Playwright contexts on multiple worker threads.

    Cooldown semantics: a burned proxy stays out of rotation for
    `cooldown_seconds` (default 900 s = 15 min). After that, `refresh()`
    will health-check it again on the next scheduled refresh. Adapters
    should not need to touch cooldown state directly — call `burn(entry,
    reason)` on a bad response and move on.

    Health check: a HEAD request against `health_check_url` (default
    `https://httpbin.org/ip`) via the proxy under test. Parallelised
    across `health_check_workers` threads to keep refresh cost bounded on
    large free-list pools."""

    def __init__(
        self,
        provider: ProxyProvider,
        *,
        health_check_url: str = "https://httpbin.org/ip",
        health_check_timeout_seconds: float = 5.0,
        health_check_workers: int = 20,
        cooldown_seconds: int = 900,
        clock: object | None = None,
    ) -> None:
        self._provider = provider
        self._health_check_url = health_check_url
        self._health_check_timeout_seconds = health_check_timeout_seconds
        self._health_check_workers = max(1, int(health_check_workers))
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._alive: list[ProxyEntry] = []
        self._alive_ptr: int = 0
        self._burned: dict[str, datetime] = {}  # server → burn time
        self._lock = threading.Lock()

    # -- Public API --

    def refresh(self) -> None:
        """Reload entries from the provider, drop still-cooling-down ones,
        health-check the rest in parallel, and replace the alive set."""
        raw = self._provider.load()
        now = self._now()
        eligible: list[ProxyEntry] = []
        for entry in raw:
            burn_time = self._burned.get(entry.server)
            if burn_time is not None and now - burn_time < self._cooldown:
                continue
            eligible.append(entry)

        alive = self._parallel_health_check(eligible)
        with self._lock:
            self._alive = alive
            self._alive_ptr = 0
        logger.info(
            "proxy pool refreshed: %d alive of %d eligible (provider=%s)",
            len(alive), len(eligible), self._provider.name(),
        )

    def next(self) -> ProxyEntry | None:
        """Round-robin next alive proxy. Returns None when the pool is
        empty; adapter code decides whether to fall back to direct fetch
        or raise `SourceError`."""
        with self._lock:
            if not self._alive:
                return None
            entry = self._alive[self._alive_ptr % len(self._alive)]
            self._alive_ptr = (self._alive_ptr + 1) % max(1, len(self._alive))
            return entry

    def burn(self, entry: ProxyEntry, reason: str) -> None:
        """Mark a proxy dead for the cooldown window and remove it from
        rotation immediately. Called by adapters when a response looks
        like a bot-block or the proxy is otherwise misbehaving."""
        now = self._now()
        with self._lock:
            self._burned[entry.server] = now
            self._alive = [e for e in self._alive if e.server != entry.server]
            if self._alive_ptr >= len(self._alive):
                self._alive_ptr = 0
        logger.info(
            "burned proxy %s (reason=%s); pool size now %d",
            entry.server, reason, len(self._alive),
        )

    def alive_count(self) -> int:
        with self._lock:
            return len(self._alive)

    # -- Internals --

    def _now(self) -> datetime:
        return self._clock() if callable(self._clock) else datetime.now(UTC)

    def _parallel_health_check(self, candidates: list[ProxyEntry]) -> list[ProxyEntry]:
        if not candidates:
            return []
        # Health checks run in parallel for speed but must yield a
        # deterministic order — otherwise `next()` round-robin becomes
        # thread-schedule-dependent, which is confusing to test and to
        # reason about at the CLI. Preserve the candidate order.
        alive_set: set[str] = set()
        with ThreadPoolExecutor(max_workers=self._health_check_workers) as pool:
            futures = {pool.submit(self._probe, e): e for e in candidates}
            for fut in as_completed(futures):
                if fut.result():
                    alive_set.add(futures[fut].server)
        return [e for e in candidates if e.server in alive_set]

    def _probe(self, entry: ProxyEntry) -> bool:
        try:
            client = httpx.Client(
                proxy=entry.as_httpx_proxy(),
                timeout=self._health_check_timeout_seconds,
                follow_redirects=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("bad proxy config %s: %s", entry.server, exc)
            return False
        try:
            resp = client.head(self._health_check_url)
            if resp.status_code >= 400:
                # Some httpbin endpoints don't support HEAD — retry with GET.
                resp = client.get(self._health_check_url)
            return resp.status_code < 400
        except Exception:  # noqa: BLE001
            return False
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


# ---- Parsing helpers -----------------------------------------------------


_HOST_PORT_RE = re.compile(r"^\s*(?P<host>[^:/\s]+):(?P<port>\d+)\s*$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def _parse_proxy_line(line: str) -> ProxyEntry | None:
    """Parse one line from a free-list feed. Accepts `host:port`,
    `scheme://host:port`, `user:pass@host:port`, or a scheme-prefixed
    variant thereof. Returns None on comments, blank lines, or unparseable
    input. Non-scheme entries default to `http://`."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    return _parse_proxy_string(stripped)


def _parse_proxy_string(raw: str) -> ProxyEntry:
    text = raw.strip()
    # Extract optional userinfo before splitting scheme.
    scheme_match = _SCHEME_RE.match(text)
    scheme = "http"
    if scheme_match:
        scheme = scheme_match.group(0)[:-3]  # strip "://"
        text = text[scheme_match.end():]
    userinfo: str | None = None
    if "@" in text:
        userinfo, _, text = text.partition("@")
    host_match = _HOST_PORT_RE.match(text)
    if not host_match:
        raise ValueError(f"unparseable proxy line: {raw!r}")
    host, port = host_match.group("host"), host_match.group("port")
    server = f"{scheme}://{host}:{port}"
    if userinfo is None:
        return ProxyEntry(server=server)
    user, _, password = userinfo.partition(":")
    return ProxyEntry(server=server, username=user, password=password or None)


# ---- Sampling helpers (used by session UA/viewport rotation later) --------


def sample_from(seq: Sequence[str] | Sequence[tuple[int, int]]) -> object:
    """Deterministic-friendly random choice. Kept here so tests can
    monkeypatch `random.choice` on a single import site."""
    if not seq:
        raise ValueError("cannot sample from empty sequence")
    return random.choice(list(seq))


__all__ = [
    "ProxyEntry",
    "ProxyProvider",
    "StaticListProvider",
    "FreeListScraperProvider",
    "FallbackProvider",
    "ProxyPool",
    "sample_from",
]
