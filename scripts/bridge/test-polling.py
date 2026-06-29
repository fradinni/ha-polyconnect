#!/usr/bin/env python3
"""Local polling test — visible Chromium, 60s status scrapes.

Replicates the HA integration + bridge polling cycle:
  1. Launch Chromium (visible) and load the Polyconnect Blazor app
  2. Navigate to the heat pump view
  3. Every N seconds, scrape the DOM for status data
  4. Print results with timestamps and timing

Usage:
    python3 test-polling.py                  # visible browser, 60s
    python3 test-polling.py --interval 30    # 30s polling
    python3 test-polling.py --once           # single poll
    python3 test-polling.py --headless       # no visible window
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

CAPTURE_DIR = Path(__file__).resolve().parent.parent / "capture"
TOKEN_FILE = CAPTURE_DIR / "captured_token.txt"
IDS_FILE = CAPTURE_DIR / "captured_ids.json"

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

STATUS_JS = """
() => {
    const parseNum = (txt) => {
        if (!txt) return null;
        const n = parseFloat(txt.replace(',', '.').replace(/[^0-9.-]/g, ''));
        return isNaN(n) ? null : n;
    };
    const textOf = (el) => el ? el.textContent.trim() : null;

    let waterTemp = null;
    for (const el of document.querySelectorAll(
        '.order-and-value-value-number, [class*="value-number"], ' +
        '[class*="water-temp"], [class*="waterTemp"], ' +
        '.heat-pump-view-temperature-value, .device-summary-temperature'
    )) {
        if (el.offsetParent === null) continue;
        const txt = el.textContent.trim();
        if (txt === '-' || txt === '') continue;
        const v = parseFloat(txt.replace(',', '.').replace(/[^0-9.-]/g, ''));
        if (!isNaN(v) && v > 0 && v < 50) { waterTemp = v; break; }
    }

    let setpointTemp = null;
    for (const el of document.querySelectorAll(
        '.order-and-value-order-number, [class*="setpoint"], .round-slider-value'
    )) {
        const v = parseNum(textOf(el));
        if (v !== null && v >= 8 && v <= 32) { setpointTemp = v; break; }
    }

    let outsideTemp = null;
    const weather = document.querySelector('.topbar-weather');
    if (weather) {
        const v = parseNum(weather.textContent);
        if (v !== null && v > -30 && v < 60) outsideTemp = v;
    }
    if (outsideTemp === null) {
        for (const row of document.querySelectorAll('.order-and-value-item, [class*="data-item"]')) {
            const txt = row.textContent.toLowerCase();
            if (txt.includes('extérieure') || txt.includes('outside') || txt.includes('ambiant')) {
                const v = parseNum(row.textContent);
                if (v !== null && v > -30 && v < 60) { outsideTemp = v; break; }
            }
        }
    }

    const MAIN_MODES = ['Chauffage', 'Froid', 'Automatique'];
    let operatingMode = null;
    for (const el of document.querySelectorAll('.state-button-value, [class*="mode-label"]')) {
        const txt = el.textContent.trim();
        if (MAIN_MODES.includes(txt)) { operatingMode = txt; break; }
    }
    if (!operatingMode) {
        for (const el of document.querySelectorAll(
            '.heat-pump-view-mode-items [class*="selected"] button, ' +
            '.heat-pump-view-mode-items button[class*="active"]'
        )) {
            const txt = el.textContent.trim();
            if (MAIN_MODES.includes(txt)) { operatingMode = txt; break; }
        }
    }
    if (!operatingMode) {
        const iconMap = {
            'heat-pump-mode-heating':'Chauffage',
            'heat-pump-mode-cooling':'Froid',
            'heat-pump-mode-auto':'Automatique'
        };
        for (const [cls, name] of Object.entries(iconMap)) {
            const el = document.querySelector('.' + cls);
            if (el && el.offsetParent !== null && !el.classList.contains('istd-co-hidden')) {
                operatingMode = name; break;
            }
        }
    }
    if (!operatingMode) {
        const body = document.body.innerText;
        for (const m of MAIN_MODES) { if (body.includes(m)) { operatingMode = m; break; } }
    }

    const REG_MODES = ['Eco', 'Smart', 'Boost'];
    let regulationMode = null;
    for (const el of document.querySelectorAll('.state-button-value, [class*="power-mode"]')) {
        const txt = el.textContent.trim();
        if (REG_MODES.includes(txt)) { regulationMode = txt; break; }
    }
    if (!regulationMode) {
        const iconMap = {
            'heat-pump-mode-power-smart':'Smart',
            'heat-pump-mode-power-eco':'Eco',
            'heat-pump-mode-power-boost':'Boost'
        };
        for (const [cls, name] of Object.entries(iconMap)) {
            const el = document.querySelector('.' + cls);
            if (el && el.offsetParent !== null && !el.classList.contains('istd-co-hidden')) {
                regulationMode = name; break;
            }
        }
    }
    if (!regulationMode) {
        const body = document.body.innerText;
        for (const m of REG_MODES) { if (body.includes(m)) { regulationMode = m; break; } }
    }

    let heatPumpActive = null;
    const btn = document.querySelector('.heat-pump-on-off button, .co-on-off-button, [class*="on-off"] button');
    if (btn) {
        const pressed = btn.getAttribute('aria-pressed');
        if (pressed !== null) heatPumpActive = pressed === 'true';
        else heatPumpActive = btn.classList.contains('istd-sty-active') || btn.classList.contains('active');
    }
    if (heatPumpActive === null) {
        const body = document.body.innerText;
        if (/\\bON\\b/.test(body)) heatPumpActive = true;
        else if (/\\bOFF\\b/.test(body)) heatPumpActive = false;
    }

    let compressorRunning = false;
    const compEl = document.querySelector('[class*="compressor"], [class*="compresseur"]');
    if (compEl) compressorRunning = compEl.classList.contains('running') || compEl.classList.contains('active');
    if (!compressorRunning) {
        const body = document.body.innerText.toLowerCase();
        if (body.includes('compresseur') && body.includes('en marche')) compressorRunning = true;
    }

    let filtrationRunning = false;
    const filtSelectors = [
        '[class*="filtration"] button', '[class*="filtration"][class*="toggle"]',
        '[class*="filtration"][class*="btn"]', '[class*="pompe-filtration"]', '[class*="pump-status"]',
    ];
    for (const sel of filtSelectors) {
        const el = document.querySelector(sel);
        if (el) {
            const p = el.getAttribute('aria-pressed');
            if (p !== null) { filtrationRunning = p === 'true'; break; }
            if (el.classList.contains('istd-sty-active') || el.classList.contains('active') ||
                el.classList.contains('on') || el.classList.contains('running')) {
                filtrationRunning = true; break;
            }
        }
    }
    if (!filtrationRunning) {
        const body = document.body.innerText.toLowerCase();
        if (body.includes('filtration') && (body.includes(' on') || body.includes('démarr')))
            filtrationRunning = true;
    }

    let alarmActive = false, alarmMessage = null;
    const banner = document.querySelector('.heat-pump-view-error-clickable');
    if (banner && banner.offsetParent !== null) {
        const txt = banner.innerText.trim();
        if (txt) { alarmActive = true; alarmMessage = txt; }
    }
    if (!alarmActive) {
        for (const el of document.querySelectorAll('[class*="alarm"], [class*="error-msg"]')) {
            if (el.offsetParent === null) continue;
            const txt = el.innerText.trim();
            if (txt && !el.classList.contains('device-summary-data-blocked-message')) {
                alarmActive = true; alarmMessage = txt; break;
            }
        }
    }

    return {
        waterTemperature:    waterTemp,
        outsideTemperature:  outsideTemp,
        setpointTemperature: setpointTemp,
        operatingMode:       operatingMode,
        regulationMode:      regulationMode,
        heatPumpActive:      heatPumpActive,
        compressorRunning:   compressorRunning,
        filtrationRunning:   filtrationRunning,
        alarmActive:         alarmActive,
        alarmMessage:        alarmMessage,
        errorCode:           alarmActive ? 1 : 0,
    };
}
"""


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"


def status(label: str, state: str, value: str | None = None):
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


def load_credentials() -> tuple[str, str]:
    if not TOKEN_FILE.exists():
        status("Token file", "err", f"not found ({TOKEN_FILE})")
        print(f"\n  {C.DIM}Run get-jwt.py first to capture a session token.{C.RESET}")
        sys.exit(1)
    token = "\n".join(
        line for line in TOKEN_FILE.read_text().splitlines() if not line.startswith("#")
    ).strip()
    if not token:
        status("Token file", "err", "empty")
        sys.exit(1)

    if not IDS_FILE.exists():
        status("IDs file", "err", f"not found ({IDS_FILE})")
        sys.exit(1)
    ids = json.loads(IDS_FILE.read_text())
    heat_pump_id = ids.get("heat_pump_id", "")
    installation_id = ids.get("installation_id", "")
    if not heat_pump_id:
        status("Heat pump ID", "err", "missing from ids.json")
        sys.exit(1)
    if not installation_id:
        status("Installation ID", "err", "missing from ids.json")
        sys.exit(1)

    return token, heat_pump_id, installation_id


def launch_browser(pw, *, headless: bool = False):
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
        status("Auth", "err", "token expired — run get-jwt.py to refresh")
        raise SystemExit(1)


def print_poll(data: dict, elapsed_ms: float, poll_num: int) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{C.CYAN}{'=' * 66}{C.RESET}")
    print(f"  {C.BOLD}Poll #{poll_num}{C.RESET} @ {ts}  ({elapsed_ms:.0f}ms)")
    print(f"{C.CYAN}{'=' * 66}{C.RESET}")

    rows = [
        ("Water temp",      "waterTemperature",    "°C"),
        ("Outside temp",    "outsideTemperature",   "°C"),
        ("Setpoint",        "setpointTemperature",  "°C"),
        ("Operating mode",  "operatingMode",        ""),
        ("Regulation mode", "regulationMode",       ""),
        ("Heat pump ON",    "heatPumpActive",       ""),
        ("Compressor",      "compressorRunning",    ""),
        ("Filtration",      "filtrationRunning",    ""),
        ("Alarm",           "alarmActive",          ""),
    ]
    for label, key, unit in rows:
        val = data.get(key)
        if val is None:
            status(label, "wait", f"{C.DIM}null{C.RESET}")
        elif key == "alarmActive" and val:
            status(label, "err", f"{val}  {data.get('alarmMessage') or ''}")
        elif key == "alarmActive":
            status(label, "ok", "none")
        else:
            status(label, "ok", f"{val}{unit}")

    nulls = [k for k, v in data.items() if v is None]
    if nulls:
        print(f"\n  {C.YELLOW}[WARN] Null fields: {', '.join(nulls)}{C.RESET}")


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description="Polyconnect polling test (visible browser)")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds (default: 60)")
    parser.add_argument("--headless", action="store_true", help="Run headless (no visible window)")
    parser.add_argument("--once", action="store_true", help="Poll once and exit")
    args = parser.parse_args()

    token, heat_pump_id, installation_id = load_credentials()

    print(f"\n{C.CYAN}{C.BOLD}  Polyconnect — Polling Test{C.RESET}")
    print(f"{C.CYAN}  {'─' * 40}{C.RESET}\n")

    status("Token", "ok", f"{len(token)} chars")
    status("Heat pump ID", "ok", heat_pump_id)
    status("Installation ID", "ok", installation_id)
    status("Interval", "info", f"{args.interval}s")
    status("Browser", "info", f"{'headless' if args.headless else 'visible'} — launching...")

    pw = sync_playwright().start()
    browser, page = launch_browser(pw, headless=args.headless)

    status("App", "info", "loading...")
    load_app(page, token)
    status("App", "ok", "loaded")

    status("Navigation", "info", "heat pump view...")
    page.evaluate(
        f"Blazor._internal.navigationManager.navigateTo("
        f"'{BASE}/heat-pump-view/{heat_pump_id}', "
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
    time.sleep(3)

    body = page.evaluate("() => document.body.innerText.substring(0, 200)")
    if "403" in body or "must be connected" in body.lower():
        status("Auth", "err", "token expired after navigation — run get-jwt.py")
        browser.close()
        pw.stop()
        sys.exit(1)

    status("Ready", "ok", "heat pump view displayed")
    print(f"\n  {C.DIM}Polling every {args.interval}s. Press Ctrl+C to stop.{C.RESET}")

    poll_num = 0
    try:
        while browser.is_connected():
            poll_num += 1
            t_start = time.time()

            try:
                page.evaluate(
                    f"Blazor._internal.navigationManager.navigateTo("
                    f"'{BASE}/installation-overview/{installation_id}', "
                    f"{{forceLoad: false, replaceHistoryEntry: false, historyEntryState: null}})"
                )
                try:
                    page.wait_for_selector(".device-summary-item", timeout=5_000)
                except Exception:
                    time.sleep(0.5)
                page.evaluate(
                    f"Blazor._internal.navigationManager.navigateTo("
                    f"'{BASE}/heat-pump-view/{heat_pump_id}', "
                    f"{{forceLoad: false, replaceHistoryEntry: false, historyEntryState: null}})"
                )
                try:
                    page.wait_for_selector(
                        ".co-gauge-container, .heat-pump-view-mode-container, "
                        ".order-and-value-item",
                        timeout=8_000,
                    )
                except Exception:
                    pass
                try:
                    page.wait_for_function(
                        "() => {"
                        "  const sp = document.querySelector('.order-and-value-order-number');"
                        "  const w  = document.querySelector('.topbar-weather');"
                        "  const g  = document.querySelector('.co-gauge-container');"
                        "  if (!g) return false;"
                        "  if (sp && sp.textContent.trim()) return true;"
                        "  if (w && w.textContent.trim()) return true;"
                        "  return false;"
                        "}",
                        timeout=6_000,
                    )
                except Exception:
                    pass

                page_text = page.evaluate("() => document.body.innerText.substring(0, 200)")
                if "403" in page_text or "must be connected" in page_text.lower():
                    print(f"\n  {C.RED}[ERROR] Token expired mid-session!{C.RESET}")
                    break

                data = page.evaluate(STATUS_JS)
                elapsed = (time.time() - t_start) * 1000
                print_poll(data, elapsed, poll_num)

                nulls = [k for k, v in data.items() if v is None]
                if len(nulls) >= 5:
                    debug = page.evaluate("() => document.body.innerText.substring(0, 500)")
                    print(f"\n  {C.YELLOW}[DEBUG] Page text:{C.RESET} {debug[:300]!r}")

            except Exception as e:
                elapsed = (time.time() - t_start) * 1000
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"\n  {C.RED}[ERROR] Poll #{poll_num} @ {ts} ({elapsed:.0f}ms): {e}{C.RESET}")
                if "disconnected" in str(e).lower() or "closed" in str(e).lower():
                    break

            if args.once:
                break

            remaining = args.interval - (time.time() - t_start)
            if remaining > 0:
                print(f"\n  {C.DIM}Next poll in {remaining:.0f}s...{C.RESET}")
                time.sleep(remaining)

    except KeyboardInterrupt:
        print(f"\n\n  {C.DIM}Stopped by user{C.RESET}")

    browser.close()
    pw.stop()
    print(f"\n  {C.BOLD}Done.{C.RESET} {poll_num} polls completed.\n")


if __name__ == "__main__":
    main()
