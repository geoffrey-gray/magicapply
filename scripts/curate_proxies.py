#!/usr/bin/env python3
"""Curate a small working proxy list from public free feeds.

Aggregates multiple community-maintained proxy lists, health-checks each
candidate against a Cloudflare-adjacent target (`--target`, default
Indeed's homepage), and emits a YAML fragment the operator can paste
into `configs/base_config.yaml` as a `static_list` provider.

Usage:

    uv run python scripts/curate_proxies.py > my_proxies.yaml

Or with a specific target + sample cap:

    uv run python scripts/curate_proxies.py \
        --target https://www.glassdoor.com/ \
        --sample-per-source 100 \
        --keep 20 \
        --out configs/curated_proxies.yaml

Rationale for target-driven health-check: proxies that pass a generic
httpbin probe often still fail against Cloudflare-hardened sites. Probing
the actual target (or a near-neighbour) tells us "will this proxy work
for what we care about?" — the only measure that matters at run time.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
import yaml

# Community-maintained free lists. All permissively licensed.
DEFAULT_SOURCES: dict[str, str] = {
    "TheSpeedX/http": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "TheSpeedX/socks5": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    "roosterkid/https": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
    "monosans/http": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "monosans/socks5": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    "proxyscrape/http-us": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=US",
}


def fetch_source(name: str, url: str, *, verbose: bool) -> list[str]:
    if verbose:
        print(f"[fetch] {name}", file=sys.stderr)
    try:
        resp = httpx.get(url, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"[!] {name} unreachable: {exc}", file=sys.stderr)
        return []
    scheme = "socks5://" if "socks5" in name else "http://"
    entries = []
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "://" not in line:
            line = scheme + line
        entries.append(line)
    return entries


def probe(proxy: str, target: str, timeout_s: float) -> tuple[bool, float]:
    t0 = time.time()
    try:
        with httpx.Client(
            proxy=proxy, timeout=timeout_s, follow_redirects=False
        ) as client:
            r = client.head(target)
            if r.status_code >= 400:
                r = client.get(target)
            # 2xx or 3xx means the proxy can *reach* the target. Some
            # targets return 302 to a login/challenge — that's still an
            # accessible IP as far as the pool is concerned.
            return r.status_code < 400, time.time() - t0
    except Exception:
        return False, time.time() - t0


def curate(
    sources: dict[str, str],
    target: str,
    sample_per_source: int,
    keep: int,
    timeout_s: float,
    workers: int,
    verbose: bool,
) -> list[tuple[str, str, float]]:
    """Return list of (source, proxy, latency_seconds) sorted by latency."""
    candidates: list[tuple[str, str]] = []
    for name, url in sources.items():
        entries = fetch_source(name, url, verbose=verbose)
        for e in entries[:sample_per_source]:
            candidates.append((name, e))

    if verbose:
        print(
            f"[probe] {len(candidates)} candidates against {target}",
            file=sys.stderr,
        )

    winners: list[tuple[str, str, float]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(probe, proxy, target, timeout_s): (src, proxy)
            for src, proxy in candidates
        }
        for fut in as_completed(futs):
            src, proxy = futs[fut]
            ok, elapsed = fut.result()
            if ok:
                winners.append((src, proxy, elapsed))
                if verbose:
                    print(
                        f"[win] {src:30s} {proxy:35s} {elapsed:.2f}s",
                        file=sys.stderr,
                    )

    winners.sort(key=lambda x: x[2])
    return winners[:keep]


def emit_yaml(winners: list[tuple[str, str, float]], target: str) -> str:
    if not winners:
        return "# no working proxies found\n"
    lines = [
        f"# Curated by scripts/curate_proxies.py against {target}",
        f"# {len(winners)} proxies sorted by latency (fastest first)",
        "# Paste into configs/base_config.yaml under:",
        "#   proxies:",
        "#     providers:",
        "#       - type: static_list",
        "#         entries:",
        "#           <these lines>",
        "",
    ]
    for src, proxy, elapsed in winners:
        lines.append(f'  - "{proxy}"  # {src}, {elapsed:.2f}s')
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--target",
        default="https://www.indeed.com/",
        help="URL to health-check against (default: Indeed homepage)",
    )
    p.add_argument("--sample-per-source", type=int, default=50)
    p.add_argument("--keep", type=int, default=20)
    p.add_argument("--timeout", type=float, default=6.0)
    p.add_argument("--workers", type=int, default=40)
    p.add_argument("--out", type=Path, default=None, help="Write to file (default: stdout)")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    winners = curate(
        DEFAULT_SOURCES,
        target=args.target,
        sample_per_source=args.sample_per_source,
        keep=args.keep,
        timeout_s=args.timeout,
        workers=args.workers,
        verbose=not args.quiet,
    )

    output = emit_yaml(winners, args.target)
    if args.out:
        args.out.write_text(output, encoding="utf-8")
        print(
            f"wrote {len(winners)} entries to {args.out}", file=sys.stderr
        )
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    main()
