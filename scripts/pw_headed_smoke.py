#!/usr/bin/env python3
"""Headed Chromium smoke on DISPLAY=:1 (guest Xvfb). Not a unit test."""
from __future__ import annotations

import os
import subprocess
import time

os.environ.setdefault("DISPLAY", ":1")

from playwright.sync_api import sync_playwright


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-gpu",
                "--use-gl=swiftshader",
                "--enable-unsafe-swiftshader",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--ozone-platform=x11",
                "--window-size=1280,800",
                "--window-position=40,40",
            ],
        )
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.goto(
            "https://www.linkedin.com/login",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        print("OK title=", page.title())
        print("OK url=", page.url)
        path = "/tmp/linkedin_vnc.png"
        page.screenshot(path=path)
        print("OK screenshot_bytes=", os.path.getsize(path))
        tree = subprocess.getoutput(
            "DISPLAY=:1 xwininfo -root -tree 2>/dev/null | grep -iE 'chrome|LinkedIn' | head -20"
        )
        print(tree)
        hold = int(os.environ.get("SMOKE_HOLD_S", "60"))
        for i in range(max(1, hold // 10)):
            time.sleep(10)
            print(f"hold {(i + 1) * 10}s")
        browser.close()
        print("done")


if __name__ == "__main__":
    main()
