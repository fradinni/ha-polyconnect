#!/usr/bin/env python3
"""Open the Polyconnect app in a visible Chromium window.

By default, opens the heat pump view for interactive browsing.
With --capture-ids, discovers and saves installation/heat pump IDs.

Usage:
    python3 open-app.py                  # just open the app
    python3 open-app.py --capture-ids    # discover and save device IDs
"""
from __future__ import annotations

import argparse
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
HEAT_PUMP_ID = "64140b25194618718c5083bd"

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


def status(label: str, state: str, value: str | None = None):
    """Print a status line with icon."""
    icons = {
        "wait": f"{C.YELLOW}\u25cc{C.RESET}",
        "ok": f"{C.GREEN}\u25cf{C.RESET}",
        "err": f"{C.RED}\u2717{C.RESET}",
        "info": f"{C.CYAN}\u2139{C.RESET}",
    }
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


# ── Browser setup ─────────────────────────────────────────────────────────────

def launch_browser(pw, *, headless: bool = False):
    """Launch Chromium with the required context (headers, cookies, UA)."""
    browser = pw.chromium.launch(
        headless=headless,
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
    return browser, page


def load_app(page, token: str) -> None:
    """Navigate to the app and wait for Blazor to initialize."""
    page.goto(f"{BASE}/from-native/{token}", wait_until="domcontentloaded", timeout=30_000)

    try:
        page.wait_for_function(
            "() => typeof Blazor !== 'undefined' && Blazor._internal",
            timeout=20_000,
        )
    except Exception:
        status("Blazor", "err", "runtime not detected")
        raise SystemExit(1)

    try:
        page.wait_for_selector(
            ".application-commons-mobile-display-mode, .co-gauge-container, "
            ".installation-overview, .heat-pump-view-mode-container",
            timeout=15_000,
        )
    except Exception:
        pass
    time.sleep(1)

    body = page.evaluate("() => document.body.innerText.substring(0, 300)")
    if "403" in body or "must be connected" in body.lower():
        status("Auth", "err", "token expired \u2014 run get-jwt.py to refresh")
        raise SystemExit(1)


def get_current_url(page) -> str:
    """Get current URL via JS (catches Blazor pushState changes)."""
    try:
        return page.evaluate("() => window.location.href")
    except Exception:
        return ""


# ── Mode: default (open app) ─────────────────────────────────────────────────

def run_open(token: str) -> None:
    """Open the heat pump view in a visible browser window."""
    print(f"\n{C.CYAN}{C.BOLD}  Polyconnect{C.RESET}")
    print(f"{C.CYAN}  {'─' * 40}{C.RESET}\n")

    status("Token", "ok", f"{len(token)} chars")
    status("Browser", "info", "launching...")

    pw = sync_playwright().start()
    browser, page = launch_browser(pw)

    status("App", "info", "loading...")
    load_app(page, token)
    status("App", "ok", "loaded")

    # Navigate to heat pump view
    status("Navigation", "info", "heat pump view...")
    page.evaluate(
        f"Blazor._internal.navigationManager.navigateTo("
        f"'{BASE}/heat-pump-view/{HEAT_PUMP_ID}', "
        f"{{forceLoad: false, replaceHistoryEntry: false, historyEntryState: null}})"
    )
    try:
        page.wait_for_selector(
            ".co-gauge-container, .heat-pump-view-mode-container, "
            ".order-and-value-item, .heat-pump-mode",
            timeout=12_000,
        )
    except Exception:
        pass
    time.sleep(2)

    status("Ready", "ok", "heat pump view displayed")
    print(f"\n  {C.DIM}Press Ctrl+C or close the browser to exit.{C.RESET}\n")

    try:
        while browser.is_connected():
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n  {C.DIM}Shutting down...{C.RESET}")
    finally:
        browser.close()
        pw.stop()


# ── Mode: --capture-ids ───────────────────────────────────────────────────────

def run_capture_ids(token: str, *, headless: bool = True) -> None:
    """Discover installation and heat pump IDs from the live app."""
    print(f"\n{C.CYAN}{C.BOLD}  Polyconnect \u2014 ID Discovery{C.RESET}")
    print(f"{C.CYAN}  {'─' * 40}{C.RESET}")
    print(f"{C.DIM}  Opens the app and captures device IDs automatically{C.RESET}\n")

    status("Token", "ok", f"{len(token)} chars")

    capture = IDCapture()

    mode_label = "headless" if headless else "visible"
    status("Browser", "info", f"launching Chromium ({mode_label})...")
    pw = sync_playwright().start()
    browser, page = launch_browser(pw, headless=headless)

    status("App", "info", "loading with token...")
    load_app(page, token)
    status("App", "ok", "loaded successfully")

    # Installation ID from initial URL
    capture.check_url(get_current_url(page))

    # Click device card to navigate to heat-pump-view
    if not capture.ids["heat_pump_id"]:
        status("Navigation", "info", "clicking heat pump card...")
        try:
            page.click(".device-summary-item.mobile-clickable", force=True, timeout=8_000)
        except Exception:
            pass
        for _ in range(30):
            time.sleep(0.5)
            capture.check_url(get_current_url(page))
            if capture.ids["heat_pump_id"]:
                break

    # ── Results ───────────────────────────────────────────────────────────────
    print(f"\n{C.CYAN}  {'─' * 40}{C.RESET}")
    print(f"  {C.BOLD}Captured IDs{C.RESET}")
    print(f"{C.CYAN}  {'─' * 40}{C.RESET}")
    capture.print_status()

    if capture.all_captured:
        capture.save()
        print(f"  {C.GREEN}{C.BOLD}All IDs captured successfully!{C.RESET}")
        print(f"  {C.DIM}Saved to: {IDS_FILE}{C.RESET}\n")
        browser.close()
        pw.stop()
        return

    # Not all captured yet — keep browser open and monitor
    missing = [capture.labels[k] for k, v in capture.ids.items() if v is None]
    print(f"  {C.YELLOW}Missing: {', '.join(missing)}{C.RESET}")
    print(f"  {C.DIM}Navigate in the browser to discover remaining IDs.{C.RESET}")
    print(f"  {C.DIM}The script monitors URL changes automatically.{C.RESET}")
    print(f"\n  {C.DIM}Press Ctrl+C to close.{C.RESET}\n")

    try:
        prev_count = capture.captured_count
        while browser.is_connected():
            time.sleep(1)
            capture.check_url(get_current_url(page))
            if capture.captured_count > prev_count:
                prev_count = capture.captured_count
                for key, val in capture.ids.items():
                    if val is not None:
                        status(capture.labels[key], "ok", val)
                if capture.all_captured:
                    capture.save()
                    print(f"\n  {C.GREEN}{C.BOLD}All IDs captured!{C.RESET} Saved to {IDS_FILE}\n")
                    break
    except KeyboardInterrupt:
        print(f"\n  {C.DIM}Shutting down...{C.RESET}")
    finally:
        if capture.captured_count > 0:
            capture.save()
        browser.close()
        pw.stop()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(
        description="Open the Polyconnect app in a visible Chromium window.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""\
Examples:
  python3 open-app.py                       Open the heat pump view
  python3 open-app.py --capture-ids         Discover IDs (headless)
  python3 open-app.py --capture-ids --show  Discover IDs (visible browser)

Output:
  IDs are saved to: {IDS_FILE}
""",
    )
    parser.add_argument(
        "--capture-ids",
        action="store_true",
        help="discover installation/heat pump IDs and save to captured_ids.json",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="show the browser window (--capture-ids runs headless by default)",
    )
    args = parser.parse_args()

    token = load_token()

    if args.capture_ids:
        run_capture_ids(token, headless=not args.show)
    else:
        run_open(token)


if __name__ == "__main__":
    main()
