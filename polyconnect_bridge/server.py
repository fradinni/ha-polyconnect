#!/usr/bin/env python3
"""Polyconnect Bridge Server — HA Add-on (v2: native login).

Features:
- Persistent Chromium instance for controlling Polyconnect heat pumps
- Native email/password authentication against auth.pool.mytech-connect.io
  (no more mitmproxy capture — see docs/native-login.md)
- Credentials stored in /data/ (persistent across add-on updates)

Ports:
- 8765: Main API (HA ingress) — bridge REST + auth control + control panel
"""
from __future__ import annotations

import math, os, logging, queue, threading, time, json, sys, re
from flask import Flask, jsonify, request, Response
from pathlib import Path

from auth import AuthManager, DATA_DIR

BRIDGE_VERSION = "2.1.0"

# ── Auth manager (replaces v1 CaptureManager) ─────────────────────────────────
_auth_mgr = AuthManager()


def _get_token() -> str:
    return _auth_mgr.credentials.token or ""


def _get_heat_pump_id() -> str:
    """Back-compat helper: first pump's id (or empty)."""
    return _auth_mgr.credentials.heat_pump_id or ""


def _get_installation_id() -> str:
    return _auth_mgr.credentials.installation_id or ""


def _get_pumps() -> list[dict]:
    """Return the full discovered pump list: [{id, name}, ...]."""
    return list(_auth_mgr.credentials.heat_pumps or [])


def _resolve_pump(pump_id: str | None) -> str | None:
    """Validate a caller-supplied pump_id against the discovered list.
    Returns the canonical id, or None when missing/unknown. Accepts None
    to mean "the default pump" (first in the list)."""
    pumps = _get_pumps()
    if not pumps:
        return None
    if not pump_id:
        return pumps[0]["id"]
    for p in pumps:
        if p["id"] == pump_id:
            return p["id"]
    return None


LOG_LEVEL = os.environ.get("POLYCONNECT_LOG_LEVEL", "info").upper()
PORT = int(os.environ.get("PORT", 8765))

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("polyconnect")

BASE     = "https://polytropic.user-app.pool.mytech-connect.io"
CF       = {"CF-Access-Client-Id": "zLT6DV", "CF-Access-Client-Secret": "NEEJ9S"}
AFFINITY = {"name": "affinity",
            "value": "382f2696aa3d7505ba3d20a0b6b549f9|dc028cea65244b463811c834d3033c89",
            "domain": "polytropic.user-app.pool.mytech-connect.io",
            "path": "/", "sameSite": "Lax"}
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")

MAIN_MODES = {"Chauffage", "Automatique", "Froid"}
REG_MODES  = {"Eco", "Smart", "Boost"}

# 24-char MongoDB ObjectId in Blazor SPA URLs (/installation-overview/<id>, /heat-pump-view/<id>)
_OBJECTID_RE = re.compile(r"/([0-9a-f]{24})(?:/|$|\?)")

# ── Status DOM extraction JS ──────────────────────────────────────────────────
_STATUS_JS = """
() => {
    const parseNum = (txt) => {
        if (!txt) return null;
        const n = parseFloat(txt.replace(',', '.').replace(/[^0-9.-]/g, ''));
        return isNaN(n) ? null : n;
    };
    const textOf = (el) => el ? el.textContent.trim() : null;

    // Water temperature
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

    // Setpoint
    let setpointTemp = null;
    for (const el of document.querySelectorAll(
        '.order-and-value-order-number, [class*="setpoint"], .round-slider-value'
    )) {
        const v = parseNum(textOf(el));
        if (v !== null && v >= 8 && v <= 32) { setpointTemp = v; break; }
    }

    // Outside temperature
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

    // Operating mode
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

    // Regulation mode
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

    // Heat pump on/off
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

    // Fan / pump forcing (Forçage pompe — active when list item does NOT have state-disabled)
    let fanRunning = false;
    const fanItem = document.querySelector('.istd-ct-list-item:has(.device-ico-water-pump)');
    if (fanItem) {
        fanRunning = !fanItem.classList.contains('state-disabled');
    } else {
        const body = document.body.innerText.toLowerCase();
        if (body.includes('compresseur') &&
            (body.includes('en marche') || body.includes('actif') || body.includes(' on')))
            fanRunning = true;
    }

    // Filtration (active when list item does NOT have state-disabled)
    let filtrationRunning = false;
    const filtItem = document.querySelector('.istd-ct-list-item:has(.device-ico-flowing)');
    if (filtItem) {
        filtrationRunning = !filtItem.classList.contains('state-disabled');
    } else {
        const body = document.body.innerText.toLowerCase();
        if ((body.includes('filtration') || body.includes('pompe')) &&
            (body.includes(' on') || body.includes('démarr') || body.includes('en marche') || body.includes('actif')))
            filtrationRunning = true;
    }

    // Defrost / Dégivrage (active when list item does NOT have state-disabled)
    let defrostActive = false;
    const defrostItem = document.querySelector('.istd-ct-list-item:has(.device-ico-defrosting)');
    if (defrostItem) defrostActive = !defrostItem.classList.contains('state-disabled');

    // Alarm
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
        fanRunning:          fanRunning,
        filtrationRunning:   filtrationRunning,
        defrostActive:       defrostActive,
        alarmActive:         alarmActive,
        alarmMessage:        alarmMessage,
        errorCode:           alarmActive ? 1 : 0,
    };
}
"""

# ── Info panel opener JS ──────────────────────────────────────────────────────
# Compressor/filtration status is hidden behind an info icon next to the
# temperature slider — click it so _STATUS_JS can see the panel content.
# ponytail: broad selector list; tighten once actual class names are known.
_OPEN_INFO_PANEL_JS = """() => {
    const icon = document.querySelector('[class*="istd-std-icon-info"]');
    if (icon) {
        const btn = icon.closest('button');
        if (btn && btn.offsetParent !== null) { btn.click(); return 'istd-std-icon-info'; }
    }
    return null;
}"""

# ── Dedicated Playwright thread ────────────────────────────────────────────────

class PlaywrightThread:
    """All Playwright calls must run on the same OS thread that created the
    sync_playwright instance.  This class owns that thread and accepts
    callables via a queue, returning results to the caller."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="playwright")
        self._thread.start()

    def _worker(self) -> None:
        while True:
            fn, args, kwargs, result_box, event = self._queue.get()
            try:
                result_box["value"] = fn(*args, **kwargs)
            except BaseException as exc:
                result_box["error"] = exc
            finally:
                event.set()

    def call(self, fn, *args, **kwargs):
        result_box: dict = {}
        event = threading.Event()
        self._queue.put((fn, args, kwargs, result_box, event))
        event.wait()
        if "error" in result_box:
            raise result_box["error"]
        return result_box.get("value")


# ── Persistent browser controller ─────────────────────────────────────────────

class PolyconnectController:
    """Single persistent Chromium instance, serialised by a threading lock."""

    # Staleness detection — Blazor's SignalR can disconnect silently while the
    # DOM keeps showing the last known values. Force a page reload when the
    # status payload hasn't changed across N consecutive calls OR for T seconds
    # (whichever fires first). Catches the "HA stops updating, no errors,
    # only a bridge restart fixes it" failure mode.
    _STALE_CALL_LIMIT   = 10           # consecutive identical reads
    _STALE_TIME_LIMIT_S = 30 * 60      # absolute wall-clock cap

    def __init__(self):
        self._pw_thread = PlaywrightThread()
        self._lock   = threading.Lock()
        self._pw     = None
        self._browser= None
        self._ctx    = None
        self._page   = None
        # Which pump's /heat-pump-view/<id> the single Chromium page is currently on.
        # Smart-navigation only navigateTo's when the active pump differs.
        self._current_pump_id: str | None = None
        # Staleness detection state, per-pump.
        self._last_data: dict[str, dict] = {}
        self._last_change_ts: dict[str, float] = {}
        self._unchanged_count: dict[str, int] = {}

    def _launch(self):
        from playwright.sync_api import sync_playwright
        token = _get_token()
        if not token:
            raise RuntimeError("No session token — configure POLYCONNECT_EMAIL / POLYCONNECT_PASSWORD")
        # Heat pump ID is allowed to be missing on first boot — _load_app will
        # auto-discover it from the SPA. Same for installation_id.

        if self._pw:
            try: self._pw.stop()
            except: pass
        self._pw      = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
        self._ctx     = self._browser.new_context(
            extra_http_headers=CF, user_agent=UA,
            viewport={"width": 390, "height": 844}, locale="fr-FR")
        self._ctx.add_cookies([AFFINITY])
        self._page    = self._ctx.new_page()
        try:
            self._load_app()
        except Exception:
            # Clean up so _ensure() forces a fresh relaunch next call
            try:
                self._browser.close()
                self._pw.stop()
            except Exception:
                pass
            self._browser = None
            self._pw      = None
            self._page    = None
            raise

    def _load_app(self):
        """Bootstrap the Blazor SPA from /from-native/<token>.
        On first boot (no pumps discovered yet) enumerate all pumps from
        the installation-overview page. Leaves the page on /heat-pump-view/<first_pump>.
        """
        token = _get_token()
        page = self._page
        page.goto(f"{BASE}/from-native/{token}", wait_until="domcontentloaded", timeout=30_000)
        try:
            page.wait_for_function(
                "() => typeof Blazor !== 'undefined' && Blazor._internal",
                timeout=20_000)
        except Exception:
            pass
        try:
            page.wait_for_function(
                "() => document.body.innerText.trim().length > 10",
                timeout=8_000)
        except Exception:
            pass
        body = page.evaluate("() => document.body.innerText.substring(0, 200)")
        if "403" in body or "must be connected" in body.lower():
            # v2: silently re-login. Caller's _ensure() will relaunch with a fresh token.
            log.warning("Session token expired — refreshing …")
            res = _auth_mgr.refresh()
            if not res.get("ok"):
                raise RuntimeError(f"Session token expired and refresh failed: {res.get('error')}")
            raise RuntimeError("Session token expired — refreshed, will retry")

        # ── First-boot ID discovery ───────────────────────────────────────────
        pumps = _get_pumps()
        if not pumps:
            pumps = self._discover_ids()
            if not pumps:
                raise RuntimeError(
                    "Could not auto-discover any heat pumps — "
                    "check that the account has at least one configured pump.")
        elif not _auth_mgr.credentials.installation_name and _auth_mgr.credentials.installation_id:
            # Pumps already known (env-var pin or existing ids.json without a name).
            # Take a detour through /pools-overview to grab the installation name;
            # the subsequent _navigate_to_pump() call takes us back to the pump view.
            name = self._scrape_installation_name(_auth_mgr.credentials.installation_id)
            if name:
                _auth_mgr.set_pumps(
                    installation_id=_auth_mgr.credentials.installation_id,
                    installation_name=name,
                    heat_pumps=pumps)

        # Navigate to the default (first) pump's view.
        default_pump = pumps[0]["id"]
        self._current_pump_id = None  # force navigation
        self._navigate_to_pump(default_pump)
        body = page.evaluate("() => document.body.innerText.substring(0, 200)")
        if "403" in body or "must be connected" in body.lower():
            log.warning("Session token expired post-load — refreshing …")
            res = _auth_mgr.refresh()
            if not res.get("ok"):
                raise RuntimeError(f"Session token expired and refresh failed: {res.get('error')}")
            raise RuntimeError("Session token expired — refreshed, will retry")
        log.info("Browser launched, default pump view loaded (%d pump(s) total)", len(pumps))

    def _navigate_to_pump(self, pump_id: str) -> None:
        """Smart-navigate the SPA to /heat-pump-view/<pump_id>. No-op if already there."""
        if self._current_pump_id == pump_id and "heat-pump-view" in (self._page.url or ""):
            return
        page = self._page
        log.debug("Navigating SPA to heat-pump-view/%s (from %s)", pump_id, self._current_pump_id)
        page.evaluate(
            f"Blazor._internal.navigationManager.navigateTo("
            f"'{BASE}/heat-pump-view/{pump_id}', false)")
        try:
            page.wait_for_function(
                f"() => /\\/heat-pump-view\\/{pump_id}/.test(window.location.pathname)",
                timeout=8_000)
        except Exception:
            pass
        try:
            page.wait_for_selector(
                ".co-gauge-container, .heat-pump-view-mode-container, "
                ".order-and-value-item, .heat-pump-mode",
                timeout=12_000)
        except Exception:
            pass
        self._wait_for_data(timeout_ms=10_000)
        self._current_pump_id = pump_id

    def _scrape_installation_name(self, installation_id: str | None = None) -> str | None:
        """Navigate to /pools-overview and read `.pool-component-installation-name`
        cards to resolve the installation name.

        - Single-pool account → returns the only name.
        - Multi-pool + installation_id given → clicks each card, matches the one
          whose click lands on /installation-overview/<installation_id>.
        - Multi-pool + no installation_id → returns the first card's name.

        Leaves the browser on /pools-overview (or wherever the last click sent
        it). Callers must re-navigate. Never throws — returns None on failure."""
        page = self._page
        try:
            page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{BASE}/pools-overview', false)")
            try:
                page.wait_for_selector(".pool-component-installation-name", timeout=10_000)
            except Exception:
                log.warning("/pools-overview did not render pool cards (url=%s)", page.url)
                return None
            names = page.evaluate("""() => Array.from(
                document.querySelectorAll('.pool-component-installation-name')
            ).map(el => (el.textContent || '').trim()).filter(Boolean)""")
            if not names:
                log.warning("/pools-overview rendered but no installation names found")
                return None
            log.info("Found %d installation(s) on /pools-overview: %s", len(names), names)

            if len(names) == 1 or not installation_id:
                return names[0]

            # Multi-pool account: click each card, capture the landing URL,
            # return the one matching installation_id.
            for i, candidate in enumerate(names):
                try:
                    if i > 0:
                        page.evaluate(
                            f"Blazor._internal.navigationManager.navigateTo("
                            f"'{BASE}/pools-overview', false)")
                        page.wait_for_selector(
                            ".pool-component-installation-name", timeout=8_000)
                    page.eval_on_selector_all(
                        ".pool-component-installation-name",
                        f"(els) => els[{i}] && els[{i}].click()")
                    page.wait_for_function(
                        "() => /\\/installation-overview\\/[0-9a-f]{24}/"
                        ".test(window.location.pathname)",
                        timeout=6_000)
                    m = _OBJECTID_RE.search(page.url or "")
                    if m and m.group(1) == installation_id:
                        log.info("Matched installation_id=%s -> %r",
                                 installation_id, candidate)
                        return candidate
                except Exception as e:
                    log.debug("Card %d (%r) click failed: %s", i, candidate, e)
            log.warning("Could not match installation_id=%s in %d pool(s); "
                        "returning first name", installation_id, len(names))
            return names[0]
        except Exception as e:
            log.warning("pools-overview scrape failed: %s", e)
            return None

    def _discover_ids(self) -> list[dict]:
        """Auto-discover installation_id + the full list of heat pumps from the SPA.

        Strategy (verified empirically — see docs/native-login.md §8):
          1. /from-native/<token> already loaded; SPA auto-routes to
             /installation-overview/<installation_id>.
          2. Pluck installation_id from the URL.
          3. Read all `.device-summary-item.mobile-clickable` cards on the page:
             extract their display name (.device-summary-title) and click them
             one-by-one to capture each /heat-pump-view/<id> URL.
          4. Persist [{id, name}, ...] via auth_mgr.set_pumps().

        Returns the discovered pump list (possibly empty on failure).
        """
        page = self._page
        log.info("Discovering installation + heat-pump IDs from SPA …")

        # Wait for the SPA's default route (installation-overview or direct heat-pump-view).
        try:
            page.wait_for_function(
                "() => /\\/installation-overview\\/[0-9a-f]{24}/.test(window.location.pathname)"
                " || /\\/heat-pump-view\\/[0-9a-f]{24}/.test(window.location.pathname)",
                timeout=15_000)
        except Exception:
            log.warning("SPA did not auto-route to a known view (url=%s)", page.url)

        installation_id: str | None = None
        m = _OBJECTID_RE.search(page.url or "")

        # Deep-link shortcut: SPA landed directly on a heat-pump-view (rare —
        # happens when the account has a single pump and the server skips the
        # overview screen). We have one pump_id but no installation_id and
        # no name from a card. Best we can do is record the single pump.
        if m and "heat-pump-view" in page.url:
            pump_id = m.group(1)
            pumps = [{"id": pump_id, "name": "Heat pump"}]
            _auth_mgr.set_pumps(installation_id=None, heat_pumps=pumps)
            log.info("Discovered (deep-link) single pump: %s", pump_id)
            return pumps

        if not (m and "installation-overview" in page.url):
            log.error("Could not find installation_id in landing URL: %s", page.url)
            return []
        installation_id = m.group(1)
        log.info("Discovered installation_id=%s", installation_id)

        # Installation name lives on /pools-overview. The scrape navigates away —
        # we come back to /installation-overview/<id> for pump enumeration below.
        installation_name = self._scrape_installation_name(installation_id)
        if installation_name:
            page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{BASE}/installation-overview/{installation_id}', false)")
            try:
                page.wait_for_function(
                    f"() => /\\/installation-overview\\/{installation_id}/"
                    ".test(window.location.pathname)",
                    timeout=8_000)
            except Exception:
                pass

        # Enumerate cards. We read names BEFORE clicking — once we click,
        # the SPA navigates away and the cards detach. The list order matches
        # what we click in step 2.
        try:
            page.wait_for_selector(".device-summary-item.mobile-clickable", timeout=10_000)
            # The .device-summary-title child renders a beat later than the
            # parent — wait for at least one non-empty title before reading.
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('.device-summary-item.mobile-clickable "
                ".device-summary-title')).some(e => e.textContent.trim().length > 0)",
                timeout=5_000)
        except Exception:
            log.warning("Pump card titles did not render — names may fall back to 'Heat pump N'")

        card_names = page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('.device-summary-item.mobile-clickable')) {
                const title = el.querySelector('.device-summary-title');
                let name = (title && title.textContent.trim()) || '';
                if (!name) {
                    // Fallback: first line of the card's full text content
                    const txt = el.textContent.trim();
                    name = txt.split('\\n')[0].trim();
                }
                out.push(name || 'Heat pump');
            }
            return out;
        }""")
        log.info("Found %d heat-pump card(s): %s", len(card_names), card_names)

        pumps: list[dict] = []
        seen_ids: set[str] = set()
        for index, name in enumerate(card_names):
            try:
                # Re-navigate to overview between clicks so the cards are mounted again.
                if index > 0:
                    page.evaluate(
                        f"Blazor._internal.navigationManager.navigateTo("
                        f"'{BASE}/installation-overview/{installation_id}', false)")
                    page.wait_for_selector(".device-summary-item.mobile-clickable", timeout=10_000)

                # Click the Nth card (0-based)
                page.eval_on_selector_all(
                    ".device-summary-item.mobile-clickable",
                    f"(els) => els[{index}] && els[{index}].click()")
                page.wait_for_function(
                    "() => /\\/heat-pump-view\\/[0-9a-f]{24}/.test(window.location.pathname)",
                    timeout=10_000)
                cur = _OBJECTID_RE.search(page.url or "")
                if not cur:
                    log.warning("Card %d (%r): no pump_id in URL after click (%s)",
                                index, name, page.url)
                    continue
                pid = cur.group(1)
                if pid in seen_ids:
                    log.warning("Card %d (%r) resolved to duplicate id %s — skipping",
                                index, name, pid)
                    continue
                seen_ids.add(pid)
                pumps.append({"id": pid, "name": name})
                log.info("  card %d: %s -> %s", index, name, pid)
            except Exception as e:
                log.warning("Card %d (%r) discovery failed: %s", index, name, e)

        if not pumps:
            log.error("Discovered no pumps despite finding %d card(s)", len(card_names))
            return []

        _auth_mgr.set_pumps(installation_id=installation_id,
                            installation_name=installation_name,
                            heat_pumps=pumps)
        return pumps

    def _ensure(self):
        if self._browser and self._browser.is_connected():
            return
        log.info("(Re-)launching browser …")
        self._launch()

    def _ensure_view(self, pump_id: str) -> None:
        """Ensure the page is on /heat-pump-view/<pump_id>.
        Detects expired sessions and refreshes; smart-navigates between pumps."""
        try:
            snippet = self._page.evaluate(
                "() => document.body.innerText.substring(0, 120)")
            if "403" in snippet or "must be connected" in snippet.lower():
                log.warning("Session token expired — refreshing and relaunching browser")
                _auth_mgr.refresh()
                try:
                    self._browser.close()
                    self._pw.stop()
                except Exception:
                    pass
                self._browser = None
                self._pw      = None
                self._page    = None
                raise RuntimeError("Session token expired — refreshed, will retry")
        except RuntimeError:
            raise
        except Exception:
            pass

        self._navigate_to_pump(pump_id)

    # ── get_status ────────────────────────────────────────────────────────────

    _DATA_READY_JS = """() => {
        const setpoint = document.querySelector('.order-and-value-order-number');
        const weather  = document.querySelector('.topbar-weather');
        const gauge    = document.querySelector('.co-gauge-container');
        if (!gauge) return false;
        if (setpoint && setpoint.textContent.trim()) return true;
        if (weather && weather.textContent.trim()) return true;
        return false;
    }"""

    def _wait_for_data(self, timeout_ms: int = 8_000) -> float:
        t0 = time.time()
        try:
            self._page.wait_for_function(self._DATA_READY_JS, timeout=timeout_ms)
        except Exception:
            pass
        return (time.time() - t0) * 1000

    def get_status(self, pump_id: str | None = None) -> dict:
        with self._lock:
            self._ensure()
            pid = _resolve_pump(pump_id)
            if not pid:
                raise RuntimeError(f"Unknown pump_id: {pump_id!r}")
            self._ensure_view(pid)
            # If this is a re-visit to a pump we've already been on, briefly
            # bounce to installation-overview and back to force a re-render —
            # Blazor sometimes caches the prior pump's DOM otherwise.
            installation_id = _get_installation_id()
            if installation_id and self._last_data.get(pid):
                self._page.evaluate(
                    f"Blazor._internal.navigationManager.navigateTo("
                    f"'{BASE}/installation-overview/{installation_id}', false)")
                try:
                    self._page.wait_for_selector(".device-summary-item", timeout=5_000)
                except Exception:
                    time.sleep(0.5)
                self._current_pump_id = None
                self._navigate_to_pump(pid)

            snippet = self._page.evaluate(
                "() => document.body.innerText.substring(0, 200)")
            if "403" in snippet or "must be connected" in snippet.lower():
                log.warning("Token expired during get_status — refreshing and closing browser")
                _auth_mgr.refresh()
                try:
                    self._browser.close()
                    self._pw.stop()
                except Exception:
                    pass
                self._browser = None
                self._pw      = None
                self._page    = None
                raise RuntimeError("Session token expired — refreshed, will retry")
            # Open the info panel to expose compressor / filtration status
            _info_sel = self._page.evaluate(_OPEN_INFO_PANEL_JS)
            if _info_sel:
                try:
                    self._page.wait_for_selector('.heat-pump-info-modal', timeout=2_000)
                except Exception:
                    pass  # panel didn't appear; _STATUS_JS falls back to text detection
                log.debug("Opened info panel via: %s", _info_sel)
            data = self._page.evaluate(_STATUS_JS)
            if _info_sel:
                try:
                    self._page.keyboard.press('Escape')
                except Exception:
                    pass
            null_count = sum(1 for v in data.values() if v is None)
            if null_count >= 6:
                log.warning(
                    "get_status(%s) returned %d/11 null fields — DOM may not have rendered. "
                    "Page text: %s",
                    pid, null_count,
                    self._page.evaluate("() => document.body.innerText.substring(0, 300)")[:200],
                )
            # ── per-pump staleness detection ──
            prev = self._last_data.get(pid)
            if prev is not None and data == prev:
                self._unchanged_count[pid] = self._unchanged_count.get(pid, 0) + 1
                age = time.time() - self._last_change_ts.get(pid, time.time())
                if (self._unchanged_count[pid] >= self._STALE_CALL_LIMIT
                        or age >= self._STALE_TIME_LIMIT_S):
                    log.warning(
                        "Pump %s status unchanged for %d calls / %.0fs — forcing reload "
                        "(suspect Blazor SignalR disconnect)",
                        pid, self._unchanged_count[pid], age,
                    )
                    self._load_app()
                    self._navigate_to_pump(pid)
                    data = self._page.evaluate(_STATUS_JS)
                    self._unchanged_count[pid] = 0
                    self._last_change_ts[pid] = time.time()
            else:
                self._unchanged_count[pid] = 0
                self._last_change_ts[pid] = time.time()
            self._last_data[pid] = data
            return data

    # ── set_mode ──────────────────────────────────────────────────────────────

    def set_mode(self, mode: str, pump_id: str | None = None) -> dict:
        with self._lock:
            self._ensure()
            pid = _resolve_pump(pump_id)
            if not pid:
                raise RuntimeError(f"Unknown pump_id: {pump_id!r}")
            self._ensure_view(pid)
            heat_pump = pid
            page = self._page
            edit_url = (f"{BASE}/heat-pump-edit-mode/{heat_pump}"
                        if mode in MAIN_MODES
                        else f"{BASE}/heat-pump-edit-power-mode/{heat_pump}")

            log.info("set_mode %s → navigating to edit page", mode)
            page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{edit_url}', {{forceLoad:false,replaceHistoryEntry:false,historyEntryState:null}})")
            try:
                page.wait_for_selector(
                    f"button:has-text('{mode}'), .control-selector",
                    timeout=8_000)
            except Exception:
                time.sleep(2)

            already = page.evaluate(
                "(m => {"
                "  for (const el of document.querySelectorAll('button')) {"
                "    if (el.textContent.trim() === m &&"
                "        (el.classList.contains('control-selected') || el.classList.contains('istd-sty-active')))"
                "      return true;"
                "  }"
                "  return false;"
                "})(" + repr(mode) + ")")
            if already:
                log.info("Mode %s already selected — skipping click", mode)
            else:
                clicked = False
                try:
                    page.click(f"button:has-text('{mode}')", timeout=3_000)
                    clicked = True
                    log.info("Clicked mode button: %s", mode)
                except Exception:
                    pass
                if not clicked:
                    page.evaluate(
                        "(m => {"
                        "  for (const el of document.querySelectorAll('.control-selector button, button')) {"
                        "    if (el.textContent.trim() === m &&"
                        "        !el.classList.contains('control-selected')) { el.click(); return true; }"
                        "  }"
                        "  return false;"
                        "})(" + repr(mode) + ")")
                    log.info("JS click fallback for mode %s", mode)

            try:
                page.wait_for_selector(
                    f"button.control-selected:has-text('{mode}'), "
                    f"button[class*='control-selected']:has-text('{mode}')",
                    timeout=3_000)
            except Exception:
                time.sleep(0.8)

            try:
                page.wait_for_selector("button:has-text('Valider')", timeout=1_500)
                page.click("button:has-text('Valider')", timeout=1_500)
                log.info("Clicked Valider")
            except Exception:
                pass

            page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{BASE}/heat-pump-view/{heat_pump}',"
                f"{{forceLoad:false,replaceHistoryEntry:false,historyEntryState:null}})")
            try:
                page.wait_for_selector(
                    ".heat-pump-view-mode-container, .co-gauge-container, .order-and-value-item",
                    timeout=8_000)
            except Exception:
                time.sleep(1.5)

            icon_map = {
                "Eco": "heat-pump-mode-power-eco", "Smart": "heat-pump-mode-power-smart",
                "Boost": "heat-pump-mode-power-boost", "Chauffage": "heat-pump-mode-heating",
                "Froid": "heat-pump-mode-cooling", "Automatique": "heat-pump-mode-auto",
            }
            expected = icon_map.get(mode)
            if expected:
                try:
                    page.wait_for_function(
                        f"() => document.querySelector('.{expected}') !== null && "
                        f"document.querySelector('.{expected}').offsetParent !== null",
                        timeout=4_000)
                except Exception:
                    time.sleep(1.5)
            else:
                time.sleep(2.0)
            return {"ok": True}

    # ── set_setpoint ──────────────────────────────────────────────────────────

    def set_setpoint(self, temp: float, pump_id: str | None = None) -> dict:
        with self._lock:
            self._ensure()
            pid = _resolve_pump(pump_id)
            if not pid:
                raise RuntimeError(f"Unknown pump_id: {pump_id!r}")
            self._ensure_view(pid)
            page   = self._page
            target = int(temp)

            info = page.evaluate("""(() => {
                const gauge  = document.getElementById('heat-pump-temperature-gauge-gauge');
                const handle = document.querySelector('#heat-pump-temperature-gauge-gauge .rs-handle');
                const orderNum = document.querySelector('.order-and-value-order-number');
                if (!gauge || !handle) return null;
                const gr = gauge.getBoundingClientRect();
                const hr = handle.getBoundingClientRect();
                const rs = typeof jQuery !== 'undefined' &&
                           jQuery('#heat-pump-temperature-gauge-gauge').data('roundSlider');
                return {
                    gcx: gr.x + gr.width / 2, gcy: gr.y + gr.height / 2,
                    hx:  hr.x + hr.width / 2, hy:  hr.y + hr.height / 2,
                    current: rs ? rs.getValue() : parseInt(orderNum?.textContent || '29'),
                    min: rs ? rs.options.min : 8, max: rs ? rs.options.max : 32,
                };
            })()""")

            if not info:
                log.warning("Temperature slider not found in DOM")
                return {"ok": True, "note": "slider not found in DOM"}

            current = info["current"]
            diff    = current - target
            log.info("Setpoint: current=%s target=%s diff=%s", current, target, diff)

            if diff == 0:
                return {"ok": True, "note": "already at target"}

            gcx, gcy = info["gcx"], info["gcy"]
            hx,  hy  = info["hx"],  info["hy"]
            angle    = math.atan2(hy - gcy, hx - gcx)
            r        = math.sqrt((hx - gcx)**2 + (hy - gcy)**2)
            rps      = (270 / (info["max"] - info["min"])) * (math.pi / 180)
            px_step  = r * rps
            tx       = math.sin(angle)
            ty       = -math.cos(angle)
            move     = diff * px_step

            page.mouse.move(hx, hy)
            page.mouse.down()
            time.sleep(0.05)
            steps = max(20, abs(diff) * 5)
            for i in range(steps + 1):
                frac = i / steps
                page.mouse.move(hx + tx * move * frac, hy + ty * move * frac)
                time.sleep(0.015)
            page.mouse.up()
            time.sleep(0.5)

            try:
                page.wait_for_selector(".order-validation-validate", timeout=2_500)
                page.click(".order-validation-validate")
                log.info("Setpoint validated: %s°C", temp)
            except Exception:
                page.evaluate("() => { const b = document.querySelector('.order-validation-validate'); if (b) b.click(); }")
            time.sleep(1.0)
            return {"ok": True}

    # ── power on/off ──────────────────────────────────────────────────────────

    def _get_active(self) -> bool | None:
        return self._page.evaluate("""() => {
            const btn = document.querySelector(
                '.heat-pump-on-off button, .co-on-off-button, [class*="on-off"] button');
            if (btn) {
                const p = btn.getAttribute('aria-pressed');
                if (p !== null) return p === 'true';
                return btn.classList.contains('istd-sty-active') || btn.classList.contains('active');
            }
            for (const b of document.querySelectorAll('button')) {
                const t = b.textContent.trim().toUpperCase();
                if (t === 'ON' || t === 'OFF') {
                    const p = b.getAttribute('aria-pressed');
                    if (p !== null) return p === 'true';
                    return t === 'ON';
                }
            }
            return null;
        }""")

    def _click_power(self):
        page = self._page
        for sel in [".heat-pump-on-off button", ".co-on-off-button", "[class*='on-off'] button"]:
            try:
                page.click(sel, timeout=3_000)
                log.info("Power clicked via CSS: %s", sel)
                return
            except Exception:
                pass
        result = page.evaluate("""() => {
            for (const b of document.querySelectorAll('button')) {
                const t = b.textContent.trim().toUpperCase();
                if (t === 'ON' || t === 'OFF') { b.click(); return 'text:' + t; }
            }
            for (const b of document.querySelectorAll('[class*="power"] button, [class*="switch"] button')) {
                b.click(); return 'class:' + b.className.substring(0, 40);
            }
            return null;
        }""")
        if result:
            log.info("Power clicked via JS fallback: %s", result)

    def turn_on(self, pump_id: str | None = None) -> dict:
        with self._lock:
            self._ensure()
            pid = _resolve_pump(pump_id)
            if not pid:
                raise RuntimeError(f"Unknown pump_id: {pump_id!r}")
            self._ensure_view(pid)
            is_on = self._get_active()
            if is_on is True:
                return {"ok": True, "note": "already ON"}
            self._click_power()
            time.sleep(2.0)
            return {"ok": True}

    def turn_off(self, pump_id: str | None = None) -> dict:
        with self._lock:
            self._ensure()
            pid = _resolve_pump(pump_id)
            if not pid:
                raise RuntimeError(f"Unknown pump_id: {pump_id!r}")
            self._ensure_view(pid)
            is_on = self._get_active()
            if is_on is False:
                return {"ok": True, "note": "already OFF"}
            self._click_power()
            time.sleep(2.0)
            return {"ok": True}

    # ── filtration ────────────────────────────────────────────────────────────

    def _scan_filtration_buttons(self) -> dict:
        return self._page.evaluate("""() => {
            const SELS = [
                '[class*="filtration"] button', 'button[class*="filtration"]',
                '[class*="filtration"][class*="toggle"]', '[class*="pompe-filtration"]',
                '[id*="filtration"]', '[class*="filtration"][class*="switch"]',
            ];
            for (const sel of SELS) {
                const el = document.querySelector(sel);
                if (el) {
                    const p = el.getAttribute('aria-pressed');
                    const state = p !== null ? p === 'true' :
                        el.classList.contains('istd-sty-active') ||
                        el.classList.contains('active') || el.classList.contains('on');
                    return {found: true, sel, state, cls: el.className.substring(0,120)};
                }
            }
            for (const b of document.querySelectorAll('button')) {
                const t = b.textContent.trim().toLowerCase();
                const c = b.className.toLowerCase();
                if (t.includes('filtrat') || c.includes('filtrat') ||
                    t.includes('pompe') || c.includes('pompe')) {
                    const p = b.getAttribute('aria-pressed');
                    const state = p !== null ? p === 'true' :
                        b.classList.contains('istd-sty-active') || b.classList.contains('active');
                    return {found: true, sel: 'broad', text: b.textContent.trim().substring(0,60),
                            state, cls: b.className.substring(0,120)};
                }
            }
            const btns = Array.from(document.querySelectorAll('button'))
                .map(b => b.textContent.trim().substring(0,30)).filter(t => t).slice(0,20);
            return {found: false, buttons: btns};
        }""")

    def _click_found_filtration(self, scan_result: dict):
        page = self._page
        sel = scan_result.get('sel', '')
        if sel == 'broad':
            text = scan_result.get('text', '')
            page.evaluate(
                "(t => { for (const b of document.querySelectorAll('button')) {"
                "  if (b.textContent.trim().startsWith(t.substring(0,15))) { b.click(); return; }"
                "} })(" + repr(text) + ")")
        else:
            try:
                page.click(sel, timeout=3_000)
            except Exception:
                page.evaluate("(s => { const b = document.querySelector(s); if (b) b.click(); })(" + repr(sel) + ")")

    def _toggle_filtration(self, want_on: bool, pump_id: str) -> dict:
        page = self._page
        installation_id = _get_installation_id()
        heat_pump = pump_id

        result = self._scan_filtration_buttons()
        if result.get('found'):
            is_on = result.get('state', False)
            if (want_on and is_on) or (not want_on and not is_on):
                return {"ok": True, "note": f"already {'running' if want_on else 'stopped'}"}
            self._click_found_filtration(result)
            time.sleep(2.0)
            return {"ok": True}

        # Try installation overview
        page.evaluate(
            f"Blazor._internal.navigationManager.navigateTo("
            f"'{BASE}/installation-overview/{installation_id}', false)")
        try:
            page.wait_for_selector('[class*="filtration"], [class*="pompe"], button', timeout=10_000)
        except Exception:
            time.sleep(3)
        result2 = self._scan_filtration_buttons()
        if result2.get('found'):
            is_on = result2.get('state', False)
            if not ((want_on and is_on) or (not want_on and not is_on)):
                self._click_found_filtration(result2)
                time.sleep(2.0)
            page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{BASE}/heat-pump-view/{heat_pump}', false)")
            try:
                page.wait_for_selector(".co-gauge-container, .heat-pump-view-mode-container", timeout=8_000)
            except Exception:
                time.sleep(2)
            return {"ok": True}
        else:
            page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{BASE}/heat-pump-view/{heat_pump}', false)")
            return {"ok": True, "note": "filtration button not found"}

    def start_filtration(self, pump_id: str | None = None) -> dict:
        with self._lock:
            self._ensure()
            pid = _resolve_pump(pump_id)
            if not pid:
                raise RuntimeError(f"Unknown pump_id: {pump_id!r}")
            self._ensure_view(pid)
            return self._toggle_filtration(want_on=True, pump_id=pid)

    def stop_filtration(self, pump_id: str | None = None) -> dict:
        with self._lock:
            self._ensure()
            pid = _resolve_pump(pump_id)
            if not pid:
                raise RuntimeError(f"Unknown pump_id: {pump_id!r}")
            self._ensure_view(pid)
            return self._toggle_filtration(want_on=False, pump_id=pid)


# ── Flask app ─────────────────────────────────────────────────────────────────

ctrl = PolyconnectController()
app  = Flask(__name__)


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw), 200
    except RuntimeError as e:
        msg = str(e).lower()
        if "expired" in msg or "refreshed" in msg or "recapture" in msg:
            return {"error": str(e), "auth_expired": True}, 401
        if "unknown pump_id" in msg:
            return {"error": str(e), "pump_not_found": True}, 404
        if "no session token" in msg or "no heat pump" in msg:
            return {"error": str(e), "credentials_missing": True}, 503
        return {"error": str(e)}, 500
    except Exception as e:
        log.exception("Unhandled error in %s", fn.__name__)
        return {"error": str(e)}, 500


# ── Bridge API routes (existing) ──────────────────────────────────────────────

@app.route("/health")
def health():
    creds = _auth_mgr.credentials
    auth_status = _auth_mgr.get_status()
    return jsonify({
        "ok": True,
        "service": "polyconnect-bridge",
        "version": BRIDGE_VERSION,
        "credentials_configured": creds.is_complete,
        "email_configured": auth_status["email_configured"],
        "terminal_registered": auth_status["terminal_registered"],
        "session_age_seconds": auth_status["session_age_seconds"],
        "last_error": auth_status["last_error"],
    })


@app.route("/status")
def get_status():
    """Legacy single-pump endpoint — aliases to the first discovered pump."""
    data, code = _safe(ctrl._pw_thread.call, ctrl.get_status, None)
    return jsonify(data), code


# ── Multi-pump routes (v2) ───────────────────────────────────────────────────

@app.route("/pumps")
def list_pumps():
    """List all discovered heat pumps. Empty list if discovery hasn't run yet."""
    creds = _auth_mgr.credentials
    return jsonify({
        "pumps": _get_pumps(),
        "installation_id": creds.installation_id,
        "installation_name": creds.installation_name,
    })


@app.route("/pumps/<pump_id>/status")
def pump_status(pump_id: str):
    data, code = _safe(ctrl._pw_thread.call, ctrl.get_status, pump_id)
    return jsonify(data), code


@app.route("/pumps/<pump_id>/setpoint", methods=["POST"])
def pump_setpoint(pump_id: str):
    temp = (request.get_json(silent=True) or {}).get("temperature")
    if temp is None:
        return jsonify({"error": "missing temperature"}), 400
    try:
        temp_f = float(temp)
    except (TypeError, ValueError):
        return jsonify({"error": "temperature must be a number"}), 400
    data, code = _safe(ctrl._pw_thread.call, ctrl.set_setpoint, temp_f, pump_id)
    return jsonify(data), code


@app.route("/pumps/<pump_id>/mode", methods=["POST"])
@app.route("/pumps/<pump_id>/regulation_mode", methods=["POST"])
def pump_mode(pump_id: str):
    m = (request.get_json(silent=True) or {}).get("mode", "")
    if not m:
        return jsonify({"error": "missing mode"}), 400
    if m not in (MAIN_MODES | REG_MODES):
        return jsonify({"error": f"invalid mode: {m}"}), 400
    data, code = _safe(ctrl._pw_thread.call, ctrl.set_mode, m, pump_id)
    return jsonify(data), code


@app.route("/pumps/<pump_id>/on", methods=["POST"])
def pump_on(pump_id: str):
    data, code = _safe(ctrl._pw_thread.call, ctrl.turn_on, pump_id)
    return jsonify(data), code


@app.route("/pumps/<pump_id>/off", methods=["POST"])
def pump_off(pump_id: str):
    data, code = _safe(ctrl._pw_thread.call, ctrl.turn_off, pump_id)
    return jsonify(data), code


@app.route("/pumps/<pump_id>/filtration/start", methods=["POST"])
def pump_filtration_start(pump_id: str):
    data, code = _safe(ctrl._pw_thread.call, ctrl.start_filtration, pump_id)
    return jsonify(data), code


@app.route("/pumps/<pump_id>/filtration/stop", methods=["POST"])
def pump_filtration_stop(pump_id: str):
    data, code = _safe(ctrl._pw_thread.call, ctrl.stop_filtration, pump_id)
    return jsonify(data), code


# ── Legacy single-pump aliases (v1 / v2.0 compat — target first pump) ─────────


@app.route("/debug/info-panel")
def debug_info_panel():
    """Click the info icon, wait for panel, dump its content."""
    def _dump():
        with ctrl._lock:
            ctrl._ensure()
            pid = _get_heat_pump_id()
            if not pid:
                return {"error": "no pump configured"}
            ctrl._ensure_view(pid)
            time.sleep(1.0)
            clicked = ctrl._page.evaluate(_OPEN_INFO_PANEL_JS)
            if not clicked:
                return {"error": "info button not found", "clicked": False}
            time.sleep(1.5)
            result = ctrl._page.evaluate("""() => {
                const modal = document.querySelector('.heat-pump-info-modal');
                const listItems = Array.from(document.querySelectorAll('.istd-ct-list-item')).map(el => ({
                    outerHTML: el.outerHTML.substring(0, 2000),
                    text: el.textContent.trim(),
                    classes: el.className,
                    children: Array.from(el.querySelectorAll('*')).map(c => ({
                        tag: c.tagName,
                        classes: c.className,
                        text: c.textContent.trim().substring(0, 50),
                        visible: c.offsetParent !== null,
                    })),
                }));
                return {
                    modalHTML: modal ? modal.innerHTML.substring(0, 6000) : null,
                    listItems,
                    bodyText: document.body.innerText.substring(0, 2000),
                };
            }""")
            ctrl._page.keyboard.press('Escape')
            result["clicked"] = clicked
            return result
    data, code = _safe(ctrl._pw_thread.call, _dump)
    return jsonify(data), code


@app.route("/debug/installation-overview")
def debug_installation_overview():
    """TEMP probe — dump candidate installation-name selectors."""
    def _dump():
        with ctrl._lock:
            ctrl._ensure()
            inst = _get_installation_id()
            if not inst:
                return {"error": "no installation_id known"}
            ctrl._page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{BASE}/installation-overview/{inst}', false)")
            try:
                ctrl._page.wait_for_selector(".device-summary-item, .installation-summary-title, h1, h2", timeout=8_000)
            except Exception:
                pass
            time.sleep(1.5)
            data = ctrl._page.evaluate("""() => {
                const out = {};
                const sels = [
                    '.installation-summary-title','.installation-title','.installation-name',
                    '.installation-header-title','.installations-menu-item',
                    '.topbar-title','.topbar-text','.topbar-installation','.topbar-installation-name',
                    '.page-title','.page-header','.header-title',
                    'h1','h2','h3','.title','.name',
                    '[class*="installation"][class*="title"]',
                    '[class*="installation"][class*="name"]',
                ];
                for (const sel of sels) {
                    const els = document.querySelectorAll(sel);
                    const items = [];
                    for (const el of els) {
                        const t = (el.textContent || '').trim();
                        if (t) items.push({text: t.substring(0, 160), class: el.className.substring(0, 200)});
                    }
                    if (items.length) out[sel] = items;
                }
                return {
                    url: location.href,
                    title: document.title,
                    body_preview: document.body.innerText.substring(0, 600),
                    matches: out,
                };
            }""")
            return data
    data, code = _safe(ctrl._pw_thread.call, _dump)
    return jsonify(data), code


@app.route("/debug/dom")
def debug_dom():
    """Dump DOM content for debugging selectors."""
    def _dump():
        with ctrl._lock:
            ctrl._ensure()
            heat_pump = _get_heat_pump_id()
            if not heat_pump:
                return {"error": "no pump configured"}
            ctrl._ensure_view(heat_pump)
            ctrl._page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{BASE}/heat-pump-view/{heat_pump}', false)")
            try:
                ctrl._page.wait_for_selector(
                    ".co-gauge-container, .heat-pump-view-mode-container, "
                    ".order-and-value-item",
                    timeout=8_000)
            except Exception:
                pass
            time.sleep(2.5)
            body_text = ctrl._page.evaluate("() => document.body.innerText")
            elements = ctrl._page.evaluate("""() => {
                const results = [];
                const selectors = [
                    '.order-and-value-value-number', '.order-and-value-order-number',
                    '.order-and-value-item', '.state-button-value',
                    '.heat-pump-view-temperature-value', '.device-summary-temperature',
                    '[class*="value-number"]', '[class*="water-temp"]', '[class*="waterTemp"]',
                    '[class*="setpoint"]', '.round-slider-value',
                    '.co-gauge-container', '.heat-pump-view-mode-container',
                    '.heat-pump-on-off button', '.co-on-off-button',
                    '[class*="compressor"]', '[class*="filtration"]',
                    '.topbar-weather', '[class*="alarm"]', '[class*="error"]',
                ];
                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        results.push({
                            selector: sel,
                            text: el.textContent.trim().substring(0, 100),
                            classes: el.className.substring(0, 200),
                            visible: el.offsetParent !== null,
                        });
                    }
                }
                return results;
            }""")
            return {"url": ctrl._page.url, "body_text": body_text[:2000], "elements": elements}
    data, code = _safe(ctrl._pw_thread.call, _dump)
    return jsonify(data), code


@app.route("/setpoint", methods=["POST"])
def setpoint():
    temp = (request.get_json(silent=True) or {}).get("temperature")
    if temp is None:
        return jsonify({"error": "missing temperature"}), 400
    try:
        temp_f = float(temp)
    except (TypeError, ValueError):
        return jsonify({"error": "temperature must be a number"}), 400
    data, code = _safe(ctrl._pw_thread.call, ctrl.set_setpoint, temp_f, None)
    return jsonify(data), code


@app.route("/mode", methods=["POST"])
@app.route("/regulation_mode", methods=["POST"])
def mode():
    m = (request.get_json(silent=True) or {}).get("mode", "")
    if not m:
        return jsonify({"error": "missing mode"}), 400
    if m not in (MAIN_MODES | REG_MODES):
        return jsonify({"error": f"invalid mode: {m}"}), 400
    data, code = _safe(ctrl._pw_thread.call, ctrl.set_mode, m, None)
    return jsonify(data), code


@app.route("/on", methods=["POST"])
def turn_on():
    data, code = _safe(ctrl._pw_thread.call, ctrl.turn_on, None)
    return jsonify(data), code


@app.route("/off", methods=["POST"])
def turn_off():
    data, code = _safe(ctrl._pw_thread.call, ctrl.turn_off, None)
    return jsonify(data), code


@app.route("/filtration/start", methods=["POST"])
def filtration_start():
    data, code = _safe(ctrl._pw_thread.call, ctrl.start_filtration, None)
    return jsonify(data), code


@app.route("/filtration/stop", methods=["POST"])
def filtration_stop():
    data, code = _safe(ctrl._pw_thread.call, ctrl.stop_filtration, None)
    return jsonify(data), code


# ── Auth API routes (v2) ──────────────────────────────────────────────────────

@app.route("/auth/status")
def auth_status():
    return jsonify(_auth_mgr.get_status())


@app.route("/auth/refresh", methods=["POST"])
def auth_refresh():
    """Force a new login round-trip with the currently-configured credentials."""
    return jsonify(_auth_mgr.refresh())


@app.route("/auth/credentials", methods=["POST"])
def auth_set_credentials():
    """Update email/password and (optionally) the heat pump / installation IDs.
    Triggers an immediate login attempt."""
    body = request.get_json(silent=True) or {}
    email = body.get("email", "")
    password = body.get("password", "")
    if not email or not password:
        return jsonify({"ok": False, "error": "email and password are required"}), 400
    return jsonify(_auth_mgr.set_credentials(
        email=email,
        password=password,
        installation_id=body.get("installation_id"),
        heat_pump_id=body.get("heat_pump_id"),
    ))


@app.route("/auth/reset", methods=["POST"])
def auth_reset():
    """Wipe auth state, then restart the process. The startup path handles
    re-registration, login, browser (re-)launch, and SPA ID discovery."""
    _auth_mgr.reset_credentials()

    def _relaunch():
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Timer(0.3, _relaunch).start()
    return jsonify({"ok": True, "message": "Auth reset — restarting…"})


@app.route("/admin/restart", methods=["POST"])
def admin_restart():
    """Restart the process in place via os.execv so the bridge comes back
    on its own (no external supervisor needed, works both in the HA add-on
    and in local dev)."""
    def _relaunch():
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Timer(0.3, _relaunch).start()
    return jsonify({"ok": True, "message": "Server restarting…"})


# ── Ingress Control Panel (HTML at /) ─────────────────────────────────────────

@app.route("/")
def ingress_panel():
    """Serve the control panel visible through HA ingress."""
    return Response(_build_ingress_html(), mimetype="text/html")


def _build_ingress_html() -> str:
    creds = _auth_mgr.credentials
    st = _auth_mgr.get_status()
    session_age = st.get("session_age_seconds")
    last_err = st.get("last_error") or ""

    def _fmt_age(secs: int | None) -> str:
        if secs is None:
            return "—"
        if secs < 60:
            return f"{secs}s"
        m, s = divmod(secs, 60)
        if m < 60:
            return f"{m}m {s}s"
        h, m = divmod(m, 60)
        if h < 24:
            return f"{h}h {m}m"
        d, h = divmod(h, 24)
        return f"{d}d {h}h"

    age_disp = _fmt_age(session_age)
    stale = bool(st.get("session_stale"))
    session_ok = bool(creds.token) and not stale
    if not creds.token:
        session_state, session_cls = "NONE", "err"
        session_detail = "Not acquired"
    elif stale:
        session_state, session_cls = "STALE", "warn"
        session_detail = f"{len(creds.token)} chars · age {age_disp}"
    else:
        session_state, session_cls = "ACTIVE", "ok"
        session_detail = f"{len(creds.token)} chars · age {age_disp}"

    terminal_ok = bool(st["terminal_registered"])
    terminal_txt = st["terminal_id"] or "Not registered"

    pumps_rows = ''.join(
        f'<div class="pump-row"><span class="pump-name">{p["name"]}</span>'
        f'<span class="pump-id">{p["id"]}</span></div>'
        for p in creds.heat_pumps
    ) or '<div class="empty">No heat pumps discovered yet.</div>'

    overall_ok = creds.is_complete
    overall_txt = "Ready" if overall_ok else "Incomplete"

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polyconnect Bridge</title>
<style>
:root {{
  --bg: #04141f; --bg-2: #061c2b; --surface: #0a2540; --surface-2: #0e3555;
  --border: #144b74; --text: #e0f2fe; --dim: #7dd3fc; --muted: #38bdf8;
  --accent: #38bdf8; --accent-2: #06b6d4; --deep: #0369a1;
  --green: #34d399; --yellow: #fbbf24; --red: #f87171;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background:
    radial-gradient(1200px 600px at 10% -10%, rgba(56,189,248,0.14), transparent 60%),
    radial-gradient(1000px 500px at 100% 0%, rgba(6,182,212,0.10), transparent 60%),
    var(--bg);
  color: var(--text); min-height: 100vh; padding: 1.25rem;
}}
.container {{ max-width: 780px; margin: 0 auto; }}

/* Hero — animated pool-water gradient, text only */
@keyframes waterFlow {{
  0%   {{ background-position:   0% 50%; }}
  50%  {{ background-position: 100% 50%; }}
  100% {{ background-position:   0% 50%; }}
}}
@keyframes shimmer {{
  0%, 100% {{ opacity: 0.55; transform: translateX(-8%); }}
  50%      {{ opacity: 0.95; transform: translateX( 8%); }}
}}
.hero {{
  position: relative; overflow: hidden; border-radius: 18px;
  padding: 2.1rem 1.6rem; text-align: center;
  background: linear-gradient(120deg,
      #0369a1 0%, #0ea5e9 22%, #06b6d4 44%, #22d3ee 62%, #0284c7 80%, #164e63 100%);
  background-size: 300% 300%;
  animation: waterFlow 14s ease-in-out infinite;
  box-shadow:
    0 20px 60px -30px rgba(14,165,233,0.65),
    0 0 0 1px rgba(255,255,255,0.06) inset;
}}
.hero::before {{
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background:
    radial-gradient(60% 100% at 20% 0%, rgba(255,255,255,0.28), transparent 60%),
    radial-gradient(50%  80% at 80% 100%, rgba(255,255,255,0.16), transparent 60%);
  mix-blend-mode: screen;
  animation: shimmer 7s ease-in-out infinite;
}}
.hero::after {{
  /* subtle caustic-like streaks */
  content: ""; position: absolute; inset: -50%;
  background:
    repeating-linear-gradient(115deg,
      rgba(255,255,255,0.05) 0 2px,
      transparent 2px 22px);
  opacity: 0.6; pointer-events: none;
  animation: waterFlow 22s linear infinite reverse;
}}
.hero h1 {{
  position: relative; font-size: 1.9rem; font-weight: 800;
  letter-spacing: -0.02em; color: #ffffff;
  text-shadow: 0 2px 20px rgba(2,132,199,0.65), 0 1px 0 rgba(255,255,255,0.15);
}}
.hero .sub {{
  position: relative; font-size: 0.88rem; margin-top: 0.35rem;
  color: rgba(240,249,255,0.9);
  text-shadow: 0 1px 8px rgba(2,132,199,0.55);
}}

/* Cards */
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0.9rem; }}
@media (max-width: 560px) {{ .grid {{ grid-template-columns: 1fr; }} }}
.container > .hero,
.container > .card,
.container > .grid,
.container > .actions {{ margin-bottom: 0.9rem; }}
.container > .hero {{ margin-bottom: 1.25rem; }}
.card {{
  background: linear-gradient(180deg, var(--surface) 0%, var(--surface-2) 100%);
  border: 1px solid var(--border); border-radius: 14px; padding: 1.1rem 1.15rem;
  box-shadow: 0 10px 30px -20px rgba(0,0,0,0.5);
}}
.card h2 {{
  font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em;
  color: var(--dim); text-transform: uppercase; margin-bottom: 0.7rem;
  display: flex; align-items: center; gap: 0.45rem;
}}
.card h2 .dot {{ width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }}

.stat {{ display: flex; flex-direction: column; align-items: flex-start; gap: 0.5rem; }}
.stat .big {{
  font-size: 0.85rem; font-weight: 500; font-family: ui-monospace, SFMono-Regular, monospace;
  word-break: break-all; color: var(--text);
}}
.stat .big.muted {{ color: var(--dim); }}
.pill {{
  display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px;
  font-size: 0.7rem; font-weight: 600; letter-spacing: 0.03em;
}}
.pill.ok  {{ background: rgba(52,211,153,0.15); color: var(--green); border: 1px solid rgba(52,211,153,0.3); }}
.pill.warn{{ background: rgba(251,191,36,0.15); color: var(--yellow); border: 1px solid rgba(251,191,36,0.3); }}
.pill.err {{ background: rgba(248,113,113,0.15); color: var(--red); border: 1px solid rgba(248,113,113,0.3); }}

.ok  {{ color: var(--green); }}
.warn{{ color: var(--yellow); }}
.err {{ color: var(--red); }}

.pump-row {{
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.55rem 0.7rem; border-radius: 8px; margin-top: 0.5rem;
  background: rgba(56,189,248,0.06); border: 1px solid rgba(56,189,248,0.12);
}}
.pump-name {{ font-weight: 600; font-size: 0.88rem; }}
.pump-id {{ font-family: ui-monospace, monospace; font-size: 0.75rem; color: var(--dim); word-break: break-all; }}
.empty {{ color: var(--muted); font-size: 0.82rem; padding: 0.5rem 0; font-style: italic; }}

.actions {{
  display: flex; flex-wrap: wrap; gap: 0.6rem;
  background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
  padding: 1rem; margin-top: 0.2rem;
}}
.btn {{
  flex: 1 1 auto; min-width: 160px;
  padding: 0.7rem 1rem; border-radius: 10px;
  font-size: 0.85rem; font-weight: 600; border: 1px solid transparent;
  cursor: pointer; transition: transform 0.05s, filter 0.15s;
  display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem;
}}
.btn:hover {{ filter: brightness(1.1); }}
.btn:active {{ transform: translateY(1px); }}
.btn:disabled {{ opacity: 0.5; cursor: not-allowed; }}
.btn-primary {{ background: linear-gradient(135deg, #0ea5e9, #06b6d4 60%, #22d3ee); color: white; }}
.btn-outline {{ background: transparent; color: var(--text); border-color: var(--border); }}
.btn-danger  {{ background: linear-gradient(135deg, #ef4444, #f472b6); color: white; }}

.banner {{
  padding: 0.7rem 0.9rem; border-radius: 10px; font-size: 0.82rem;
  margin-bottom: 0.9rem; display: none;
}}
.banner.err  {{ background: rgba(248,113,113,0.1); border: 1px solid rgba(248,113,113,0.3); color: var(--red); }}
.banner.info {{ background: rgba(56,189,248,0.1); border: 1px solid rgba(56,189,248,0.3); color: var(--accent); }}
.banner.show {{ display: block; }}

.footer {{ text-align: center; color: var(--muted); font-size: 0.72rem; margin-top: 1.2rem; }}
</style>
</head>
<body>
<div class="container">
    <div class="hero">
      <h1>Polyconnect Bridge</h1>
      <div class="sub">v{BRIDGE_VERSION} · pool heat-pump control plane</div>
    </div>

    <div id="banner" class="banner"></div>

    <!-- IDs card (installation first, then pumps) -->
    <div class="card">
      <h2><span class="dot"></span>Installation</h2>
      <div class="stat">
        <span style="color:var(--dim); font-size:0.72rem; letter-spacing:0.08em; text-transform:uppercase;">Name</span>
        <span id="installation-name" class="big {'ok' if creds.installation_name else ('muted' if creds.installation_id else 'warn')}" style="font-size:1.05rem; font-family: system-ui, sans-serif; font-weight:600;">{creds.installation_name or ('Unnamed installation' if creds.installation_id else 'Not set')}</span>
        <span id="installation-id" class="big muted" style="font-size:0.78rem;">{creds.installation_id or ''}</span>
      </div>

      <div style="margin-top: 1.1rem;">
        <div style="color:var(--dim); font-size:0.72rem; letter-spacing:0.08em; text-transform:uppercase;">
          Heat pumps · <span id="pumps-count">{len(creds.heat_pumps)}</span>
        </div>
        <div id="pumps-list">{pumps_rows}</div>
      </div>
    </div>

    <!-- Status grid -->
    <div class="grid">
      <div class="card">
        <h2><span class="dot"></span>Session</h2>
        <div class="stat">
          <span id="session-pill" class="pill {session_cls}">{session_state}</span>
          <span id="session-detail" class="big {'muted' if not creds.token else ''}">{session_detail}</span>
        </div>
      </div>

      <div class="card">
        <h2><span class="dot"></span>Terminal</h2>
        <div class="stat">
          <span id="terminal-pill" class="pill {'ok' if terminal_ok else 'warn'}">{'REGISTERED' if terminal_ok else 'PENDING'}</span>
          <span id="terminal-detail" class="big {'muted' if not terminal_ok else ''}">{terminal_txt}</span>
        </div>
      </div>

      <div class="card">
        <h2><span class="dot"></span>Bridge</h2>
        <div class="stat">
          <span id="bridge-pill" class="pill {'ok' if overall_ok else 'warn'}">{overall_txt.upper()}</span>
          <span id="bridge-detail" class="big">{'All systems go' if overall_ok else 'Awaiting login'}</span>
        </div>
      </div>

      <div class="card">
        <h2><span class="dot"></span>Last Error</h2>
        <div class="stat">
          <span id="error-pill" class="pill {'err' if last_err else 'ok'}">{'ERROR' if last_err else 'CLEAN'}</span>
          <span id="error-detail" class="big {'muted' if not last_err else 'err'}">{last_err or 'None'}</span>
        </div>
      </div>
    </div>

    <!-- Actions -->
    <div class="actions">
      <button type="button" class="btn btn-primary" id="btn-refresh">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>
        </svg>
        Refresh Browser
      </button>
      <button type="button" class="btn btn-outline" id="btn-restart">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2v6"/><path d="M18.36 5.64a9 9 0 1 1-12.73 0"/>
        </svg>
        Restart Server
      </button>
      <button type="button" class="btn btn-danger" id="btn-reset">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
        </svg>
        Reset Auth &amp; IDs
      </button>
    </div>

    <div class="footer">Credentials are configured via the add-on options.</div>
</div>

<script>
const BASE = (() => {{
    try {{
        if (window.location.pathname.includes('/api/hassio_ingress/')) {{
            return window.location.pathname.replace(/\\/?$/, '/');
        }}
    }} catch(e) {{}}
    try {{
        const base = new URL(document.baseURI);
        if (base.pathname.includes('/api/hassio_ingress/')) {{
            return base.pathname.replace(/\\/?$/, '/');
        }}
    }} catch(e) {{}}
    return '/';
}})();

const banner = document.getElementById('banner');
function show(msg, kind) {{
    banner.textContent = msg;
    banner.className = 'banner ' + kind + ' show';
}}
function clearBanner() {{ banner.className = 'banner'; }}

async function post(path, btn, busyLabel, waitReload) {{
    clearBanner();
    const originalLabel = btn.innerHTML;
    btn.disabled = true; btn.innerHTML = busyLabel;
    try {{
        const r = await fetch(BASE + path, {{method: 'POST'}});
        const d = await r.json();
        if (d.ok) {{
            if (waitReload) {{
                show(d.message || 'Done. Reloading…', 'info');
                setTimeout(() => location.reload(), waitReload);
            }} else {{
                location.reload();
            }}
        }} else {{
            show(d.error || 'Request failed', 'err');
            btn.disabled = false; btn.innerHTML = originalLabel;
        }}
    }} catch (ex) {{
        show('Request failed: ' + ex, 'err');
        btn.disabled = false; btn.innerHTML = originalLabel;
    }}
}}

function setStat(key, pillText, pillCls, detail, detailMuted) {{
    const p = document.getElementById(key + '-pill');
    p.textContent = pillText;
    p.className = 'pill ' + pillCls;
    const d = document.getElementById(key + '-detail');
    d.textContent = detail;
    d.className = 'big' + (detailMuted ? ' muted' : '');
}}
function clearAuthUI() {{
    const name = document.getElementById('installation-name');
    name.textContent = 'Not set';
    name.className = 'big warn';
    name.style.cssText = 'font-size:1.05rem; font-family: system-ui, sans-serif; font-weight:600;';
    document.getElementById('installation-id').textContent = '';
    document.getElementById('pumps-count').textContent = '0';
    document.getElementById('pumps-list').innerHTML =
        '<div class="empty">No heat pumps discovered yet.</div>';
    setStat('session',  'NONE',       'err',  'Not acquired',    true);
    setStat('terminal', 'PENDING',    'warn', 'Not registered',  true);
    setStat('bridge',   'INCOMPLETE', 'warn', 'Awaiting login',  false);
    setStat('error',    'CLEAN',      'ok',   'None',            true);
}}

document.getElementById('btn-refresh').addEventListener('click', (e) => {{
    post('auth/refresh', e.currentTarget, 'Refreshing…', 0);
}});
document.getElementById('btn-restart').addEventListener('click', (e) => {{
    if (!confirm('Restart the bridge server?')) return;
    post('admin/restart', e.currentTarget, 'Restarting…', 4000);
}});
document.getElementById('btn-reset').addEventListener('click', async (e) => {{
    if (!confirm('Wipe all auth state and restart? Takes ~5 seconds.')) return;
    const btn = e.currentTarget;
    btn.disabled = true; btn.innerHTML = 'Clearing…';
    clearAuthUI();
    show('Auth state cleared — restarting…', 'info');
    await new Promise(r => setTimeout(r, 900));
    btn.innerHTML = 'Restarting…';
    fetch(BASE + 'auth/reset', {{method: 'POST'}}).catch(() => {{}});
    setTimeout(() => location.reload(), 5000);
}});
</script>
</body>
</html>'''


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Starting Polyconnect Bridge v%s on port %d", BRIDGE_VERSION, PORT)

    if _auth_mgr.credentials.token:
        log.info("Session token ready — launching Playwright browser")
        try:
            ctrl._pw_thread.call(ctrl._launch)
        except Exception as e:
            log.warning("Pre-launch failed: %s — will retry on first request", e)
    else:
        log.warning("Credentials incomplete — open the add-on UI to log in")

    from waitress import serve
    serve(app, host="0.0.0.0", port=PORT, threads=8)
