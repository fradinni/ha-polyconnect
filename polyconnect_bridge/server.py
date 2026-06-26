#!/usr/bin/env python3
"""Polyconnect Bridge Server — HA Add-on v2.0.0

Features:
- Persistent Chromium instance for controlling Polyconnect heat pumps
- Integrated credential capture via mitmproxy (start/stop from HA ingress)
- Credentials stored in /data/ (persistent across add-on updates)

Ports:
- 8765: Main API (HA ingress) — bridge REST + capture control + control panel
- 8080: Phone-facing setup UI (only during capture)
- 8888: mitmproxy (only during capture)
"""
from __future__ import annotations

import math, os, logging, threading, time, json, sys
from flask import Flask, jsonify, request, Response
from pathlib import Path

from capture_manager import CaptureManager, DATA_DIR

# ── Credential loading (from /data/ persistent storage) ───────────────────────

_capture_mgr = CaptureManager()

# Load credentials — these update dynamically after capture
def _get_token() -> str:
    """Get current token — re-reads from manager (may update after capture)."""
    return _capture_mgr.credentials.token or ""

def _get_heat_pump_id() -> str:
    return _capture_mgr.credentials.heat_pump_id or ""

def _get_installation_id() -> str:
    inst = _capture_mgr.credentials.installation_id or ""
    if not inst and _get_heat_pump_id():
        # Fallback: derive from heat_pump_id (often differs by last char)
        hp = _get_heat_pump_id()
        inst = hp[:-1] + chr(ord(hp[-1]) + 1)
    return inst


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

    // Compressor
    let compressorRunning = false;
    const compEl = document.querySelector('[class*="compressor"], [class*="compresseur"]');
    if (compEl) compressorRunning = compEl.classList.contains('running') || compEl.classList.contains('active');
    if (!compressorRunning) {
        const body = document.body.innerText.toLowerCase();
        if (body.includes('compresseur') && body.includes('en marche')) compressorRunning = true;
    }

    // Filtration
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

    // COP / power
    let cop = null, powerConsumptionW = null;
    for (const row of document.querySelectorAll('.order-and-value-item, [class*="data-item"]')) {
        const txt = row.textContent.toLowerCase();
        const v = parseNum(row.textContent);
        if (v === null) continue;
        if (txt.includes('cop') || txt.includes('performance')) cop = v;
        if ((txt.includes('watt') || txt.includes(' w ') || txt.includes('puissance') ||
             txt.includes('power') || txt.includes('consomm')) && v > 50) powerConsumptionW = v;
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
        cop:                 cop,
        powerConsumptionW:   powerConsumptionW,
        errorCode:           alarmActive ? 1 : 0,
    };
}
"""

# ── Persistent browser controller ─────────────────────────────────────────────

class PolyconnectController:
    """Single persistent Chromium instance, serialised by a threading lock."""

    def __init__(self):
        self._lock   = threading.Lock()
        self._pw     = None
        self._browser= None
        self._ctx    = None
        self._page   = None

    def _launch(self):
        from playwright.sync_api import sync_playwright
        token = _get_token()
        heat_pump = _get_heat_pump_id()
        if not token:
            raise RuntimeError("No session token configured — run capture first")
        if not heat_pump:
            raise RuntimeError("No heat pump ID configured — run capture first")

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
        self._load_app()

    def _load_app(self):
        token = _get_token()
        heat_pump = _get_heat_pump_id()
        page = self._page
        page.goto(f"{BASE}/from-native/{token}", wait_until="domcontentloaded", timeout=30_000)
        try:
            page.wait_for_function(
                "() => typeof Blazor !== 'undefined' && Blazor._internal",
                timeout=20_000)
        except Exception:
            pass
        body = page.evaluate("() => document.body.innerText.substring(0, 200)")
        if "403" in body or "must be connected" in body.lower():
            raise RuntimeError("Session token expired — recapture needed")
        page.evaluate(
            f"Blazor._internal.navigationManager.navigateTo("
            f"'{BASE}/heat-pump-view/{heat_pump}', false)")
        try:
            page.wait_for_selector(
                ".co-gauge-container, .heat-pump-view-mode-container, "
                ".order-and-value-item, .heat-pump-mode",
                timeout=12_000)
        except Exception:
            pass
        time.sleep(3.0)
        log.info("Browser launched and heat pump view loaded")

    def _ensure(self):
        if self._browser and self._browser.is_connected():
            return
        log.info("(Re-)launching browser …")
        self._launch()

    def _ensure_view(self):
        heat_pump = _get_heat_pump_id()
        try:
            snippet = self._page.evaluate(
                "() => document.body.innerText.substring(0, 120)")
            if "403" in snippet or "must be connected" in snippet.lower():
                log.warning("Session token expired — closing browser")
                try:
                    self._browser.close()
                    self._pw.stop()
                except Exception:
                    pass
                self._browser = None
                self._pw      = None
                self._page    = None
                raise RuntimeError("Session token expired — recapture needed")
        except RuntimeError:
            raise
        except Exception:
            pass

        if "heat-pump-view" not in self._page.url:
            self._page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{BASE}/heat-pump-view/{heat_pump}', false)")
            try:
                self._page.wait_for_selector(
                    ".co-gauge-container, .heat-pump-view-mode-container",
                    timeout=8_000)
            except Exception:
                time.sleep(2)

    # ── get_status ────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        with self._lock:
            self._ensure()
            self._ensure_view()
            time.sleep(1.5)
            return self._page.evaluate(_STATUS_JS)

    # ── set_mode ──────────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> dict:
        with self._lock:
            self._ensure()
            heat_pump = _get_heat_pump_id()
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

    def set_setpoint(self, temp: float) -> dict:
        with self._lock:
            self._ensure()
            self._ensure_view()
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

    def turn_on(self) -> dict:
        with self._lock:
            self._ensure(); self._ensure_view()
            is_on = self._get_active()
            if is_on is True:
                return {"ok": True, "note": "already ON"}
            self._click_power()
            time.sleep(2.0)
            return {"ok": True}

    def turn_off(self) -> dict:
        with self._lock:
            self._ensure(); self._ensure_view()
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

    def _toggle_filtration(self, want_on: bool) -> dict:
        page = self._page
        installation_id = _get_installation_id()
        heat_pump = _get_heat_pump_id()

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

    def start_filtration(self) -> dict:
        with self._lock:
            self._ensure(); self._ensure_view()
            return self._toggle_filtration(want_on=True)

    def stop_filtration(self) -> dict:
        with self._lock:
            self._ensure(); self._ensure_view()
            return self._toggle_filtration(want_on=False)


# ── Flask app ─────────────────────────────────────────────────────────────────

ctrl = PolyconnectController()
app  = Flask(__name__)

_setup_server = None  # reference to the phone-facing HTTP server


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw), 200
    except RuntimeError as e:
        if "expired" in str(e).lower() or "recapture" in str(e).lower():
            return {"error": str(e), "auth_expired": True}, 401
        if "no session token" in str(e).lower() or "no heat pump" in str(e).lower():
            return {"error": str(e), "credentials_missing": True}, 503
        return {"error": str(e)}, 500
    except Exception as e:
        log.exception("Unhandled error in %s", fn.__name__)
        return {"error": str(e)}, 500


# ── Bridge API routes (existing) ──────────────────────────────────────────────

@app.route("/health")
def health():
    creds = _capture_mgr.credentials
    return jsonify({
        "ok": True,
        "service": "polyconnect-bridge",
        "version": "2.0.0",
        "credentials_configured": creds.is_complete,
        "capture_phase": _capture_mgr.status.phase.value,
    })


@app.route("/status")
def get_status():
    data, code = _safe(ctrl.get_status)
    return jsonify(data), code


@app.route("/setpoint", methods=["POST"])
def setpoint():
    temp = (request.get_json(silent=True) or {}).get("temperature")
    if temp is None:
        return jsonify({"error": "missing temperature"}), 400
    data, code = _safe(ctrl.set_setpoint, float(temp))
    return jsonify(data), code


@app.route("/mode", methods=["POST"])
@app.route("/regulation_mode", methods=["POST"])
def mode():
    m = (request.get_json(silent=True) or {}).get("mode", "")
    if not m:
        return jsonify({"error": "missing mode"}), 400
    data, code = _safe(ctrl.set_mode, m)
    return jsonify(data), code


@app.route("/on", methods=["POST"])
def turn_on():
    data, code = _safe(ctrl.turn_on)
    return jsonify(data), code


@app.route("/off", methods=["POST"])
def turn_off():
    data, code = _safe(ctrl.turn_off)
    return jsonify(data), code


@app.route("/filtration/start", methods=["POST"])
def filtration_start():
    data, code = _safe(ctrl.start_filtration)
    return jsonify(data), code


@app.route("/filtration/stop", methods=["POST"])
def filtration_stop():
    data, code = _safe(ctrl.stop_filtration)
    return jsonify(data), code


# ── Capture API routes (new) ──────────────────────────────────────────────────

@app.route("/capture/status")
def capture_status():
    return jsonify(_capture_mgr.get_status())


@app.route("/capture/start", methods=["POST"])
def capture_start():
    global _setup_server
    result = _capture_mgr.start_capture()
    if result.get("ok") and _setup_server is None:
        from setup_ui import start_setup_server
        _setup_server = start_setup_server(_capture_mgr)
        log.info("Setup UI server started on port 8080")
    return jsonify(result)


@app.route("/capture/stop", methods=["POST"])
def capture_stop():
    global _setup_server
    result = _capture_mgr.stop_capture()
    if _setup_server:
        from setup_ui import stop_setup_server
        stop_setup_server(_setup_server)
        _setup_server = None
        log.info("Setup UI server stopped")
    return jsonify(result)


@app.route("/capture/reset", methods=["POST"])
def capture_reset():
    global _setup_server
    # Stop capture if running
    if _capture_mgr.status.phase.value != "idle":
        _capture_mgr.stop_capture()
        if _setup_server:
            from setup_ui import stop_setup_server
            stop_setup_server(_setup_server)
            _setup_server = None
    # Clear credentials
    _capture_mgr.reset_credentials()
    return jsonify({"ok": True, "message": "Credentials cleared. Start capture to recapture."})


# ── Ingress Control Panel (HTML at /) ─────────────────────────────────────────

@app.route("/")
def ingress_panel():
    """Serve the control panel visible through HA ingress."""
    # HA sets X-Ingress-Path header (e.g. /api/hassio_ingress/<token>)
    ingress_path = request.headers.get("X-Ingress-Path", "")
    # Log all headers for debugging ingress issues
    log.info("Ingress panel request headers: %s",
             {k: v for k, v in request.headers if k.lower().startswith("x-")})
    log.info("X-Ingress-Path=%r, Referer=%r", ingress_path, request.headers.get("Referer", ""))
    return Response(_build_ingress_html(ingress_path), mimetype="text/html")


def _build_ingress_html(ingress_path: str = "") -> str:
    creds = _capture_mgr.credentials
    phase = _capture_mgr.status.phase.value
    local_ip = _capture_mgr.status.local_ip or "detecting..."
    # Ensure base path ends with /
    base_path = ingress_path.rstrip("/") + "/" if ingress_path else "/"

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polyconnect Bridge</title>
<style>
:root {{ --bg: #1a1a2e; --surface: #16213e; --border: #0f3460; --text: #e4e4e4; --dim: #8b8b8b; --accent: #0ea5e9; --green: #4ade80; --yellow: #fbbf24; --red: #f87171; }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: system-ui, sans-serif; background:var(--bg); color:var(--text); padding:1.5rem; min-height:100vh; }}
.container {{ max-width:600px; margin:0 auto; }}
h1 {{ font-size:1.4rem; margin-bottom:0.3rem; }}
.subtitle {{ color:var(--dim); font-size:0.82rem; margin-bottom:1.5rem; }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:1.2rem; margin-bottom:1rem; }}
.card h2 {{ font-size:0.9rem; color:var(--accent); margin-bottom:0.7rem; }}
.row {{ display:flex; justify-content:space-between; align-items:center; padding:0.4rem 0; }}
.row .label {{ color:var(--dim); font-size:0.8rem; }}
.row .value {{ font-size:0.8rem; font-family:monospace; }}
.ok {{ color:var(--green); }}
.warn {{ color:var(--yellow); }}
.err {{ color:var(--red); }}
.btn {{ display:inline-block; padding:0.6rem 1.2rem; border-radius:8px; font-size:0.82rem; font-weight:600; border:none; cursor:pointer; margin-right:0.5rem; margin-top:0.5rem; }}
.btn-primary {{ background:var(--accent); color:white; }}
.btn-danger {{ background:var(--red); color:white; }}
.btn-outline {{ background:transparent; border:1px solid var(--border); color:var(--text); }}
.btn:hover {{ opacity:0.9; }}
.phone-url {{ background:var(--bg); border:1px solid var(--accent); border-radius:8px; padding:0.8rem; text-align:center; margin:0.8rem 0; }}
.phone-url .label {{ font-size:0.7rem; color:var(--dim); text-transform:uppercase; }}
.phone-url .url {{ font-size:1.1rem; font-weight:700; color:var(--accent); font-family:monospace; }}
#capture-section {{ display: {"block" if phase != "idle" else "none"}; }}
</style>
</head>
<body>
<div class="container">
    <h1>Polyconnect Bridge</h1>
    <p class="subtitle">v2.0.0 — Pool heat pump control via Playwright</p>

    <!-- Credentials Status -->
    <div class="card">
        <h2>Credentials</h2>
        <div class="row">
            <span class="label">Token</span>
            <span class="value {'ok' if creds.token else 'warn'}">{'Configured (' + str(len(creds.token)) + ' chars)' if creds.token else 'Not captured'}</span>
        </div>
        <div class="row">
            <span class="label">Heat Pump ID</span>
            <span class="value {'ok' if creds.heat_pump_id else 'warn'}">{creds.heat_pump_id or 'Not captured'}</span>
        </div>
        <div class="row">
            <span class="label">Installation ID</span>
            <span class="value {'ok' if creds.installation_id else 'warn'}">{creds.installation_id or 'Not captured'}</span>
        </div>
        <div class="row">
            <span class="label">Status</span>
            <span class="value {'ok' if creds.is_complete else 'warn'}">{'Ready' if creds.is_complete else 'Incomplete — capture needed'}</span>
        </div>
    </div>

    <!-- Capture Controls -->
    <div class="card">
        <h2>Credential Capture</h2>
        <p style="color:var(--dim); font-size:0.8rem; margin-bottom:0.8rem;">
            Capture your Polyconnect credentials by proxying your phone's traffic.
            This requires your phone to be on the same WiFi network.
        </p>
        <div>
            <button class="btn btn-primary" onclick="startCapture()" id="btn-start">Start Capture</button>
            <button class="btn btn-outline" onclick="stopCapture()" id="btn-stop" style="display:none;">Stop Capture</button>
            <button class="btn btn-danger" onclick="resetCredentials()">Reset Credentials</button>
        </div>

        <div id="capture-section">
            <div class="phone-url">
                <div class="label">Open this on your phone:</div>
                <div class="url" id="phone-url">http://{local_ip}:8080</div>
            </div>
            <div class="row">
                <span class="label">Capture Phase</span>
                <span class="value" id="cap-phase">{phase}</span>
            </div>
        </div>
    </div>

    <!-- Bridge Status -->
    <div class="card">
        <h2>Bridge</h2>
        <div class="row">
            <span class="label">Playwright</span>
            <span class="value" id="bridge-status">{'Ready' if creds.is_complete else 'Waiting for credentials'}</span>
        </div>
    </div>
</div>

<script>
// Detect the correct base URL for API calls.
// HA ingress serves this page at /api/hassio_ingress/<token>/
// but the document context is the main HA page, so we need the absolute ingress path.
const BASE = (() => {{
    // Primary: server injected the X-Ingress-Path
    const serverBase = '{base_path}';
    if (serverBase && serverBase !== '/' && serverBase.includes('ingress')) {{
        return serverBase;
    }}
    // Fallback: find ingress path from the page's fetch origin
    // When loaded via ingress, HA frontend sets a data attribute or we can detect from script src
    const scripts = document.querySelectorAll('script[src]');
    for (const s of scripts) {{
        const m = s.src.match(/(\\/api\\/hassio_ingress\\/[^/]+)/);
        if (m) return m[1] + '/';
    }}
    // Last resort: try to find it from the browser history/referrer
    const match = document.referrer && document.referrer.match(/(\\/api\\/hassio_ingress\\/[^/]+)/);
    if (match) return match[1] + '/';
    // If all else fails, use the server-provided value (might be just '/')
    return serverBase;
}})();

console.log('[Polyconnect] BASE path:', BASE);

function startCapture() {{
    fetch(BASE + 'capture/start', {{method: 'POST'}})
        .then(r => r.json())
        .then(d => {{ if(d.ok) location.reload(); else alert(d.error || 'Failed'); }})
        .catch(e => alert('Request failed: ' + e));
}}
function stopCapture() {{
    fetch(BASE + 'capture/stop', {{method: 'POST'}})
        .then(r => r.json())
        .then(() => location.reload());
}}
function resetCredentials() {{
    if (!confirm('Clear all credentials? You will need to recapture them.')) return;
    fetch(BASE + 'capture/reset', {{method: 'POST'}})
        .then(r => r.json())
        .then(() => location.reload());
}}

// Poll capture status
function pollStatus() {{
    fetch(BASE + 'capture/status')
        .then(r => r.json())
        .then(d => {{
            const cap = d.capture || {{}};
            const phase = cap.phase || 'idle';
            const sec = document.getElementById('capture-section');
            const btnStart = document.getElementById('btn-start');
            const btnStop = document.getElementById('btn-stop');

            if (phase === 'running' || phase === 'complete') {{
                sec.style.display = 'block';
                btnStart.style.display = 'none';
                btnStop.style.display = 'inline-block';
            }} else {{
                sec.style.display = 'none';
                btnStart.style.display = 'inline-block';
                btnStop.style.display = 'none';
            }}
            document.getElementById('cap-phase').textContent = phase;
            if (cap.local_ip) {{
                document.getElementById('phone-url').textContent = 'http://' + cap.local_ip + ':8080';
            }}

            // Auto-reload when capture completes
            if (phase === 'complete' || (d.credentials && d.credentials.complete)) {{
                setTimeout(() => location.reload(), 2000);
            }}
        }})
        .catch(() => {{}});
}}
setInterval(pollStatus, 3000);
pollStatus();
</script>
</body>
</html>'''


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Starting Polyconnect Bridge v2.0.0 on port %d", PORT)

    if _capture_mgr.credentials.is_complete:
        log.info("Credentials loaded — launching Playwright browser")
        try:
            ctrl._launch()
        except Exception as e:
            log.warning("Pre-launch failed: %s — will retry on first request", e)
    else:
        log.warning("Credentials incomplete — open the add-on UI to run capture")

    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=False)
