#!/usr/bin/env python3
"""Polyconnect Bridge Server — HA Add-on v1.1.5

Single persistent Chromium instance reused across all requests.
Mode/preset changes take ~2-3 s. Setpoint uses mouse-drag (robust).
Thread safety: a global Lock serialises all Playwright operations.
If the browser crashes it is relaunched transparently on the next call.
"""
import math, os, logging, threading, time, json, sys
from flask import Flask, jsonify, request

TOKEN     = os.environ.get("POLYCONNECT_TOKEN", "")
HEAT_PUMP          = os.environ.get("POLYCONNECT_HEAT_PUMP_ID", "64140b25194618718c5083bd")
INSTALLATION_ID    = os.environ.get("POLYCONNECT_INSTALLATION_ID",
                        HEAT_PUMP[:-1] + chr(ord(HEAT_PUMP[-1]) + 1))
LOG_LEVEL = os.environ.get("POLYCONNECT_LOG_LEVEL", "info").upper()
PORT      = int(os.environ.get("PORT", 8765))

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

    // Water temperature — .order-and-value-value-number when not '-'
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

    // Outside temperature — topbar-weather widget (primary)
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

    // Operating mode (main: Chauffage / Froid / Automatique)
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

    // Regulation mode (preset: Eco / Smart / Boost)
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

    // Heat pump on/off state
    let heatPumpActive = null;
    const btn = document.querySelector('.heat-pump-on-off button, .co-on-off-button, [class*="on-off"] button');
    if (btn) {
        const pressed = btn.getAttribute('aria-pressed');
        if (pressed !== null) heatPumpActive = pressed === 'true';
        else heatPumpActive = btn.classList.contains('istd-sty-active') || btn.classList.contains('active');
    }
    if (heatPumpActive === null) {
        const body = document.body.innerText;
        if (/\bON\b/.test(body)) heatPumpActive = true;
        else if (/\bOFF\b/.test(body)) heatPumpActive = false;
    }

    // Compressor
    let compressorRunning = false;
    const compEl = document.querySelector('[class*="compressor"], [class*="compresseur"]');
    if (compEl) compressorRunning = compEl.classList.contains('running') || compEl.classList.contains('active');
    if (!compressorRunning) {
        const body = document.body.innerText.toLowerCase();
        if (body.includes('compresseur') && body.includes('en marche')) compressorRunning = true;
    }

    // Filtration — scan multiple possible selectors
    let filtrationRunning = false;
    const filtSelectors = [
        '[class*="filtration"] button',
        '[class*="filtration"][class*="toggle"]',
        '[class*="filtration"][class*="btn"]',
        '[class*="pompe-filtration"]',
        '[class*="pump-status"]',
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
        page = self._page
        page.goto(f"{BASE}/from-native/{TOKEN}", wait_until="domcontentloaded", timeout=30_000)
        try:
            page.wait_for_function(
                "() => typeof Blazor !== 'undefined' && Blazor._internal",
                timeout=20_000)
        except Exception:
            pass
        body = page.evaluate("() => document.body.innerText.substring(0, 200)")
        if "403" in body or "must be connected" in body.lower():
            raise RuntimeError("Session token expired — update it in the add-on options")
        page.evaluate(
            f"Blazor._internal.navigationManager.navigateTo("
            f"'{BASE}/heat-pump-view/{HEAT_PUMP}', false)")
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
        """Launch or re-launch the browser if necessary."""
        if self._browser and self._browser.is_connected():
            return
        log.info("(Re-)launching browser …")
        self._launch()

    def _ensure_view(self):
        """Navigate back to heat pump view; detect 403 token expiry.

        The Blazor SPA keeps the original URL even after auth failure, so we must
        inspect the page body — not just the URL — to detect expiry. On 403 we
        close the browser (so the next _ensure() re-launches with a fresh token)
        and raise RuntimeError so _safe() returns 401 to HA.
        """
        # 403 check — runs every request, inexpensive (just reads innerText)
        try:
            snippet = self._page.evaluate(
                "() => document.body.innerText.substring(0, 120)")
            if "403" in snippet or "must be connected" in snippet.lower():
                log.warning("Session token expired — closing browser for re-launch")
                try:
                    self._browser.close()
                    self._pw.stop()
                except Exception:
                    pass
                self._browser = None
                self._pw      = None
                self._page    = None
                raise RuntimeError(
                    "Session token expired — update it in the add-on options")
        except RuntimeError:
            raise
        except Exception:
            pass  # evaluate may fail transiently; treat as recoverable

        if "heat-pump-view" not in self._page.url:
            self._page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{BASE}/heat-pump-view/{HEAT_PUMP}', false)")
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
            page = self._page
            edit_url = (f"{BASE}/heat-pump-edit-mode/{HEAT_PUMP}"
                        if mode in MAIN_MODES
                        else f"{BASE}/heat-pump-edit-power-mode/{HEAT_PUMP}")

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
                log.info("Mode button confirmed selected: %s", mode)
            except Exception:
                time.sleep(0.8)

            try:
                page.wait_for_selector("button:has-text('Valider')", timeout=1_500)
                page.click("button:has-text('Valider')", timeout=1_500)
                log.info("Clicked Valider")
            except Exception:
                pass

            log.info("Navigating back to heat pump view")
            page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{BASE}/heat-pump-view/{HEAT_PUMP}',"
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
                    log.info("Mode icon %s confirmed in DOM", expected)
                except Exception:
                    time.sleep(1.5)
            else:
                time.sleep(2.0)
            return {"ok": True}

    # ── set_setpoint (mouse-drag — proven reliable) ───────────────────────────

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
                log.warning("Temperature slider not found in DOM (alarm state or page not loaded)")
                return {"ok": True, "note": "slider not found in DOM"}

            current = info["current"]
            diff    = current - target
            log.info("Setpoint: current=%s target=%s diff=%s", current, target, diff)

            if diff == 0:
                log.info("Setpoint already at %s — no action", target)
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
                log.info("Setpoint validate JS fallback: %s°C", temp)
            time.sleep(1.0)
            return {"ok": True}

    # ── power on/off ──────────────────────────────────────────────────────────

    def _get_active(self) -> bool | None:
        return self._page.evaluate("""() => {
            // Primary: CSS class selectors (standard Polyconnect layout)
            const btn = document.querySelector(
                '.heat-pump-on-off button, .co-on-off-button, [class*="on-off"] button');
            if (btn) {
                const p = btn.getAttribute('aria-pressed');
                if (p !== null) return p === 'true';
                return btn.classList.contains('istd-sty-active') || btn.classList.contains('active');
            }
            // Fallback: find button whose visible text is exactly "ON" or "OFF"
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
        # Primary: CSS class selectors
        for sel in [".heat-pump-on-off button", ".co-on-off-button", "[class*='on-off'] button"]:
            try:
                page.click(sel, timeout=3_000)
                log.info("Power clicked via CSS: %s", sel)
                return
            except Exception:
                pass
        # Fallback: find button by visible text "ON" / "OFF" (Polyconnect uses plain text labels)
        result = page.evaluate("""() => {
            for (const b of document.querySelectorAll('button')) {
                const t = b.textContent.trim().toUpperCase();
                if (t === 'ON' || t === 'OFF') {
                    b.click();
                    return 'text:' + b.textContent.trim().substring(0, 8);
                }
            }
            // Broader: any power/switch-related element
            for (const b of document.querySelectorAll('[class*="power"] button, [class*="switch"] button')) {
                b.click();
                return 'class:' + b.className.substring(0, 40);
            }
            return null;
        }""")
        if result:
            log.info("Power clicked via JS fallback: %s", result)
        else:
            log.warning("Power button not found in DOM — check CSS selectors")

    def turn_on(self) -> dict:
        with self._lock:
            self._ensure(); self._ensure_view()
            is_on = self._get_active()
            log.info("turn_on: current active=%s", is_on)
            if is_on is True:
                return {"ok": True, "note": "already ON"}
            self._click_power()
            time.sleep(2.0)
            return {"ok": True}

    def turn_off(self) -> dict:
        with self._lock:
            self._ensure(); self._ensure_view()
            is_on = self._get_active()
            log.info("turn_off: current active=%s", is_on)
            if is_on is False:
                return {"ok": True, "note": "already OFF"}
            self._click_power()
            time.sleep(2.0)
            return {"ok": True}

    # ── filtration ────────────────────────────────────────────────────────────
    # Filtration may be on heat-pump-view OR installation-overview page.
    # We scan both and log whatever we find to identify the correct selector.

    def _scan_filtration_buttons(self) -> dict:
        """Scan current page for filtration controls. Returns {found, sel, state, buttons}."""
        return self._page.evaluate("""() => {
            const SELS = [
                '[class*="filtration"] button',
                'button[class*="filtration"]',
                '[class*="filtration"][class*="toggle"]',
                '[class*="pompe-filtration"]',
                '[id*="filtration"]',
                '[class*="filtration"][class*="switch"]',
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
            // Broad: any button with filtration/pompe text
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
            // Not found — list all button texts for debugging
            const btns = Array.from(document.querySelectorAll('button'))
                .map(b => b.textContent.trim().substring(0,30)).filter(t => t).slice(0,20);
            return {found: false, buttons: btns};
        }""")

    def _click_found_filtration(self, scan_result: dict):
        """Click a filtration button from a scan result."""
        page = self._page
        sel = scan_result.get('sel', '')
        if sel == 'broad':
            text = scan_result.get('text', '')
            page.evaluate(
                "(t => { for (const b of document.querySelectorAll('button')) {"
                "  if (b.textContent.trim().startsWith(t.substring(0,15))) { b.click(); return; }"
                "} })(" + repr(text) + ")")
            log.info("Filtration broad-click: text=%r", text)
        else:
            try:
                page.click(sel, timeout=3_000)
                log.info("Filtration clicked: %s", sel)
            except Exception:
                page.evaluate("(s => { const b = document.querySelector(s); if (b) b.click(); })(" + repr(sel) + ")")
                log.info("Filtration JS-click: %s", sel)

    def _toggle_filtration(self, want_on: bool) -> dict:
        page = self._page

        # 1. Try heat pump view (already loaded)
        result = self._scan_filtration_buttons()
        log.info("Filtration scan heat-pump-view: %s", result)
        if result.get('found'):
            is_on = result.get('state', False)
            if (want_on and is_on) or (not want_on and not is_on):
                return {"ok": True, "note": f"already {'running' if want_on else 'stopped'}"}
            self._click_found_filtration(result)
            time.sleep(2.0)
            after = self._scan_filtration_buttons()
            log.info("Filtration after click: %s", after)
            return {"ok": True}

        # 2. Try installation overview
        log.info("Filtration not on heat-pump-view → trying installation-overview (%s)", INSTALLATION_ID)
        page.evaluate(
            f"Blazor._internal.navigationManager.navigateTo("
            f"'{BASE}/installation-overview/{INSTALLATION_ID}', false)")
        try:
            page.wait_for_selector('[class*="filtration"], [class*="pompe"], button', timeout=10_000)
        except Exception:
            time.sleep(3)
        result2 = self._scan_filtration_buttons()
        log.info("Filtration scan installation-overview: %s", result2)
        if result2.get('found'):
            is_on = result2.get('state', False)
            if not ((want_on and is_on) or (not want_on and not is_on)):
                self._click_found_filtration(result2)
                time.sleep(2.0)
            # Navigate back
            page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{BASE}/heat-pump-view/{HEAT_PUMP}', false)")
            try:
                page.wait_for_selector(".co-gauge-container, .heat-pump-view-mode-container", timeout=8_000)
            except Exception:
                time.sleep(2)
            return {"ok": True}
        else:
            log.warning("Filtration button NOT found anywhere. Page buttons: %s", result2.get('buttons', []))
            # Navigate back regardless
            page.evaluate(
                f"Blazor._internal.navigationManager.navigateTo("
                f"'{BASE}/heat-pump-view/{HEAT_PUMP}', false)")
            return {"ok": True, "note": "filtration button not found in DOM — check add-on logs"}

    def start_filtration(self) -> dict:
        with self._lock:
            self._ensure()
            self._ensure_view()
            return self._toggle_filtration(want_on=True)

    def stop_filtration(self) -> dict:
        with self._lock:
            self._ensure()
            self._ensure_view()
            return self._toggle_filtration(want_on=False)



    def debug_power_dom(self) -> dict:
        """Non-destructive DOM scan of the power button area."""
        with self._lock:
            self._ensure()
            self._ensure_view()
            return self._page.evaluate("""() => {
                const CSS_SELS = [
                    '.heat-pump-on-off button', '.co-on-off-button',
                    '[class*="on-off"] button', '[class*="power"] button',
                    '[class*="switch"] button',
                ];
                const cssMatches = [];
                for (const sel of CSS_SELS) {
                    const el = document.querySelector(sel);
                    if (el) cssMatches.push({sel, tag: el.tagName,
                        cls: el.className.substring(0,80),
                        text: el.textContent.trim().substring(0,30),
                        ariaPressed: el.getAttribute('aria-pressed')});
                }
                const allBtns = Array.from(document.querySelectorAll('button')).map(b => ({
                    textRaw: JSON.stringify(b.textContent.substring(0, 40)),
                    textTrimmed: b.textContent.trim().substring(0, 30),
                    cls: b.className.substring(0, 80),
                    ariaPressed: b.getAttribute('aria-pressed'),
                    id: b.id || null,
                    visible: b.offsetParent !== null,
                }));
                let textActive = null;
                for (const b of document.querySelectorAll('button')) {
                    const t = b.textContent.trim().toUpperCase();
                    if (t === 'ON' || t === 'OFF') {
                        const p = b.getAttribute('aria-pressed');
                        textActive = {found: true, text: t,
                            ariaPressed: p, cls: b.className.substring(0, 80),
                            state: p !== null ? p === 'true' : t === 'ON'};
                        break;
                    }
                }
                return {
                    url: window.location.href.split('/').slice(-2).join('/'),
                    cssMatches, allBtns,
                    textActive: textActive || {found: false},
                    bodySnippet: document.body.innerText.substring(0, 300),
                };
            }""")


# ── Flask app ─────────────────────────────────────────────────────────────────

ctrl = PolyconnectController()
app  = Flask(__name__)


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw), 200
    except RuntimeError as e:
        if "expired" in str(e).lower():
            return {"error": str(e), "auth_expired": True}, 401
        return {"error": str(e)}, 500
    except Exception as e:
        log.exception("Unhandled error in %s", fn.__name__)
        return {"error": str(e)}, 500


@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "polyconnect-bridge",
                    "version": "1.1.8", "token_configured": bool(TOKEN)})


@app.route("/status")
def status():
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

@app.route("/debug/power")
def debug_power():
    data, code = _safe(ctrl.debug_power_dom)
    return jsonify(data), code


if __name__ == "__main__":
    log.info("Starting Polyconnect Bridge v1.1.8 on port %d", PORT)
    if not TOKEN:
        log.warning("No token configured — set it in the add-on options.")
    if TOKEN:
        try:
            ctrl._launch()
        except Exception as e:
            log.warning("Pre-launch failed: %s — will retry on first request", e)
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=False)
