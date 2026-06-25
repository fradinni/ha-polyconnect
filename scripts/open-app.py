#!/usr/bin/env python3
"""Open the Polyconnect app in a visible Chromium window and capture device IDs.

Discovers installation ID and heat pump ID by observing the Blazor app's
internal navigation, then displays them in a friendly terminal UI.

Usage:
    pip install playwright
    python3 -m playwright install chromium
    python3 open-app.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# ── Config ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
TOKEN_FILE = SCRIPT_DIR / "captured_token.txt"
IDS_FILE = SCRIPT_DIR / "captured_ids.json"

BASE = "https://polytropic.user-app.pool.mytech-connect.io"
CF_HEADERS = {
    "CF-Access-Client-Id": "zLT6DV",
    "CF-Access-Client-Secret": "NEEJ9S",
}
AFFINITY_COOKIE = {
    "name": "affinity",
    "value": "382f2696aa3d7505ba3d20a0b6b549f9|dc028cea65244b463811c834d3033c89",
    "domain": "polytropic.user-app.pool.mytech-connect.io",
    "path": "/",
    "sameSite": "Lax",
}
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)

# URL route patterns for ID extraction
ROUTE_PATTERNS = {
    "installation_id": re.compile(r"/installation-overview/([0-9a-f]{24})"),
    "heat_pump_id": re.compile(r"/heat-pump-view/([0-9a-f]{24})"),
}


# ── Terminal UI ───────────────────────────────────────────────────────────────

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


def banner():
    print(f"""
{C.CYAN}{C.BOLD}  Polyconnect — ID Discovery Tool{C.RESET}
{C.CYAN}  {'─' * 40}{C.RESET}
{C.DIM}  Opens the app in a browser and captures device IDs{C.RESET}
""")


def status(label: str, state: str, value: str | None = None):
    """Print a status line with icon."""
    icons = {"wait": f"{C.YELLOW}◌{C.RESET}", "ok": f"{C.GREEN}●{C.RESET}", "err": f"{C.RED}✗{C.RESET}", "info": f"{C.CYAN}ℹ{C.RESET}"}
    icon = icons.get(state, " ")
    if value:
        print(f"  {icon}  {label:<22} {C.BOLD}{value}{C.RESET}")
    else:
        print(f"  {icon}  {label}")


# ── Token loader ──────────────────────────────────────────────────────────────

def load_token() -> str:
    if not TOKEN_FILE.exists():
        status("Token file", "err", "not found")
        print(f"\n  {C.DIM}Run get-jwt.py first to capture a session token.{C.RESET}")
        print(f"  {C.DIM}Path: {TOKEN_FILE}{C.RESET}\n")
        sys.exit(1)
    token = "\n".join(
        line for line in TOKEN_FILE.read_text().splitlines() if not line.startswith("#")
    ).strip()
    if not token:
        status("Token file", "err", "empty")
        sys.exit(1)
    return token


# ── ID capture logic ──────────────────────────────────────────────────────────

class IDCapture:
    """Tracks discovered IDs from URL navigation."""

    def __init__(self):
        self.ids: dict[str, str | None] = {
            "installation_id": None,
            "heat_pump_id": None,
        }
        self.labels = {
            "installation_id": "Installation ID",
            "heat_pump_id": "Heat Pump ID",
        }

    def check_url(self, url: str) -> bool:
        """Extract IDs from a URL. Returns True if a new ID was found."""
        found_new = False
        for key, pattern in ROUTE_PATTERNS.items():
            if self.ids[key] is None:
                m = pattern.search(url)
                if m:
                    self.ids[key] = m.group(1)
                    found_new = True
        return found_new

    @property
    def all_captured(self) -> bool:
        return all(v is not None for v in self.ids.values())

    @property
    def captured_count(self) -> int:
        return sum(1 for v in self.ids.values() if v is not None)

    def print_status(self):
        """Print current capture status."""
        print()
        for key, value in self.ids.items():
            label = self.labels[key]
            if value:
                status(label, "ok", value)
            else:
                status(label, "wait", f"{C.DIM}waiting...{C.RESET}")
        print()

    def save(self):
        """Save captured IDs to JSON file."""
        data = {k: v for k, v in self.ids.items() if v is not None}
        IDS_FILE.write_text(json.dumps(data, indent=2) + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)
    banner()

    token = load_token()
    status("Token", "ok", f"{len(token)} chars")

    capture = IDCapture()

    # ── Launch browser ────────────────────────────────────────────────────────
    status("Browser", "info", "launching Chromium...")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=False,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    ctx = browser.new_context(
        extra_http_headers=CF_HEADERS,
        user_agent=USER_AGENT,
        viewport={"width": 390, "height": 844},
        locale="fr-FR",
    )
    ctx.add_cookies([AFFINITY_COOKIE])
    page = ctx.new_page()

    # Blazor SPA uses pushState — page.url may lag behind window.location.href

    # ── Load app ──────────────────────────────────────────────────────────────
    status("App", "info", "loading with token...")
    page.goto(f"{BASE}/from-native/{token}", wait_until="domcontentloaded", timeout=30_000)

    # Wait for Blazor runtime
    try:
        page.wait_for_function(
            "() => typeof Blazor !== 'undefined' && Blazor._internal",
            timeout=20_000,
        )
    except Exception:
        status("Blazor", "err", "runtime not detected")
        browser.close()
        pw.stop()
        sys.exit(1)

    # Wait for initial render
    try:
        page.wait_for_selector(
            ".application-commons-mobile-display-mode, .co-gauge-container, "
            ".installation-overview, .heat-pump-view-mode-container",
            timeout=15_000,
        )
    except Exception:
        pass
    time.sleep(1)

    # Check for auth failure
    body = page.evaluate("() => document.body.innerText.substring(0, 300)")
    if "403" in body or "must be connected" in body.lower():
        status("Auth", "err", "token expired — run get-jwt.py to refresh")
        browser.close()
        pw.stop()
        sys.exit(1)

    status("App", "ok", "loaded successfully")

    def get_current_url() -> str:
        """Get current URL via JS (catches Blazor pushState changes faster)."""
        try:
            return page.evaluate("() => window.location.href")
        except Exception:
            return ""

    # Check URL for installation ID (app auto-navigates to /installation-overview/{id})
    capture.check_url(get_current_url())

    # ── Navigate to heat pump view (click the device card) ────────────────────
    if not capture.ids["heat_pump_id"]:
        status("Navigation", "info", "clicking heat pump card...")
        try:
            page.click(".device-summary-item.mobile-clickable", force=True, timeout=8_000)
        except Exception:
            pass
        # Blazor SPA navigation can take several seconds in non-headless mode
        for _ in range(30):
            time.sleep(0.5)
            capture.check_url(get_current_url())
            if capture.ids["heat_pump_id"]:
                break

    # ── Display results ───────────────────────────────────────────────────────
    print(f"\n{C.CYAN}  {'─' * 40}{C.RESET}")
    print(f"  {C.BOLD}Captured IDs{C.RESET}")
    print(f"{C.CYAN}  {'─' * 40}{C.RESET}")
    capture.print_status()

    if capture.all_captured:
        capture.save()
        print(f"  {C.GREEN}{C.BOLD}All IDs captured successfully!{C.RESET}")
        print(f"  {C.DIM}Saved to: {IDS_FILE}{C.RESET}")
    else:
        missing = [capture.labels[k] for k, v in capture.ids.items() if v is None]
        print(f"  {C.YELLOW}Missing: {', '.join(missing)}{C.RESET}")
        print(f"  {C.DIM}Navigate in the browser to discover remaining IDs.{C.RESET}")
        print(f"  {C.DIM}The script monitors URL changes automatically.{C.RESET}")

    print(f"\n{C.CYAN}  {'─' * 40}{C.RESET}")
    print(f"  {C.DIM}Browser is open — navigate freely to explore.{C.RESET}")
    print(f"  {C.DIM}Press Ctrl+C to close.{C.RESET}")
    print()

    # ── Keep running and watch for new IDs ────────────────────────────────────
    try:
        prev_count = capture.captured_count
        while browser.is_connected():
            time.sleep(1)
            capture.check_url(get_current_url())
            if capture.captured_count > prev_count:
                prev_count = capture.captured_count
                for key, val in capture.ids.items():
                    if val is not None:
                        status(capture.labels[key], "ok", val)
                if capture.all_captured:
                    capture.save()
                    print(f"\n  {C.GREEN}{C.BOLD}All IDs captured!{C.RESET} Saved to {IDS_FILE}")
    except KeyboardInterrupt:
        print(f"\n  {C.DIM}Shutting down...{C.RESET}")
    finally:
        # Save whatever we have
        if capture.captured_count > 0:
            capture.save()
        browser.close()
        pw.stop()

    # Final summary
    print(f"\n{C.CYAN}  {'─' * 40}{C.RESET}")
    print(f"  {C.BOLD}Final Results{C.RESET}")
    print(f"{C.CYAN}  {'─' * 40}{C.RESET}")
    capture.print_status()
    if IDS_FILE.exists():
        print(f"  {C.DIM}File: {IDS_FILE}{C.RESET}\n")


if __name__ == "__main__":
    main()
