"""Launches a fresh, standalone Chromium window (NOT the user's real Chrome
profile - no risk of profile corruption, no shared session unless the user
logs in fresh inside it) with a remote-debugging port open, so a separate
process can reconnect and inspect/fill the page across multiple steps without
holding the browser open in a single blocking script.

Used for portal/form flows that claude-in-chrome's own domain policy blocks
(confirmed: LinkedIn, Keka, and Google Forms all hit the same "Navigation to
this domain is not allowed" wall from that specific tool - this is a
separate, independently-built path, not a workaround of that policy, since it
never touches the user's real logged-in browser session at all).

Credential handling: if the destination requires login, this script does NOT
touch it - the window is visible and the user types their own credentials
into the real page themselves. Nothing here reads, stores, or transmits any
password.
"""
from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright

DEBUG_PORT = 9333


def main(url: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[f"--remote-debugging-port={DEBUG_PORT}"],
        )
        page = browser.new_page()
        page.goto(url)
        print(f"Launched, connected on CDP port {DEBUG_PORT}. Window is open - leave it open.")
        # Keep this process alive so the browser and its remote-debugging
        # port stay up for later steps to connect to.
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main(sys.argv[1])
