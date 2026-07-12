#!/usr/bin/env python3
"""Run one magicapply apply with a wall-clock timeout (DoD harness)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("label")
    p.add_argument("job_id")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--retry", action="store_true")
    args = p.parse_args()
    cmd = [
        sys.executable,
        "-m",
        "magicapply.cli.main",
        "apply",
        args.job_id,
        "--root",
        "configs",
        "--no-submit",
    ]
    if args.retry:
        cmd.append("--retry")
    env = os.environ.copy()
    env.setdefault("MAGICAPPLY_INDEED_ACK", "1")
    env.setdefault("MAGICAPPLY_LINKEDIN_ACK", "1")
    print(f"=== {args.label} ===", flush=True)
    print(" ".join(cmd), flush=True)
    try:
        proc = subprocess.run(
            cmd,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            timeout=args.timeout,
            capture_output=False,
        )
        print(f"exit: {proc.returncode}", flush=True)
        return proc.returncode
    except subprocess.TimeoutExpired:
        print(f"TIMEOUT after {args.timeout}s", flush=True)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
