"""Phone-facing setup UI — serves the capture landing page on port 8080.

This lightweight HTTP server is only active during capture mode.
It serves:
  /         → step-by-step instructions with live status
  /cert/pem → mitmproxy CA certificate download
  /api/status → JSON capture status (polled by the page)

No authentication — the phone needs to reach this directly on the LAN.
"""
from __future__ import annotations

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from capture_manager import CaptureManager, CERT_PEM, PROXY_PORT, SETUP_PORT

# ── Setup Web Server ──────────────────────────────────────────────────────────

_manager: CaptureManager | None = None


class SetupHandler(BaseHTTPRequestHandler):
    """HTTP handler for the phone-facing setup page."""

    def log_message(self, format, *args):
        pass  # suppress access logs

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "":
            self._serve_landing()
        elif path == "/api/status":
            self._serve_status()
        elif path == "/cert/pem":
            self._serve_cert()
        else:
            self.send_error(404)

    def _serve_landing(self):
        status = _manager.get_status() if _manager else {}
        local_ip = status.get("capture", {}).get("local_ip", "0.0.0.0")
        html = _build_landing_html(local_ip)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_status(self):
        status = _manager.get_status() if _manager else {"capture": {}, "credentials": {}}
        data = json.dumps(status).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _serve_cert(self):
        if not CERT_PEM.exists():
            self.send_error(404, "Certificate not ready. Try again in a moment.")
            return
        data = CERT_PEM.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-pem-file")
        self.send_header("Content-Disposition", "attachment; filename=mitmproxy-ca-cert.pem")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_setup_server(manager: CaptureManager) -> HTTPServer:
    """Start the phone-facing setup server on port 8080."""
    global _manager
    _manager = manager

    server = HTTPServer(("0.0.0.0", SETUP_PORT), SetupHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="setup-ui")
    thread.start()
    return server


def stop_setup_server(server: HTTPServer) -> None:
    """Shutdown the setup server."""
    server.shutdown()


# ── Landing Page HTML ─────────────────────────────────────────────────────────

def _build_landing_html(local_ip: str) -> str:
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polyconnect Setup</title>
<style>
:root {{
    --bg: #0f172a;
    --surface: #1e293b;
    --surface-2: #334155;
    --border: #475569;
    --text: #f1f5f9;
    --text-dim: #94a3b8;
    --accent: #38bdf8;
    --green: #4ade80;
    --yellow: #fbbf24;
    --red: #f87171;
    --purple: #a78bfa;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro', 'Segoe UI', system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 1rem;
    padding-bottom: 4rem;
}}
.container {{ max-width: 680px; margin: 0 auto; }}
header {{
    text-align: center;
    padding: 1.5rem 0 1.2rem;
}}
header h1 {{
    font-size: 1.6rem;
    font-weight: 700;
    background: linear-gradient(135deg, var(--accent), var(--purple));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}}
header p {{ color: var(--text-dim); margin-top: 0.3rem; font-size: 0.85rem; }}

/* Status card */
.status-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.2rem;
    margin-bottom: 1rem;
}}
.status-card h2 {{
    font-size: 0.95rem;
    color: var(--accent);
    margin-bottom: 0.8rem;
    display: flex; align-items: center; gap: 0.5rem;
}}
.status-row {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--surface-2);
}}
.status-row:last-child {{ border-bottom: none; }}
.status-label {{ color: var(--text-dim); font-size: 0.82rem; }}
.badge {{
    display: inline-flex; align-items: center; gap: 0.3rem;
    padding: 0.2rem 0.6rem; border-radius: 20px;
    font-size: 0.72rem; font-weight: 600;
}}
.badge-ok {{ background: rgba(74,222,128,0.15); color: var(--green); }}
.badge-wait {{ background: rgba(251,191,36,0.15); color: var(--yellow); }}
.dot {{ width: 6px; height: 6px; border-radius: 50%; display: inline-block; }}
.dot-green {{ background: var(--green); }}
.dot-yellow {{ background: var(--yellow); animation: pulse 2s infinite; }}
@keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}

/* Success banner */
.success-banner {{
    display: none;
    background: rgba(74,222,128,0.1);
    border: 1px solid var(--green);
    border-radius: 12px;
    padding: 1.2rem;
    margin-bottom: 1rem;
    text-align: center;
}}
.success-banner h2 {{ color: var(--green); font-size: 1.1rem; margin-bottom: 0.5rem; }}
.success-banner p {{ color: var(--text-dim); font-size: 0.85rem; line-height: 1.5; }}

/* Proxy info */
.proxy-info {{
    background: var(--bg);
    border: 1px solid var(--accent);
    border-radius: 8px;
    padding: 1rem; margin: 1rem 0;
    text-align: center;
}}
.proxy-info .label {{ font-size: 0.72rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; }}
.proxy-info .value {{ font-size: 1.3rem; font-weight: 700; color: var(--accent); font-family: monospace; }}

/* Tabs */
.tabs {{ display: flex; gap: 0; margin-bottom: 0; }}
.tab {{
    flex: 1; padding: 0.65rem; text-align: center;
    font-size: 0.82rem; font-weight: 600; cursor: pointer;
    background: var(--surface-2); border: 1px solid var(--border); color: var(--text-dim);
    transition: all 0.2s;
}}
.tab:first-child {{ border-radius: 12px 0 0 0; }}
.tab:last-child {{ border-radius: 0 12px 0 0; }}
.tab.active {{ background: var(--surface); color: var(--accent); border-bottom-color: var(--surface); }}
.tab-content {{
    background: var(--surface); border: 1px solid var(--border); border-top: none;
    border-radius: 0 0 12px 12px; padding: 1.2rem; margin-bottom: 1rem;
}}
.tab-pane {{ display: none; }}
.tab-pane.active {{ display: block; }}

/* Steps */
.step {{ display: flex; gap: 0.7rem; margin-bottom: 1.1rem; }}
.step-num {{
    flex-shrink: 0; width: 24px; height: 24px;
    background: var(--accent); color: var(--bg); border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.7rem; font-weight: 700;
}}
.step-content h3 {{ font-size: 0.85rem; margin-bottom: 0.2rem; }}
.step-content p {{ font-size: 0.8rem; color: var(--text-dim); line-height: 1.4; }}
.code-block {{
    background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 0.6rem 0.8rem; font-family: 'SF Mono', monospace;
    font-size: 0.78rem; margin: 0.4rem 0; color: var(--green);
}}
.btn {{
    display: inline-flex; align-items: center; gap: 0.4rem;
    padding: 0.55rem 1rem; border-radius: 8px;
    font-size: 0.82rem; font-weight: 600; text-decoration: none;
    border: none; cursor: pointer; transition: all 0.2s;
}}
.btn-primary {{ background: var(--accent); color: var(--bg); }}
.btn-primary:hover {{ filter: brightness(1.1); transform: translateY(-1px); }}
.btn-outline {{ background: transparent; border: 1px solid var(--border); color: var(--text); }}

/* Warning card */
.warn-card {{
    background: var(--surface); border: 1px solid var(--yellow);
    border-radius: 12px; padding: 1rem; margin-bottom: 1rem;
}}
.warn-card h3 {{ color: var(--yellow); font-size: 0.85rem; margin-bottom: 0.4rem; }}
.warn-card p {{ color: var(--text-dim); font-size: 0.8rem; line-height: 1.4; }}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>Polyconnect Setup</h1>
        <p>Capturing credentials for your heat pump</p>
    </header>

    <!-- Success Banner (shown when all captured) -->
    <div class="success-banner" id="success-banner">
        <h2>Setup Complete!</h2>
        <p>All credentials have been captured successfully.<br>
        You can now <strong>remove the proxy settings</strong> from your phone's WiFi
        and close this page.</p>
        <p style="margin-top:0.8rem; font-size:0.78rem; color:var(--text-dim);">
        The capture server will stop automatically in a few seconds.</p>
    </div>

    <!-- Live Status -->
    <div class="status-card" id="status-card">
        <h2><span class="dot dot-yellow" id="status-dot"></span> Capture Progress</h2>
        <div class="status-row">
            <span class="status-label">Session Token</span>
            <span id="st-token" class="badge badge-wait">Waiting...</span>
        </div>
        <div class="status-row">
            <span class="status-label">Installation ID</span>
            <span id="st-installation" class="badge badge-wait">Waiting...</span>
        </div>
        <div class="status-row">
            <span class="status-label">Heat Pump ID</span>
            <span id="st-heatpump" class="badge badge-wait">Waiting...</span>
        </div>
    </div>

    <!-- Proxy config -->
    <div class="proxy-info">
        <div class="label">Configure your phone's WiFi proxy to:</div>
        <div class="value"><span id="proxy-host">{local_ip}</span>:{PROXY_PORT}</div>
    </div>

    <!-- Platform Tabs -->
    <div class="tabs">
        <div class="tab active" onclick="switchTab('iphone')">iPhone</div>
        <div class="tab" onclick="switchTab('android')">Android</div>
    </div>
    <div class="tab-content">
        <!-- iPhone -->
        <div class="tab-pane active" id="pane-iphone">
            <div class="step">
                <div class="step-num">1</div>
                <div class="step-content">
                    <h3>Download the security certificate</h3>
                    <p>Tap the button below <strong>using Safari</strong> (not Chrome).</p>
                    <a href="/cert/pem" class="btn btn-primary" style="margin-top:0.4rem;">Download Certificate</a>
                    <p style="margin-top:0.3rem;">When prompted, tap <strong>Allow</strong>.</p>
                </div>
            </div>
            <div class="step">
                <div class="step-num">2</div>
                <div class="step-content">
                    <h3>Install the certificate</h3>
                    <p>Go to <strong>Settings &rarr; General &rarr; VPN & Device Management</strong><br>
                    Tap the <em>mitmproxy</em> profile &rarr; <strong>Install</strong> &rarr; Enter passcode &rarr; <strong>Install</strong></p>
                </div>
            </div>
            <div class="step">
                <div class="step-num">3</div>
                <div class="step-content">
                    <h3>Trust the certificate</h3>
                    <p>Go to <strong>Settings &rarr; General &rarr; About &rarr; Certificate Trust Settings</strong><br>
                    Toggle ON for <em>mitmproxy</em> &rarr; tap <strong>Continue</strong></p>
                </div>
            </div>
            <div class="step">
                <div class="step-num">4</div>
                <div class="step-content">
                    <h3>Set up the WiFi proxy</h3>
                    <p>Go to <strong>Settings &rarr; WiFi &rarr; tap (i) on your network</strong><br>
                    Scroll down &rarr; <strong>HTTP Proxy &rarr; Manual</strong></p>
                    <div class="code-block">Server: <span class="proxy-host-val"></span><br>Port: {PROXY_PORT}<br>Authentication: Off</div>
                    <p>Tap <strong>Save</strong>.</p>
                </div>
            </div>
            <div class="step">
                <div class="step-num">5</div>
                <div class="step-content">
                    <h3>Open the Polyconnect app</h3>
                    <p>Open the app, log in, then <strong>tap on your heat pump</strong> to select it.</p>
                    <p style="margin-top:0.3rem;">Watch the status above &mdash; it updates live as each piece is captured.</p>
                </div>
            </div>
        </div>

        <!-- Android -->
        <div class="tab-pane" id="pane-android">
            <div class="step">
                <div class="step-num">1</div>
                <div class="step-content">
                    <h3>Download the certificate</h3>
                    <p>Tap the button below from your phone's browser.</p>
                    <a href="/cert/pem" class="btn btn-primary" style="margin-top:0.4rem;">Download Certificate</a>
                </div>
            </div>
            <div class="step">
                <div class="step-num">2</div>
                <div class="step-content">
                    <h3>Install the certificate</h3>
                    <p><strong>Settings &rarr; Security &rarr; Encryption & credentials &rarr; Install a certificate &rarr; CA certificate</strong></p>
                    <p>Select the downloaded file and confirm with <strong>Install anyway</strong>.</p>
                    <p style="margin-top:0.3rem;"><em>Samsung:</em> Settings &rarr; Biometrics and Security &rarr; Other security settings &rarr; Install from device storage &rarr; CA certificate</p>
                </div>
            </div>
            <div class="step">
                <div class="step-num">3</div>
                <div class="step-content">
                    <h3>Set up the WiFi proxy</h3>
                    <p><strong>Settings &rarr; WiFi &rarr; Long-press your network &rarr; Modify &rarr; Advanced</strong></p>
                    <div class="code-block">Proxy: Manual<br>Hostname: <span class="proxy-host-val"></span><br>Port: {PROXY_PORT}<br>Bypass: (leave empty)</div>
                    <p>Tap <strong>Save</strong>.</p>
                </div>
            </div>
            <div class="step">
                <div class="step-num">4</div>
                <div class="step-content">
                    <h3>Open the Polyconnect app</h3>
                    <p>Open the app, log in, then <strong>tap on your heat pump</strong>.</p>
                    <p style="margin-top:0.3rem;">Watch the status above &mdash; it updates live.</p>
                    <p style="margin-top:0.3rem; font-size:0.75rem; color:var(--yellow);">Note: Some Android apps use certificate pinning. If capture fails, try the iPhone method instead.</p>
                </div>
            </div>
        </div>
    </div>

    <!-- After capture reminder -->
    <div class="warn-card">
        <h3>Important: After setup</h3>
        <p>Remove the proxy settings from your phone's WiFi when done.
        You can also remove the mitmproxy certificate from your trusted certificates.</p>
    </div>
</div>

<script>
function switchTab(tab) {{
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    event.target.classList.add('active');
    document.getElementById('pane-' + tab).classList.add('active');
}}

function updateStatus() {{
    fetch('/api/status')
        .then(r => r.json())
        .then(data => {{
            const cap = data.capture || {{}};
            const ids = cap.ids || {{}};

            // Token
            const tokenEl = document.getElementById('st-token');
            if (cap.token_captured) {{
                tokenEl.className = 'badge badge-ok';
                tokenEl.textContent = 'Captured';
            }}

            // Installation ID
            const instEl = document.getElementById('st-installation');
            if (ids.installation_id) {{
                instEl.className = 'badge badge-ok';
                instEl.textContent = ids.installation_id;
            }}

            // Heat Pump ID
            const hpEl = document.getElementById('st-heatpump');
            if (ids.heat_pump_id) {{
                hpEl.className = 'badge badge-ok';
                hpEl.textContent = ids.heat_pump_id;
            }}

            // Overall status
            const dot = document.getElementById('status-dot');
            if (cap.all_captured) {{
                dot.className = 'dot dot-green';
                document.getElementById('success-banner').style.display = 'block';
                document.getElementById('status-card').style.borderColor = 'var(--green)';
            }} else if (cap.token_captured || ids.installation_id || ids.heat_pump_id) {{
                dot.className = 'dot dot-yellow';
            }}
        }})
        .catch(() => {{}});
}}

setInterval(updateStatus, 2000);
updateStatus();

// Replace proxy host IP with the actual hostname the phone used to reach this page
// (since port 8080 is mapped to the HA host, window.location.hostname is the correct IP)
const proxyHost = window.location.hostname;
document.getElementById('proxy-host').textContent = proxyHost;
document.querySelectorAll('.proxy-host-val').forEach(el => el.textContent = proxyHost);
</script>
</body>
</html>'''
