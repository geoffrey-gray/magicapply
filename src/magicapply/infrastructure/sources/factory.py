"""Build a concrete JobSource from a config Source entry."""

from __future__ import annotations

from magicapply.config.models import (
    CareerPageSource,
    GlassdoorSource,
    GreenhouseSource,
    IndeedSource,
    JobUrlSource,
    LinkedInSource,
    ProxyPoolConfig,
)
from magicapply.infrastructure.browser.proxy_pool import (
    FallbackProvider,
    FreeListScraperProvider,
    ProxyPool,
    ProxyProvider,
    StaticListProvider,
)
from magicapply.infrastructure.sources.base import JobSource
from magicapply.infrastructure.sources.custom_url import (
    CareerPageAdapter,
    JobUrlAdapter,
)
from magicapply.infrastructure.sources.glassdoor import GlassdoorAdapter
from magicapply.infrastructure.sources.greenhouse import GreenhouseAdapter
from magicapply.infrastructure.sources.indeed import IndeedAdapter
from magicapply.infrastructure.sources.linkedin import LinkedInAdapter


def build_source(
    config: (
        CareerPageSource
        | JobUrlSource
        | LinkedInSource
        | IndeedSource
        | GlassdoorSource
        | GreenhouseSource
    ),
    *,
    proxy_pool: ProxyPool | None = None,
) -> JobSource:
    """Return the concrete adapter for a Source config entry.

    Adapters that participate in proxy rotation (Indeed / Glassdoor)
    accept the `proxy_pool` kwarg; others ignore it. See
    `ARCHITECTURE.md` §9 and the plan file for the rationale — proxy
    scraping is scoped to sources with Cloudflare-heavy front doors."""
    if isinstance(config, CareerPageSource):
        return CareerPageAdapter.from_config(config)
    if isinstance(config, JobUrlSource):
        return JobUrlAdapter.from_config(config)
    if isinstance(config, LinkedInSource):
        return LinkedInAdapter.from_config(config)
    if isinstance(config, IndeedSource):
        return IndeedAdapter.from_config(config, proxy_pool=proxy_pool)
    if isinstance(config, GlassdoorSource):
        return GlassdoorAdapter.from_config(config, proxy_pool=proxy_pool)
    if isinstance(config, GreenhouseSource):
        return GreenhouseAdapter.from_config(config)
    raise TypeError(f"unknown source type: {type(config).__name__}")


def build_proxy_provider(config: ProxyPoolConfig) -> ProxyProvider | None:
    """Assemble a `ProxyProvider` from a list of `ProxyProviderConfig`
    entries. Wraps multiple entries in a `FallbackProvider` — order in
    the YAML is fallback priority. Returns None when the pool is disabled
    or no providers are configured."""
    if not config.enabled or not config.providers:
        return None
    children: list[ProxyProvider] = []
    for entry in config.providers:
        provider = _build_one_provider(
            entry.type, entry.sources, entry.entries, entry.max_entries
        )
        if provider is not None:
            children.append(provider)
    if not children:
        return None
    if len(children) == 1:
        return children[0]
    return FallbackProvider(children)


def build_proxy_pool(config: ProxyPoolConfig) -> ProxyPool | None:
    """One-liner from `LoadedConfig.base.proxies` → `ProxyPool` (or None
    when the pool is disabled). Called once by the composition root and
    passed to `build_source` per adapter."""
    provider = build_proxy_provider(config)
    if provider is None:
        return None
    pool = ProxyPool(
        provider,
        health_check_url=config.health_check_url,
        health_check_timeout_seconds=config.health_check_timeout_seconds,
        health_check_workers=config.health_check_workers,
        cooldown_seconds=config.cooldown_seconds,
    )
    pool.refresh()
    return pool


def _build_one_provider(
    provider_type: str,
    sources: list[str],
    entries: list[str],
    max_entries: int,
) -> ProxyProvider | None:
    """Small dispatch table. Adding a new provider is one branch here
    plus one new class in `proxy_pool.py`."""
    if provider_type == "static_list":
        return StaticListProvider(entries)
    if provider_type == "free_list_scraper":
        # No `sources` → use the FreeListScraperProvider defaults.
        return FreeListScraperProvider(sources or None, max_entries=max_entries)
    # Unknown provider type is a config error, but we log-and-skip so a
    # forward-compat future type name doesn't hard-crash old operators.
    import logging

    logging.getLogger(__name__).warning(
        "unknown proxy provider type %r; skipping", provider_type
    )
    return None
