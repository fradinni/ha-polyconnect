#!/usr/bin/env python3
"""
Polyconnect Bridge Server
=========================
A simple HTTP REST server that wraps the Playwright-based Polyconnect controller.
Run this on a machine that has Playwright installed (Linux/Mac), then point
the Home Assistant integration at it.

Usage:
    pip install flask playwright
    python3 -m playwright install chromium
    python3 polyconnect-server.py

    # Or with a specific port:
    python3 polyconnect-server.py --port 8765

The server exposes:
    GET  /status                → full device state dict
    POST /setpoint              → {"temperature": 28.0}
    POST /mode                  → {"mode": "Chauffage"}   (Chauffage/Froid/Automatique)
    POST /regulation_mode       → {"mode": "Eco"}         (Eco/Smart/Boost)
    POST /on                    → turn heat pump on
    POST /off                   → turn heat pump off
    POST /filtration/start      → start filtration pump
    POST /filtration/stop       → stop filtration pump
    GET  /health                → {"ok": true}

State dict keys:
    waterTemperature        float | null   — pool water temperature (°C)
    outsideTemperature      float | null   — ambient air temperature (°C)
    setpointTemperature     float | null   — target setpoint (°C)
    operatingMode           str   | null   — Chauffage / Froid / Automatique
    regulationMode          str   | null   — Eco / Smart / Boost (or null)
    heatPumpActive          bool           — heat pump on/off state
    compressorRunning       bool           — compressor currently running
    filtrationRunning       bool           — filtration pump running
    alarmActive             bool           — alarm/error present
    alarmMessage            str   | null   — alarm text if present
    cop                     float | null   — coefficient of performance
    powerConsumptionW       float | null   — power draw in Watts
    errorCode               int            — 0 = no error
"""

import argparse
import sys
import threading
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
TOKEN_FILE = SCRIPT_DIR / "captured_token.txt"

BASE_URL = "https://polytropic.user-app.pool.mytech-connect.io"
CF_HEADERS = {
    "CF-Access-Client-Id": "zLT6DV",
    "CF-Access-Client-Secret": "NEEJ9S",
}
DEFAULT_HEAT_PUMP_ID = "64140b25194618718c5083bd"
DEFAULT_INSTALLATION_ID = "64140b25194618718c5083be"

# Modes that live on /heat-pump-edit-mode
MAIN_MODES = {"Chauffage", "Froid", "Automatique"}
# Modes that live on /heat-pump-edit-power-mode
REGULATION_MODES = {"Eco", "Smart", "Boost"}


def load_token():
    if not TOKEN_FILE.exists():
        print(f"Error: token file not found at {TOKEN_FILE}", file=sys.stderr)
        print("Run get-jwt.py first to capture a session token.", file=sys.stderr)
        sys.exit(1)
    token = "\n".join(
        l for l in TOKEN_FILE.read_text().splitlines() if not l.startswith("#")
    ).strip()
    if not token:
        print("Error: token file is empty.", file=sys.stderr)
        sys.exit(1)
    return token


# ─── Comprehensive DOM extraction JS ─────────────────────────────────────────

_STATUS_JS = """
() => {
    // ── helpers ──────────────────────────────────────────────────────────────
    const parseNum = (txt) => {
        if (!txt) return null;
        const n = parseFloat(txt.replace(',', '.').replace(/[^0-9.-]/g, ''));
        return isNaN(n) ? null : n;
    };

    const textOf = (el) => el ? el.textContent.trim() : null;

    // Try a list of selectors, return first non-null numeric value
    const firstNum = (...sels) => {
        for (const sel of sels) {
            for (const el of document.querySelectorAll(sel)) {
                const v = parseNum(textOf(el));
                if (v !== null) return v;
            }
        }
        return null;
    };

    // ── Water temperature ─────────────────────────────────────────────────────
    // The gauge shows 'SET 29 °C -' where '-' = water temp unavailable (no flow)
    // Actual water temp is in .order-and-value-value-number when available
    let waterTemp = null;
    {
        for (const el of document.querySelectorAll(
            '.order-and-value-value-number, [class*="value-number"], ' +
            '[class*="water-temp"], [class*="waterTemp"], ' +
            '.heat-pump-view-temperature-value, .device-summary-temperature'
        )) {
            if (el.offsetParent === null) continue;
            const txt = el.textContent.trim();
            if (txt === '-' || txt === '') continue; // unavailable placeholder
            const v = parseFloat(txt.replace(',', '.').replace(/[^0-9.-]/g, ''));
            if (!isNaN(v) && v > 0 && v < 50) {
                waterTemp = v; break;
            }
        }
    }

    // ── Setpoint temperature ──────────────────────────────────────────────────
    // The round slider order-and-value section shows SET temperature
    const setpointTemp = firstNum(
        '.order-and-value-order-number',
        '.order-and-value-value-number',
        '[class*="setpoint"]',
        '.round-slider-value'
    );

    // ── Outside temperature ───────────────────────────────────────────────────
    // Shown in the topbar as .topbar-weather (e.g. "26.5°C")
    let outsideTemp = null;
    {
        // Primary: topbar weather widget (confirmed working)
        const weather = document.querySelector('.topbar-weather');
        if (weather) {
            const v = parseNum(weather.textContent);
            if (v !== null && v > -30 && v < 60) outsideTemp = v;
        }

        // Secondary: labeled data rows
        if (outsideTemp === null) {
            for (const row of document.querySelectorAll(
                '.order-and-value-item, .device-summary-data-item, ' +
                '[class*="data-item"], [class*="info-item"]'
            )) {
                const txt = row.textContent.toLowerCase();
                if (txt.includes('extérieure') || txt.includes('outside') ||
                    txt.includes('ambiant') || txt.includes('ambient')) {
                    const v = parseNum(row.textContent);
                    if (v !== null && v > -30 && v < 60) { outsideTemp = v; break; }
                }
            }
        }

        // Tertiary: class-name patterns
        if (outsideTemp === null) {
            for (const el of document.querySelectorAll(
                '[class*="outside"], [class*="exterior"], [class*="air-temp"], [class*="outdoor"]'
            )) {
                const v = parseNum(textOf(el));
                if (v !== null && v > -30 && v < 60) { outsideTemp = v; break; }
            }
        }
    }

    // ── Operating mode (Chauffage / Froid / Automatique) ─────────────────────
    const MAIN_MODES = ['Chauffage', 'Froid', 'Automatique'];
    let operatingMode = null;
    {
        // state-button-value shows the selected mode text
        for (const el of document.querySelectorAll('.state-button-value, [class*="mode-label"], [class*="mode-text"]')) {
            const txt = el.textContent.trim();
            if (MAIN_MODES.includes(txt)) { operatingMode = txt; break; }
        }
        // Selected button in mode list
        if (!operatingMode) {
            for (const el of document.querySelectorAll(
                '.heat-pump-view-mode-items [class*="selected"] button, ' +
                '.heat-pump-view-mode-items button[class*="active"], ' +
                '.heat-pump-view-mode-items button[class*="selected"]'
            )) {
                const txt = el.textContent.trim();
                if (MAIN_MODES.includes(txt)) { operatingMode = txt; break; }
            }
        }
        // Icon class fallback
        if (!operatingMode) {
            const iconMap = {
                'heat-pump-mode-heating': 'Chauffage',
                'heat-pump-mode-cooling': 'Froid',
                'heat-pump-mode-auto':    'Automatique',
            };
            for (const [cls, name] of Object.entries(iconMap)) {
                const el = document.querySelector('.' + cls);
                if (el && el.offsetParent !== null && !el.classList.contains('istd-co-hidden')) {
                    operatingMode = name; break;
                }
            }
        }
        // Scan body text as last resort
        if (!operatingMode) {
            const body = document.body.innerText;
            for (const m of MAIN_MODES) {
                if (body.includes(m)) { operatingMode = m; break; }
            }
        }
    }

    // ── Regulation mode (Eco / Smart / Boost) ────────────────────────────────
    const REG_MODES = ['Eco', 'Smart', 'Boost'];
    let regulationMode = null;
    {
        for (const el of document.querySelectorAll('.state-button-value, [class*="power-mode"], [class*="regulation-mode"]')) {
            const txt = el.textContent.trim();
            if (REG_MODES.includes(txt)) { regulationMode = txt; break; }
        }
        if (!regulationMode) {
            const iconMap = {
                'heat-pump-mode-power-smart': 'Smart',
                'heat-pump-mode-boost':       'Boost',
                'heat-pump-mode-eco':         'Eco',
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
            for (const m of REG_MODES) {
                if (body.includes(m)) { regulationMode = m; break; }
            }
        }
    }

    // ── Heat pump on/off state ────────────────────────────────────────────────
    let heatPumpActive = null;
    {
        // The on/off button has aria-pressed or an active class
        const btn = document.querySelector(
            '.heat-pump-on-off button, .co-on-off-button, [class*="on-off"] button'
        );
        if (btn) {
            const pressed = btn.getAttribute('aria-pressed');
            if (pressed !== null) {
                heatPumpActive = pressed === 'true';
            } else {
                heatPumpActive = btn.classList.contains('istd-sty-active') ||
                                 btn.classList.contains('active') ||
                                 btn.classList.contains('on');
            }
        }
        // Fallback: body text ON/OFF indicator
        if (heatPumpActive === null) {
            const body = document.body.innerText;
            if (/\bON\b/.test(body)) heatPumpActive = true;
            else if (/\bOFF\b/.test(body)) heatPumpActive = false;
        }
    }

    // ── Compressor running ────────────────────────────────────────────────────
    let compressorRunning = false;
    {
        const el = document.querySelector(
            '[class*="compressor"], [class*="compresseur"]'
        );
        if (el) {
            compressorRunning = el.classList.contains('running') ||
                                el.classList.contains('active') ||
                                el.classList.contains('on') ||
                                el.getAttribute('data-running') === 'true';
        }
        // Also check body text
        const body = document.body.innerText.toLowerCase();
        if (body.includes('compresseur') && body.includes('en marche')) compressorRunning = true;
    }

    // ── Filtration running ────────────────────────────────────────────────────
    let filtrationRunning = false;
    {
        const el = document.querySelector(
            '[class*="filtration"], [class*="pump-status"], [class*="pompe"]'
        );
        if (el) {
            filtrationRunning = el.classList.contains('running') ||
                                el.classList.contains('active') ||
                                el.classList.contains('on') ||
                                el.getAttribute('data-running') === 'true';
        }
        const body = document.body.innerText.toLowerCase();
        if (body.includes('filtration') && body.includes('on')) filtrationRunning = true;
    }

    // ── Alarm / error ─────────────────────────────────────────────────────────
    let alarmActive = false;
    let alarmMessage = null;
    {
        const banner = document.querySelector('.heat-pump-view-error-clickable');
        if (banner && banner.offsetParent !== null) {
            const txt = banner.innerText.trim();
            if (txt) { alarmActive = true; alarmMessage = txt; }
        }
        if (!alarmActive) {
            for (const el of document.querySelectorAll('[class*="alarm"], [class*="error-msg"], [class*="alert-msg"]')) {
                if (el.offsetParent === null) continue;
                const txt = el.innerText.trim();
                if (txt && !el.classList.contains('device-summary-data-blocked-message')) {
                    alarmActive = true; alarmMessage = txt; break;
                }
            }
        }
    }

    // ── COP / power ───────────────────────────────────────────────────────────
    let cop = null;
    let powerConsumptionW = null;
    {
        for (const row of document.querySelectorAll('.order-and-value-item, [class*="data-item"]')) {
            const txt = row.textContent.toLowerCase();
            const v = parseNum(row.textContent);
            if (v === null) continue;
            if (txt.includes('cop') || txt.includes('performance')) cop = v;
            if (txt.includes('watt') || txt.includes(' w ') || txt.includes('puissance') ||
                txt.includes('power') || txt.includes('consomm')) {
                // Watts are typically > 100 for a heat pump
                if (v > 50) powerConsumptionW = v;
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
        cop:                 cop,
        powerConsumptionW:   powerConsumptionW,
        errorCode:           alarmActive ? 1 : 0,
    };
}
"""


class PolyconnectController:
    """Playwright-based controller (singleton, browser reused across requests)."""

    def __init__(self, token: str, heat_pump_id: str = DEFAULT_HEAT_PUMP_ID):
        self.token = token
        self.heat_pump_id = heat_pump_id
        self._lock = threading.Lock()
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    def _ensure_browser(self):
        if self._browser and self._browser.is_connected():
            return
        self._launch_browser()

    def _launch_browser(self):
        from playwright.sync_api import sync_playwright
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._context = self._browser.new_context(
            extra_http_headers=CF_HEADERS,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
            viewport={"width": 390, "height": 844},
            locale="fr-FR",
        )
        self._context.add_cookies([{
            "name": "affinity",
            "value": "382f2696aa3d7505ba3d20a0b6b549f9|dc028cea65244b463811c834d3033c89",
            "domain": "polytropic.user-app.pool.mytech-connect.io",
            "path": "/",
            "sameSite": "Lax",
        }])
        self._page = self._context.new_page()
        self._load_app()

    def _load_app(self):
        entry_url = f"{BASE_URL}/from-native/{self.token}"
        self._page.goto(entry_url, wait_until="domcontentloaded", timeout=30_000)
        try:
            self._page.wait_for_function(
                "() => typeof Blazor !== 'undefined' && Blazor._internal",
                timeout=20_000,
            )
        except Exception:
            pass
        # Check for auth failure
        body = self._page.evaluate("() => document.body.innerText.substring(0, 200)")
        if "403" in body or "must be connected" in body.lower():
            raise RuntimeError("Session token expired — run get-jwt.py to refresh")
        # Navigate to heat pump view
        self._page.evaluate(f"""
            Blazor._internal.navigationManager.navigateTo(
                "{BASE_URL}/heat-pump-view/{self.heat_pump_id}", false
            )
        """)
        # Wait for heat pump view to fully render before returning
        try:
            self._page.wait_for_selector(
                ".co-gauge-container, .heat-pump-view-mode-container, "
                ".order-and-value-item, .heat-pump-mode",
                timeout=12_000,
            )
        except Exception:
            pass
        time.sleep(3.0)  # Extra wait for all data to populate

    def _ensure_heat_pump_view(self):
        if "heat-pump-view" not in self._page.url:
            self._page.evaluate(f"""
                Blazor._internal.navigationManager.navigateTo(
                    "{BASE_URL}/heat-pump-view/{self.heat_pump_id}", false
                )
            """)
            try:
                self._page.wait_for_selector(
                    ".co-gauge-container, .heat-pump-view-mode-container, "
                    ".order-and-value-item",
                    timeout=10_000,
                )
            except Exception:
                pass
            time.sleep(2.5)  # Allow data to populate

    def get_status(self) -> dict:
        with self._lock:
            self._ensure_browser()
            self._ensure_heat_pump_view()
            # Extra delay to ensure all async data has loaded
            time.sleep(1.5)
            result = self._page.evaluate(_STATUS_JS)
            return result

    def set_setpoint(self, temp: float):
        """Change the temperature setpoint using the round slider."""
        import math
        with self._lock:
            self._ensure_browser()
            self._ensure_heat_pump_view()

            target = int(temp)

            # Get slider geometry and current value
            info_js = self._page.evaluate("""
            (() => {
                const gauge = document.getElementById('heat-pump-temperature-gauge-gauge');
                const handle = document.querySelector('#heat-pump-temperature-gauge-gauge .rs-handle');
                const orderNum = document.querySelector('.order-and-value-order-number');
                if (!gauge || !handle) return null;
                const gr = gauge.getBoundingClientRect();
                const hr = handle.getBoundingClientRect();
                const rs = typeof jQuery !== 'undefined' &&
                           jQuery('#heat-pump-temperature-gauge-gauge').data('roundSlider');
                return {
                    gcx: gr.x + gr.width / 2,
                    gcy: gr.y + gr.height / 2,
                    hx:  hr.x + hr.width / 2,
                    hy:  hr.y + hr.height / 2,
                    current: rs ? rs.getValue() : parseInt(orderNum?.textContent || '29'),
                    min: rs ? rs.options.min : 8,
                    max: rs ? rs.options.max : 32,
                };
            })()
            """)

            if not info_js:
                print("[server] Could not locate slider — skipping setpoint change", flush=True)
                return

            current = info_js['current']
            diff = current - target
            if diff == 0:
                return

            gcx, gcy = info_js['gcx'], info_js['gcy']
            hx, hy   = info_js['hx'],  info_js['hy']
            dx = hx - gcx; dy = hy - gcy
            angle = math.atan2(dy, dx)
            r = math.sqrt(dx**2 + dy**2)
            total_range = info_js['max'] - info_js['min']
            radians_per_step = (270 / total_range) * (math.pi / 180)
            px_per_step = r * radians_per_step
            tx = math.sin(angle); ty = -math.cos(angle)
            total_move = diff * px_per_step

            self._page.mouse.move(hx, hy)
            self._page.mouse.down()
            time.sleep(0.05)
            steps = max(20, abs(diff) * 5)
            for i in range(steps + 1):
                frac = i / steps
                self._page.mouse.move(
                    hx + tx * total_move * frac,
                    hy + ty * total_move * frac,
                )
                time.sleep(0.015)
            self._page.mouse.up()
            time.sleep(0.5)

            # Click validate button
            try:
                self._page.click(".order-validation-validate", timeout=3000)
                time.sleep(2)
            except Exception:
                self._page.evaluate("""
                (() => {
                    const b = document.querySelector('.order-validation-validate');
                    if (b) b.click();
                })()
                """)
                time.sleep(2)

    def set_mode(self, mode: str):
        """Switch main operating mode (Chauffage / Froid / Automatique)."""
        with self._lock:
            self._ensure_browser()
            if mode in REGULATION_MODES:
                edit_url = f"{BASE_URL}/heat-pump-edit-power-mode/{self.heat_pump_id}"
            else:
                edit_url = f"{BASE_URL}/heat-pump-edit-mode/{self.heat_pump_id}"

            self._page.evaluate(f"""
                Blazor._internal.navigationManager.navigateTo(
                    "{edit_url}", false
                )
            """)
            try:
                self._page.wait_for_selector("button, .control-selector", timeout=8_000)
            except Exception:
                time.sleep(3)
            time.sleep(1.0)

            # Click the mode button
            clicked = False
            try:
                self._page.click(f"button:has-text('{mode}')", timeout=3000)
                clicked = True
            except Exception:
                pass

            if not clicked:
                self._page.evaluate(f"""(() => {{
                    for (const el of document.querySelectorAll(
                        'button, [class*="control-selector"], [class*="button-radio"]'
                    )) {{
                        if (el.textContent.trim() === '{mode}' ||
                            el.textContent.includes('{mode}')) {{
                            el.click(); return;
                        }}
                    }}
                }})()""")

            time.sleep(1)

            # Click Valider if present
            try:
                self._page.click("button:has-text('Valider')", timeout=2000)
            except Exception:
                pass

            time.sleep(2)

            # Navigate back to heat pump view
            self._page.evaluate(f"""
                Blazor._internal.navigationManager.navigateTo(
                    "{BASE_URL}/heat-pump-view/{self.heat_pump_id}", false
                )
            """)
            time.sleep(3)

    def _get_active_state(self) -> bool | None:
        """Read current on/off state from DOM."""
        return self._page.evaluate("""
        (() => {
            const btn = document.querySelector(
                '.heat-pump-on-off button, .co-on-off-button, [class*="on-off"] button'
            );
            if (btn) {
                const pressed = btn.getAttribute('aria-pressed');
                if (pressed !== null) return pressed === 'true';
                return btn.classList.contains('istd-sty-active') ||
                       btn.classList.contains('active');
            }
            const body = document.body.innerText;
            if (/\bON\b/.test(body)) return true;
            if (/\bOFF\b/.test(body)) return false;
            return null;
        })()
        """)

    def turn_on(self):
        """Turn the heat pump on (only clicks if currently off)."""
        with self._lock:
            self._ensure_browser()
            self._ensure_heat_pump_view()
            is_on = self._get_active_state()
            if is_on is True:
                print("[server] Heat pump already ON — no action", flush=True)
                return
            self._click_power_button()

    def turn_off(self):
        """Turn the heat pump off (only clicks if currently on)."""
        with self._lock:
            self._ensure_browser()
            self._ensure_heat_pump_view()
            is_on = self._get_active_state()
            if is_on is False:
                print("[server] Heat pump already OFF — no action", flush=True)
                return
            self._click_power_button()

    def _click_power_button(self):
        """Click the on/off toggle button."""
        clicked = False
        for selector in [
            ".heat-pump-on-off button",
            ".co-on-off-button",
            "button.co-on-off-button",
            "[class*='on-off'] button",
        ]:
            try:
                self._page.click(selector, timeout=3000)
                clicked = True
                break
            except Exception:
                pass
        if not clicked:
            self._page.evaluate("""
            (() => {
                const btn = document.querySelector(
                    '.heat-pump-on-off button, .co-on-off-button'
                );
                if (btn) btn.click();
            })()
            """)
        time.sleep(2)

    def _get_filtration_state(self) -> bool | None:
        """Read current filtration running state from DOM."""
        return self._page.evaluate("""
        (() => {
            const el = document.querySelector(
                '[class*="filtration"] button, [class*="filtration"][class*="toggle"], ' +
                '[class*="pump"] button'
            );
            if (el) {
                const pressed = el.getAttribute('aria-pressed');
                if (pressed !== null) return pressed === 'true';
                return el.classList.contains('istd-sty-active') ||
                       el.classList.contains('active') ||
                       el.classList.contains('on');
            }
            return null;
        })()
        """)

    def start_filtration(self):
        """Start filtration pump (only if not already running)."""
        with self._lock:
            self._ensure_browser()
            self._ensure_heat_pump_view()
            is_running = self._get_filtration_state()
            if is_running is True:
                print("[server] Filtration already running — no action", flush=True)
                return
            self._click_filtration_button()

    def stop_filtration(self):
        """Stop filtration pump (only if currently running)."""
        with self._lock:
            self._ensure_browser()
            self._ensure_heat_pump_view()
            is_running = self._get_filtration_state()
            if is_running is False:
                print("[server] Filtration already stopped — no action", flush=True)
                return
            self._click_filtration_button()

    def _click_filtration_button(self):
        """Click the filtration toggle button."""
        clicked = False
        for selector in [
            "[class*='filtration'] button",
            "[class*='filtration'][class*='toggle']",
            "[class*='pump-toggle']",
        ]:
            try:
                self._page.click(selector, timeout=3000)
                clicked = True
                break
            except Exception:
                pass
        if not clicked:
            self._page.evaluate("""
            (() => {
                const btn = document.querySelector(
                    '[class*="filtration"] button, [class*="filtration"][class*="toggle"]'
                );
                if (btn) btn.click();
            })()
            """)
        time.sleep(2)

    def close(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()


def create_app(controller: PolyconnectController):
    from flask import Flask, jsonify, request, abort
    app = Flask(__name__)

    @app.route("/health")
    def health():
        return jsonify({"ok": True, "service": "polyconnect-bridge"})

    @app.route("/status")
    def status():
        try:
            data = controller.get_status()
            return jsonify(data)
        except RuntimeError as e:
            return jsonify({"error": str(e), "auth_expired": True}), 401
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500

    @app.route("/setpoint", methods=["POST"])
    def setpoint():
        data = request.get_json(force=True, silent=True) or {}
        temp = data.get("temperature")
        if temp is None:
            abort(400, "missing temperature")
        try:
            controller.set_setpoint(float(temp))
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/mode", methods=["POST"])
    def mode():
        data = request.get_json(force=True, silent=True) or {}
        m = data.get("mode")
        if not m:
            abort(400, "missing mode")
        try:
            controller.set_mode(m)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # Alias: regulation modes (Eco/Smart/Boost) go through the same set_mode
    @app.route("/regulation_mode", methods=["POST"])
    def regulation_mode():
        data = request.get_json(force=True, silent=True) or {}
        m = data.get("mode")
        if not m:
            abort(400, "missing mode")
        try:
            controller.set_mode(m)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/on", methods=["POST"])
    def turn_on():
        try:
            controller.turn_on()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/off", methods=["POST"])
    def turn_off():
        try:
            controller.turn_off()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/filtration/start", methods=["POST"])
    def filtration_start():
        try:
            controller.start_filtration()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/filtration/stop", methods=["POST"])
    def filtration_stop():
        try:
            controller.stop_filtration()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app


def main():
    parser = argparse.ArgumentParser(description="Polyconnect Bridge Server")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()

    token = load_token()
    controller = PolyconnectController(token=token)
    print(f"Starting Polyconnect Bridge Server on {args.host}:{args.port}")
    print(f"Token loaded from {TOKEN_FILE}")
    print("Endpoints:")
    print("  GET  /status")
    print("  POST /setpoint   {temperature: float}")
    print("  POST /mode       {mode: Chauffage|Froid|Automatique|Eco|Smart|Boost}")
    print("  POST /on  /off")
    print("  POST /filtration/start  /filtration/stop")
    print(f"HA bridge_url: http://<this-machine-ip>:{args.port}")

    app = create_app(controller)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
