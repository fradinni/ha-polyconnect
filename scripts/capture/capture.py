#!/usr/bin/env python3
"""
Polyconnect Token & ID Capture Tool
====================================
All-in-one script that:
  1. Starts mitmproxy to intercept Polyconnect app traffic
  2. Serves a beautiful landing page with cert install instructions
  3. Captures JWT token + installation/heat pump IDs automatically

Usage:
    python3 capture.py              # start capture server
    python3 capture.py --port 8080  # custom proxy port
    python3 capture.py --web 9090   # custom web UI port

Requirements:
    pip install mitmproxy
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ── Config ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
TOKEN_FILE = SCRIPT_DIR / "captured_token.txt"
IDS_FILE = SCRIPT_DIR / "captured_ids.json"
MITM_SCRIPT = SCRIPT_DIR / "mitm_addon.py"
CERT_DIR = Path.home() / ".mitmproxy"
CERT_PEM = CERT_DIR / "mitmproxy-ca-cert.pem"
CERT_P12 = CERT_DIR / "mitmproxy-ca-cert.p12"
CERT_CER = CERT_DIR / "mitmproxy-ca-cert.cer"

PROXY_PORT = 8888
WEB_PORT = 8080

# ID extraction patterns (from URL paths in intercepted traffic)
ROUTE_PATTERNS = {
    "installation_id": re.compile(r"/installation-overview/([0-9a-f]{24})"),
    "heat_pump_id": re.compile(r"/heat-pump-view/([0-9a-f]{24})"),
}

# ── Terminal Colors ───────────────────────────────────────────────────────────

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


def status(label: str, state: str, value: str | None = None):
    icons = {
        "wait": f"{C.YELLOW}◌{C.RESET}",
        "ok": f"{C.GREEN}●{C.RESET}",
        "err": f"{C.RED}✗{C.RESET}",
        "info": f"{C.CYAN}ℹ{C.RESET}",
    }
    icon = icons.get(state, " ")
    if value:
        print(f"  {icon}  {label:<22} {C.BOLD}{value}{C.RESET}")
    else:
        print(f"  {icon}  {label}")


# ── Shared Capture State ──────────────────────────────────────────────────────

class CaptureState:
    """Thread-safe state shared between mitmproxy addon and web server."""

    def __init__(self):
        self._lock = threading.Lock()
        self.token: str | None = None
        self.token_source: str | None = None
        self.token_time: str | None = None
        self.ids: dict[str, str | None] = {}
        self.requests_seen: int = 0
        self.target_requests: int = 0
        self.started_at = datetime.datetime.now()
        self._load_existing()

    def _load_existing(self):
        """Load previously captured data if available."""
        if TOKEN_FILE.exists() and TOKEN_FILE.stat().st_size > 0:
            self.token = TOKEN_FILE.read_text().strip()
            self.token_time = datetime.datetime.fromtimestamp(
                TOKEN_FILE.stat().st_mtime
            ).strftime("%Y-%m-%d %H:%M:%S")
            self.token_source = "previously captured"
        if IDS_FILE.exists():
            try:
                data = json.loads(IDS_FILE.read_text())
                self.ids.update(data)
            except Exception:
                pass

    def set_token(self, token: str, source: str):
        with self._lock:
            self.token = token
            self.token_source = source
            self.token_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            TOKEN_FILE.write_text(token)

    def set_id(self, key: str, value: str):
        with self._lock:
            self.ids[key] = value
            self._save_ids()

    def _save_ids(self):
        data = {k: v for k, v in self.ids.items() if v is not None}
        if data:
            IDS_FILE.write_text(json.dumps(data, indent=2) + "\n")

    def check_url(self, url: str):
        """Extract IDs from a URL."""
        for key, pattern in ROUTE_PATTERNS.items():
            if self.ids[key] is None:
                m = pattern.search(url)
                if m:
                    self.set_id(key, m.group(1))

    def increment_requests(self, is_target: bool = False):
        with self._lock:
            self.requests_seen += 1
            if is_target:
                self.target_requests += 1

    @property
    def all_captured(self) -> bool:
        return (
            self.token is not None
            and self.ids.get("installation_id") is not None
            and self.ids.get("heat_pump_id") is not None
        )

    def to_dict(self) -> dict:
        with self._lock:
            token_info = None
            if self.token:
                token_info = self._decode_token(self.token)
            return {
                "token": {
                    "captured": self.token is not None,
                    "source": self.token_source,
                    "time": self.token_time,
                    "preview": (self.token[:40] + "...") if self.token else None,
                    "info": token_info,
                },
                "ids": self.ids,
                "stats": {
                    "requests_seen": self.requests_seen,
                    "target_requests": self.target_requests,
                    "uptime_seconds": int(
                        (datetime.datetime.now() - self.started_at).total_seconds()
                    ),
                },
                "all_captured": self.all_captured,
            }

    @staticmethod
    def _decode_token(token: str) -> dict | None:
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None
            payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            info = {}
            if "email" in payload:
                info["email"] = payload["email"]
            if "sub" in payload:
                info["user_id"] = payload["sub"]
            if "exp" in payload:
                exp = datetime.datetime.fromtimestamp(payload["exp"])
                remaining = exp - datetime.datetime.now()
                hours = int(remaining.total_seconds() // 3600)
                info["expires"] = exp.strftime("%Y-%m-%d %H:%M")
                info["hours_remaining"] = max(0, hours)
            return info
        except Exception:
            return None


# Global state instance
STATE = CaptureState()

# ── mitmproxy Addon Code ─────────────────────────────────────────────────────

ADDON_TEMPLATE = r'''"""
Polyconnect capture addon — full traffic capture.
Auto-generated by capture.py — do not edit.

Captures EVERYTHING from Polyconnect traffic:
- JWT tokens (Authorization headers, URL paths, JSON bodies)
- All MongoDB ObjectIDs (24-char hex) with their JSON key context
- Full JSON response bodies dumped to a log file
"""
from mitmproxy import http, ctx
import json
import datetime
import re
import os
import sys

JWT_FILE = "{jwt_file}"
IDS_FILE = "{ids_file}"
STATUS_FILE = "{status_file}"
DUMP_FILE = "{dump_file}"

TARGET_HOSTS = ("mytech-connect.io", "polytropic")

# Regex for MongoDB ObjectID (24 hex chars)
OBJECTID_RE = re.compile(r"[0-9a-f]{{24}}")

# URL path patterns that may contain IDs
URL_ID_RE = re.compile(r"/([0-9a-f]{{24}})(?:/|$|\?)")

# JSON keys that strongly suggest specific ID types
ID_KEY_HINTS = {{
    "installation_id": [
        "installationId", "installation_id", "installationid",
        "idInstallation", "id_installation",
    ],
    "heat_pump_id": [
        "heatPumpId", "heat_pump_id", "heatpumpid",
        "idHeatPump", "id_heat_pump", "deviceId", "device_id",
        "idDevice", "id_device",
    ],
    "user_id": [
        "userId", "user_id", "userid", "sub", "idUser", "id_user",
    ],
}}

# URL path segments that hint at the type of ID that follows
URL_PATH_HINTS = {{
    "installation_id": ["installation", "installations", "install"],
    "heat_pump_id": ["heat-pump", "heatpump", "device", "devices", "equipment"],
    "user_id": ["user", "users", "account"],
}}

found_tokens = set()
found_ids = {{}}
all_captured_data = []  # full log of everything interesting

def _ts():
    return datetime.datetime.now().strftime("%H:%M:%S")

def _is_target(host: str) -> bool:
    return any(t in host for t in TARGET_HOSTS)

def _looks_like_token(s: str) -> bool:
    if not isinstance(s, str) or len(s) < 40:
        return False
    if s.startswith("eyJ"):
        return True
    if len(s) > 100:
        return True
    return False

def _is_objectid(s: str) -> bool:
    """Check if string is exactly a 24-char lowercase hex (MongoDB ObjectID)."""
    return bool(s) and len(s) == 24 and OBJECTID_RE.fullmatch(s)

def _save_token(token: str, source: str):
    if token in found_tokens:
        return
    found_tokens.add(token)
    with open(JWT_FILE, "w") as f:
        f.write(token)
    ctx.log.warn("")
    ctx.log.warn("=" * 60)
    ctx.log.warn("  JWT TOKEN CAPTURED!")
    ctx.log.warn(f"  Source : {{source}}")
    ctx.log.warn(f"  Token  : {{token[:80]}}...")
    ctx.log.warn(f"  Saved  : {{JWT_FILE}}")
    ctx.log.warn("=" * 60)
    ctx.log.warn("")
    _update_status()

def _save_id(key: str, value: str, source: str = ""):
    """Save an ID only if it's one we care about (installation_id or heat_pump_id)."""
    # Only keep the two IDs we need
    if key not in ("installation_id", "heat_pump_id"):
        ctx.log.debug(f"[{{_ts()}}]    [SKIP] {{key}}: {{value}} (not a target ID)")
        return
    if found_ids.get(key) == value:
        return
    found_ids[key] = value
    # Merge with existing file
    data = {{}}
    if os.path.exists(IDS_FILE):
        try:
            with open(IDS_FILE) as f:
                data = json.load(f)
        except Exception:
            pass
    data[key] = value
    with open(IDS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    label = key.replace("_", " ").title()
    ctx.log.warn(f"[{{_ts()}}]  [CAPTURED] {{label}}: {{value}} ({{source}})")
    _update_status()

def _save_raw(category: str, key: str, value, source: str):
    """Save any interesting data point to the dump file."""
    entry = {{
        "ts": datetime.datetime.now().isoformat(),
        "category": category,
        "key": key,
        "value": value if not isinstance(value, str) or len(value) < 500 else value[:500] + "...",
        "source": source,
    }}
    all_captured_data.append(entry)
    # Append to dump file
    try:
        existing = []
        if os.path.exists(DUMP_FILE):
            with open(DUMP_FILE) as f:
                existing = json.load(f)
        existing.append(entry)
        with open(DUMP_FILE, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception:
        pass

def _update_status():
    """Write a status file the web server can read."""
    data = {{
        "token_captured": bool(found_tokens),
        "ids": found_ids,
        "last_update": datetime.datetime.now().isoformat(),
    }}
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

def _guess_id_type_from_key(key: str) -> str:
    """Guess the ID type from a JSON key name."""
    key_lower = key.lower()
    for id_type, hints in ID_KEY_HINTS.items():
        for hint in hints:
            if hint.lower() == key_lower:
                return id_type
    # Generic _id or Id suffix
    if key_lower == "_id" or key_lower == "id":
        return "object_id"
    if key_lower.endswith("id") or key_lower.endswith("_id"):
        # Try to infer from prefix
        prefix = key_lower.replace("_id", "").replace("id", "").strip("_")
        if prefix:
            return f"{{prefix}}_id"
    return "unknown_id"

def _guess_id_type_from_url(url: str, id_value: str) -> str:
    """Guess the ID type from the URL path context."""
    url_lower = url.lower()
    # Find the segment before the ID
    parts = url_lower.split("/")
    for i, part in enumerate(parts):
        if id_value in part or (len(part) == 24 and OBJECTID_RE.fullmatch(part)):
            # Look at previous segment
            if i > 0:
                prev = parts[i - 1]
                for id_type, hints in URL_PATH_HINTS.items():
                    for hint in hints:
                        if hint in prev:
                            return id_type
    return "url_id"

def _extract_ids_from_url(url: str, source: str):
    """Extract all ObjectIDs from URL path."""
    matches = URL_ID_RE.findall(url)
    for id_val in matches:
        id_type = _guess_id_type_from_url(url, id_val)
        _save_id(id_type, id_val, f"URL path: {{source}}")
        _save_raw("id_from_url", id_type, id_val, source)

def _scan_json_deep(data, path="", source=""):
    """Recursively scan JSON for tokens AND ObjectIDs."""
    if isinstance(data, dict):
        for k, v in data.items():
            child_path = f"{{path}}.{{k}}" if path else k
            if isinstance(v, str):
                # Check for token
                if _looks_like_token(v):
                    _save_token(v, f"JSON '{{child_path}}' -> {{source}}")
                    _save_raw("token", child_path, v[:100], source)
                # Check for ObjectID
                elif _is_objectid(v):
                    id_type = _guess_id_type_from_key(k)
                    _save_id(id_type, v, f"JSON '{{child_path}}' in {{source}}")
                    _save_raw("id", child_path, v, source)
            elif isinstance(v, (dict, list)):
                _scan_json_deep(v, child_path, source)
    elif isinstance(data, list):
        for i, v in enumerate(data):
            _scan_json_deep(v, f"{{path}}[{{i}}]", source)

def request(flow: http.HTTPFlow):
    host = flow.request.pretty_host
    method = flow.request.method
    path = flow.request.path
    url = flow.request.pretty_url

    ctx.log.debug(f"[{{_ts()}}] >> {{method}} {{host}}{{path[:120]}}")

    if not _is_target(host):
        return

    ctx.log.debug(f"[{{_ts()}}]    [TARGET] checking request...")
    source = f"{{method}} {{host}}{{path}}"

    # Extract IDs from URL
    _extract_ids_from_url(url, source)

    # Check Authorization header
    auth = flow.request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
        ctx.log.warn(f"[{{_ts()}}]    [FOUND] Bearer token ({{len(token)}} chars)")
        _save_token(token, f"Authorization header -> {{source}}")
        return

    # Check /from-native/ URL pattern
    if "/from-native/" in path:
        raw = path.split("/from-native/", 1)[1].split("?")[0]
        ctx.log.warn(f"[{{_ts()}}]    [FOUND] /from-native/ token ({{len(raw)}} chars)")
        _save_token(raw, f"/from-native/ URL path -> {{host}}")
        return

    # Scan request body for JSON with IDs/tokens
    if flow.request.content:
        ct = flow.request.headers.get("content-type", "")
        if "json" in ct:
            try:
                data = json.loads(flow.request.content)
                _scan_json_deep(data, source=f"request body -> {{source}}")
                _save_raw("request_body", source, data, source)
            except Exception:
                pass

def response(flow: http.HTTPFlow):
    host = flow.request.pretty_host
    path = flow.request.path
    url = flow.request.pretty_url
    resp_status = flow.response.status_code
    method = flow.request.method

    ctx.log.debug(f"[{{_ts()}}] << {{method}} {{host}}{{path[:80]}} -> {{resp_status}}")

    if not _is_target(host):
        return

    source = f"{{method}} {{host}}{{path}} (HTTP {{resp_status}})"

    # Extract IDs from URL (response confirms the resource exists)
    _extract_ids_from_url(url, source)

    ct = flow.response.headers.get("content-type", "")
    if "json" not in ct and "text" not in ct:
        return

    try:
        body = flow.response.content
        if not body:
            return
        data = json.loads(body)

        # Log the full response
        ctx.log.debug(f"[{{_ts()}}]    [TARGET] JSON response ({{len(body)}} bytes)")
        _save_raw("response_body", source, data, source)

        # Deep scan for tokens and IDs
        _scan_json_deep(data, source=source)

    except json.JSONDecodeError:
        # Maybe it's a plain text token
        try:
            text = flow.response.content.decode("utf-8", errors="replace").strip()
            if _looks_like_token(text):
                _save_token(text, f"plain text response -> {{source}}")
            elif _is_objectid(text):
                _save_id("response_id", text, f"plain text -> {{source}}")
        except Exception:
            pass
    except Exception as e:
        ctx.log.debug(f"[{{_ts()}}]    parse error: {{e}}")


# ── WebSocket interception (SignalR / Blazor) ─────────────────────────────────

# Regex to find any 24-char hex string anywhere in text
OBJECTID_INLINE_RE = re.compile(r"[0-9a-f]{{24}}")

# Regex for Blazor navigation URLs containing IDs
NAV_URL_RE = re.compile(
    r"/(installation-overview|heat-pump-view|heat-pump-edit-mode|"
    r"heat-pump-edit-power-mode|devices-management|pool-info-edit|"
    r"support(?:/device)?)/([0-9a-f]{{24}})"
)

# Map URL segment to ID type (strict: only unambiguous paths)
NAV_SEGMENT_TO_TYPE = {{
    "installation-overview": "installation_id",
    "heat-pump-view": "heat_pump_id",
    "heat-pump-edit-mode": "heat_pump_id",
    "heat-pump-edit-power-mode": "heat_pump_id",
}}

def websocket_message(flow: http.HTTPFlow):
    """Intercept WebSocket messages (SignalR frames from Blazor)."""
    host = flow.request.pretty_host
    if not _is_target(host):
        return

    message = flow.websocket.messages[-1]
    is_text = message.type == 1  # 1 = text, 2 = binary

    content = None
    if is_text:
        content = message.content.decode("utf-8", errors="replace") if isinstance(message.content, bytes) else message.content
    else:
        # Binary frame — try to decode as UTF-8 to find embedded strings
        try:
            content = message.content.decode("utf-8", errors="replace") if isinstance(message.content, bytes) else str(message.content)
        except Exception:
            content = repr(message.content)

    if not content:
        return

    direction = "C->S" if message.from_client else "S->C"
    ctx.log.debug(f"[{{_ts()}}] WS {{direction}} ({{len(content)}} chars)")

    # --- Look for navigation URLs with IDs ---
    nav_matches = NAV_URL_RE.findall(content)
    for segment, id_val in nav_matches:
        id_type = NAV_SEGMENT_TO_TYPE.get(segment)
        if not id_type:
            continue
        # Skip if already captured this exact ID
        if found_ids.get(id_type) == id_val:
            continue
        ctx.log.warn(f"[{{_ts()}}]    [WS-NAV] /{{segment}}/{{id_val}} -> {{id_type}}")
        _save_id(id_type, id_val, f"WebSocket navigation /{{segment}}/")
        _save_raw("ws_navigation", id_type, id_val, f"WS {{direction}} /{{segment}}/{{id_val}}")

    # --- Look for any ObjectIDs in the frame ---
    all_ids = OBJECTID_INLINE_RE.findall(content)
    for id_val in all_ids:
        # Skip if already found via nav URL
        if any(id_val == v for v in found_ids.values()):
            continue
        # Try to find context around the ID
        idx = content.find(id_val)
        context_start = max(0, idx - 50)
        context_end = min(len(content), idx + 24 + 50)
        context = content[context_start:context_end].replace("\\n", " ").strip()
        ctx.log.debug(f"[{{_ts()}}]    [WS-ID] {{id_val}} (context: {{context[:80]}})")
        _save_raw("ws_objectid", id_val, context[:200], f"WS {{direction}}")

    # --- Look for tokens in the frame ---
    if "eyJ" in content:
        # Find JWT-like strings
        for match in re.finditer(r"eyJ[A-Za-z0-9_-]{{2,}}\.eyJ[A-Za-z0-9_-]{{2,}}\.[A-Za-z0-9_-]{{2,}}", content):
            token = match.group(0)
            ctx.log.warn(f"[{{_ts()}}]    [WS-TOKEN] JWT found ({{len(token)}} chars)")
            _save_token(token, f"WebSocket frame {{direction}}")

    # --- Try to parse as JSON (SignalR text frames are often JSON) ---
    # SignalR uses record separator (0x1E) between messages
    parts = content.split("\\x1e") if "\\x1e" in content else [content]
    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            data = json.loads(part)
            _scan_json_deep(data, source=f"WebSocket {{direction}}")
            # Log interesting frames
            if isinstance(data, dict):
                msg_type = data.get("type")
                target = data.get("target", "")
                if target or msg_type:
                    _save_raw("ws_frame", f"type={{msg_type}} target={{target}}", data, f"WS {{direction}}")
        except (json.JSONDecodeError, ValueError):
            pass

    # --- Also scan raw binary for any readable ObjectIDs ---
    if not is_text and isinstance(message.content, bytes):
        raw = message.content
        # Scan for hex sequences that might be ObjectIDs stored as ASCII in binary
        text_in_binary = raw.decode("latin-1", errors="replace")
        bin_ids = OBJECTID_INLINE_RE.findall(text_in_binary)
        for id_val in bin_ids:
            if any(id_val == v for v in found_ids.values()):
                continue
            ctx.log.debug(f"[{{_ts()}}]    [WS-BIN-ID] {{id_val}}")
            _save_raw("ws_binary_objectid", id_val, "", f"WS binary {{direction}}")
'''

# ── HTML Landing Page ─────────────────────────────────────────────────────────

def build_landing_html(local_ip: str, proxy_port: int, web_port: int) -> str:
    """Generate the landing page HTML."""
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polyconnect Capture</title>
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
}}
.container {{ max-width: 720px; margin: 0 auto; }}
header {{
    text-align: center;
    padding: 2rem 0 1.5rem;
}}
header h1 {{
    font-size: 1.8rem;
    font-weight: 700;
    background: linear-gradient(135deg, var(--accent), var(--purple));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}}
header p {{
    color: var(--text-dim);
    margin-top: 0.4rem;
    font-size: 0.9rem;
}}
.status-card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.2rem;
    margin-bottom: 1rem;
}}
.status-card h2 {{
    font-size: 1rem;
    color: var(--accent);
    margin-bottom: 0.8rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}}
.status-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.5rem 0;
    border-bottom: 1px solid var(--surface-2);
}}
.status-row:last-child {{ border-bottom: none; }}
.status-label {{ color: var(--text-dim); font-size: 0.85rem; }}
.status-value {{ font-family: 'SF Mono', monospace; font-size: 0.85rem; }}
.badge {{
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    padding: 0.2rem 0.6rem;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 600;
}}
.badge-ok {{ background: rgba(74,222,128,0.15); color: var(--green); }}
.badge-wait {{ background: rgba(251,191,36,0.15); color: var(--yellow); }}
.badge-err {{ background: rgba(248,113,113,0.15); color: var(--red); }}
.dot {{
    width: 6px; height: 6px;
    border-radius: 50%;
    display: inline-block;
}}
.dot-green {{ background: var(--green); }}
.dot-yellow {{ background: var(--yellow); animation: pulse 2s infinite; }}
.dot-red {{ background: var(--red); }}
@keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}
.tabs {{
    display: flex;
    gap: 0;
    margin-bottom: 0;
}}
.tab {{
    flex: 1;
    padding: 0.7rem;
    text-align: center;
    font-size: 0.85rem;
    font-weight: 600;
    cursor: pointer;
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--text-dim);
    transition: all 0.2s;
}}
.tab:first-child {{ border-radius: 12px 0 0 0; }}
.tab:last-child {{ border-radius: 0 12px 0 0; }}
.tab.active {{
    background: var(--surface);
    color: var(--accent);
    border-bottom-color: var(--surface);
}}
.tab-content {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-top: none;
    border-radius: 0 0 12px 12px;
    padding: 1.5rem;
    margin-bottom: 1rem;
}}
.tab-pane {{ display: none; }}
.tab-pane.active {{ display: block; }}
.step {{
    display: flex;
    gap: 0.8rem;
    margin-bottom: 1.2rem;
}}
.step-num {{
    flex-shrink: 0;
    width: 26px; height: 26px;
    background: var(--accent);
    color: var(--bg);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.75rem;
    font-weight: 700;
}}
.step-content h3 {{ font-size: 0.9rem; margin-bottom: 0.3rem; }}
.step-content p {{ font-size: 0.82rem; color: var(--text-dim); line-height: 1.5; }}
.code-block {{
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.7rem 1rem;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 0.82rem;
    margin: 0.5rem 0;
    word-break: break-all;
    color: var(--green);
}}
.btn {{
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.6rem 1.2rem;
    border-radius: 8px;
    font-size: 0.85rem;
    font-weight: 600;
    text-decoration: none;
    transition: all 0.2s;
    border: none;
    cursor: pointer;
}}
.btn-primary {{
    background: var(--accent);
    color: var(--bg);
}}
.btn-primary:hover {{ filter: brightness(1.1); transform: translateY(-1px); }}
.btn-outline {{
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text);
}}
.btn-outline:hover {{ border-color: var(--accent); color: var(--accent); }}
.proxy-info {{
    background: var(--bg);
    border: 1px solid var(--accent);
    border-radius: 8px;
    padding: 1rem;
    margin: 1rem 0;
    text-align: center;
}}
.proxy-info .label {{ font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; }}
.proxy-info .value {{ font-size: 1.4rem; font-weight: 700; color: var(--accent); font-family: monospace; }}
.certs {{ display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 0.8rem 0; }}
footer {{
    text-align: center;
    padding: 1.5rem 0;
    color: var(--text-dim);
    font-size: 0.75rem;
}}
</style>
</head>
<body>
<div class="container">
    <header>
        <h1>Polyconnect Capture</h1>
        <p>Token &amp; Device ID capture via MITM proxy</p>
    </header>

    <!-- Live Status -->
    <div class="status-card" id="status-card">
        <h2><span class="dot dot-yellow" id="status-dot"></span> Capture Status</h2>
        <div class="status-row">
            <span class="status-label">JWT Token</span>
            <span id="st-token" class="badge badge-wait">&#9203; Waiting</span>
        </div>
        <div id="ids-container">
            <div class="status-row">
                <span class="status-label">IDs</span>
                <span class="badge badge-wait">&#9203; Waiting for traffic</span>
            </div>
        </div>
        <div class="status-row">
            <span class="status-label">Requests proxied</span>
            <span id="st-requests" class="status-value">0</span>
        </div>
    </div>

    <!-- Proxy config box -->
    <div class="proxy-info">
        <div class="label">Configure your phone's proxy to</div>
        <div class="value">{local_ip}:{proxy_port}</div>
    </div>

    <!-- Platform Tabs -->
    <div class="tabs">
        <div class="tab active" onclick="switchTab('iphone')">iPhone</div>
        <div class="tab" onclick="switchTab('android')">Android</div>
    </div>

    <div class="tab-content">
        <!-- iPhone Instructions -->
        <div class="tab-pane active" id="pane-iphone">
            <div class="step">
                <div class="step-num">1</div>
                <div class="step-content">
                    <h3>Download &amp; Install Certificate</h3>
                    <p>Tap the button below <strong>from Safari on your iPhone</strong> (not Chrome).</p>
                    <div class="certs">
                        <a href="/cert/pem" class="btn btn-primary">📥 Download CA Cert (.pem)</a>
                    </div>
                    <p>When prompted, tap <strong>Allow</strong> to download the profile.</p>
                </div>
            </div>
            <div class="step">
                <div class="step-num">2</div>
                <div class="step-content">
                    <h3>Install the Profile</h3>
                    <p>Go to: <strong>Settings → General → VPN &amp; Device Management</strong><br>
                    Tap the <em>mitmproxy</em> profile → <strong>Install</strong> → Enter passcode → <strong>Install</strong></p>
                </div>
            </div>
            <div class="step">
                <div class="step-num">3</div>
                <div class="step-content">
                    <h3>Trust the Certificate</h3>
                    <p>Go to: <strong>Settings → General → About → Certificate Trust Settings</strong><br>
                    Toggle ON for <em>mitmproxy</em> → tap <strong>Continue</strong></p>
                </div>
            </div>
            <div class="step">
                <div class="step-num">4</div>
                <div class="step-content">
                    <h3>Configure WiFi Proxy</h3>
                    <p>Go to: <strong>Settings → WiFi → tap ⓘ on your network</strong><br>
                    Scroll down → <strong>HTTP Proxy → Manual</strong></p>
                    <div class="code-block">Server: {local_ip}<br>Port: {proxy_port}<br>Authentication: Off</div>
                    <p>Tap <strong>Save</strong>.</p>
                </div>
            </div>
            <div class="step">
                <div class="step-num">5</div>
                <div class="step-content">
                    <h3>Open Polyconnect App &amp; Select Your Heat Pump</h3>
                    <p>Open the Polyconnect app and log in normally. Then <strong>tap on the heat pump you want to connect to Home Assistant</strong>.</p>
                    <p style="margin-top:0.4rem;">This navigation is required so the tool can capture the unique identifiers for your specific device. Watch the status card above — it updates in real-time as each piece is captured.</p>
                </div>
            </div>
        </div>

        <!-- Android Instructions -->
        <div class="tab-pane" id="pane-android">
            <div class="step">
                <div class="step-num">1</div>
                <div class="step-content">
                    <h3>Download Certificate</h3>
                    <p>Tap the button below from your Android phone's browser.</p>
                    <div class="certs">
                        <a href="/cert/pem" class="btn btn-primary">📥 Download CA Cert (.pem)</a>
                        <a href="/cert/cer" class="btn btn-outline">📥 .cer format</a>
                    </div>
                </div>
            </div>
            <div class="step">
                <div class="step-num">2</div>
                <div class="step-content">
                    <h3>Install the Certificate</h3>
                    <p>Go to: <strong>Settings → Security → Encryption &amp; credentials → Install a certificate → CA certificate</strong></p>
                    <p>Select the downloaded file. You may need to confirm with <strong>Install anyway</strong>.</p>
                    <p><em>On Samsung:</em> Settings → Biometrics and Security → Other security settings → Install from device storage → CA certificate</p>
                </div>
            </div>
            <div class="step">
                <div class="step-num">3</div>
                <div class="step-content">
                    <h3>Configure WiFi Proxy</h3>
                    <p>Go to: <strong>Settings → WiFi → Long-press your network → Modify → Advanced options</strong></p>
                    <div class="code-block">Proxy: Manual<br>Proxy hostname: {local_ip}<br>Proxy port: {proxy_port}<br>Bypass: (leave empty)</div>
                    <p>Tap <strong>Save</strong>.</p>
                </div>
            </div>
            <div class="step">
                <div class="step-num">4</div>
                <div class="step-content">
                    <h3>Open Polyconnect App &amp; Select Your Heat Pump</h3>
                    <p>Open the Polyconnect app and log in normally. Then <strong>tap on the heat pump you want to connect to Home Assistant</strong>.</p>
                    <p style="margin-top:0.4rem;">This navigation is required so the tool can capture the unique identifiers for your specific device. Watch the status card above — it updates in real-time.</p>
                    <p style="margin-top:0.4rem;">⚠️ <em>Note:</em> Some Android apps use certificate pinning. If capture fails, you may need to use the iOS method or root your device.</p>
                </div>
            </div>
        </div>
    </div>

    <!-- Cleanup reminder -->
    <div class="status-card">
        <h2>⚠️ After Capture</h2>
        <p style="color: var(--text-dim); font-size: 0.85rem; line-height: 1.5;">
            Remember to <strong>remove the proxy settings</strong> from your phone's WiFi configuration when done.
            You can also remove the mitmproxy certificate from your device's trusted certificates.
        </p>
    </div>

    <footer>
        Polyconnect Capture Tool &mdash; mitmproxy on port {proxy_port}
    </footer>
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
            // Token
            const tokenEl = document.getElementById('st-token');
            if (data.token.captured) {{
                let label = '\u2713 Captured';
                if (data.token.info && data.token.info.hours_remaining !== undefined) {{
                    label += ' (' + data.token.info.hours_remaining + 'h left)';
                }}
                tokenEl.className = 'badge badge-ok';
                tokenEl.textContent = label;
            }}

            // Dynamic IDs
            const idsContainer = document.getElementById('ids-container');
            const ids = data.ids || {{}};
            const idKeys = Object.keys(ids);
            if (idKeys.length > 0) {{
                let html = '';
                idKeys.forEach(key => {{
                    const val = ids[key];
                    const label = key.replace(/_/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
                    html += '<div class="status-row">';
                    html += '<span class="status-label">' + label + '</span>';
                    if (val) {{
                        html += '<span class="badge badge-ok">\u2713 ' + val + '</span>';
                    }} else {{
                        html += '<span class="badge badge-wait">\u23F3 Waiting</span>';
                    }}
                    html += '</div>';
                }});
                idsContainer.innerHTML = html;
            }}

            // Requests
            document.getElementById('st-requests').textContent =
                data.stats.requests_seen + ' total, ' + data.stats.target_requests + ' target';

            // Overall status dot
            const dot = document.getElementById('status-dot');
            if (data.all_captured) {{
                dot.className = 'dot dot-green';
            }} else if (data.token.captured || idKeys.length > 0) {{
                dot.className = 'dot dot-yellow';
            }}
        }})
        .catch(() => {{}});
}}

// Poll every 2 seconds
setInterval(updateStatus, 2000);
updateStatus();
</script>
</body>
</html>'''


# ── Web Server ────────────────────────────────────────────────────────────────

class CaptureHandler(BaseHTTPRequestHandler):
    """HTTP handler for the landing page and API."""

    local_ip: str = "127.0.0.1"
    proxy_port: int = PROXY_PORT
    web_port: int = WEB_PORT

    def log_message(self, format, *args):
        # Suppress default access logs
        pass

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "":
            self._serve_html()
        elif path == "/api/status":
            self._serve_status()
        elif path == "/cert/pem":
            self._serve_cert(CERT_PEM, "application/x-pem-file", "mitmproxy-ca-cert.pem")
        elif path == "/cert/cer":
            self._serve_cert(CERT_CER if CERT_CER.exists() else CERT_PEM,
                           "application/x-x509-ca-cert", "mitmproxy-ca-cert.cer")
        elif path == "/cert/p12":
            self._serve_cert(CERT_P12, "application/x-pkcs12", "mitmproxy-ca-cert.p12")
        else:
            self.send_error(404)

    def _serve_html(self):
        html = build_landing_html(self.local_ip, self.proxy_port, self.web_port)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_status(self):
        data = json.dumps(STATE.to_dict()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _serve_cert(self, cert_path: Path, content_type: str, filename: str):
        if not cert_path.exists():
            self.send_error(404, "Certificate not found. Start the proxy first.")
            return
        data = cert_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f"attachment; filename={filename}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_web_server(local_ip: str, web_port: int, proxy_port: int) -> HTTPServer:
    """Start the web UI server in a background thread."""
    CaptureHandler.local_ip = local_ip
    CaptureHandler.proxy_port = proxy_port
    CaptureHandler.web_port = web_port

    server = HTTPServer(("0.0.0.0", web_port), CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ── mitmproxy Management ──────────────────────────────────────────────────────

def find_mitmdump() -> str | None:
    """Locate mitmdump binary."""
    for candidate in ["mitmdump", str(Path.home() / ".local" / "bin" / "mitmdump")]:
        if shutil.which(candidate) or Path(candidate).exists():
            return candidate
    return None


def ensure_cert() -> bool:
    """Ensure mitmproxy CA cert exists, generating if needed."""
    if CERT_PEM.exists():
        return True

    mitmdump = find_mitmdump()
    if not mitmdump:
        return False

    status("Certificate", "info", "generating CA cert...")
    proc = subprocess.Popen(
        [mitmdump, "--listen-port", "0"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    proc.terminate()
    proc.wait()
    return CERT_PEM.exists()


def write_addon_script() -> Path:
    """Write the mitmproxy addon script to disk."""
    status_file = SCRIPT_DIR / ".capture_status.json"
    dump_file = SCRIPT_DIR / "captured_dump.json"
    code = ADDON_TEMPLATE.format(
        jwt_file=str(TOKEN_FILE),
        ids_file=str(IDS_FILE),
        status_file=str(status_file),
        dump_file=str(dump_file),
    )
    MITM_SCRIPT.write_text(code)
    return MITM_SCRIPT


def start_mitmdump(proxy_port: int) -> subprocess.Popen | None:
    """Start mitmdump with the capture addon."""
    mitmdump = find_mitmdump()
    if not mitmdump:
        return None

    addon_path = write_addon_script()
    cmd = [
        mitmdump,
        "--listen-port", str(proxy_port),
        "--ssl-insecure",
        "--set", "stream_large_bodies=1",
        "--set", "console_eventlog_verbosity=warn",
        "-s", str(addon_path),
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc


# ── Status File Watcher ───────────────────────────────────────────────────────

def watch_status_file():
    """Background thread that syncs addon status file + IDs file → STATE."""
    status_file = SCRIPT_DIR / ".capture_status.json"
    last_status_mtime = 0.0
    last_ids_mtime = 0.0

    while True:
        time.sleep(1)
        try:
            # Watch status file from addon
            if status_file.exists():
                mtime = status_file.stat().st_mtime
                if mtime > last_status_mtime:
                    last_status_mtime = mtime
                    data = json.loads(status_file.read_text())

                    # Sync token
                    if data.get("token_captured") and TOKEN_FILE.exists():
                        token = TOKEN_FILE.read_text().strip()
                        if token and STATE.token != token:
                            STATE.set_token(token, "mitmproxy addon")
                            status("Token", "ok", f"captured ({len(token)} chars)")

                    # Sync IDs from status
                    for key, value in data.get("ids", {}).items():
                        if value and STATE.ids.get(key) != value:
                            STATE.set_id(key, value)
                            label = key.replace("_", " ").title()
                            status(label, "ok", value)

            # Also watch IDS_FILE directly (addon writes there too)
            if IDS_FILE.exists():
                mtime = IDS_FILE.stat().st_mtime
                if mtime > last_ids_mtime:
                    last_ids_mtime = mtime
                    ids_data = json.loads(IDS_FILE.read_text())
                    for key, value in ids_data.items():
                        if value and STATE.ids.get(key) != value:
                            STATE.set_id(key, value)
                            label = key.replace("_", " ").title()
                            status(label, "ok", value)

        except Exception:
            pass


# ── Log Reader ────────────────────────────────────────────────────────────────

def read_proxy_output(proc: subprocess.Popen):
    """Read mitmdump stdout and update request counters."""
    for line in iter(proc.stdout.readline, b""):
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue

        STATE.increment_requests(
            is_target="[FOUND]" in text or "[CAPTURED]" in text or "[WS-NAV]" in text
        )

        # Only show capture events with source/path info
        if "[CAPTURED]" in text or "TOKEN CAPTURED" in text:
            print(f"  {C.GREEN}▶{C.RESET}  {text}")
        elif "[FOUND]" in text or "[WS-NAV]" in text:
            print(f"  {C.GREEN}▶{C.RESET}  {text}")


# ── Main ──────────────────────────────────────────────────────────────────────

def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "192.168.x.x"


def main():
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(
        description="Polyconnect Token & ID Capture Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--port", type=int, default=PROXY_PORT, help="mitmproxy listen port (default: 8888)")
    parser.add_argument("--web", type=int, default=WEB_PORT, help="web UI port (default: 8080)")
    args = parser.parse_args()

    proxy_port = args.port
    web_port = args.web
    local_ip = get_local_ip()

    # ── Banner ────────────────────────────────────────────────────────────────
    os.system("clear")
    print(f"""
{C.CYAN}{C.BOLD}
  ╔═══════════════════════════════════════════════════════════╗
  ║         POLYCONNECT  CAPTURE  TOOL                       ║
  ║         Token + Device ID auto-capture                   ║
  ╚═══════════════════════════════════════════════════════════╝
{C.RESET}""")

    # ── Check dependencies ────────────────────────────────────────────────────
    mitmdump = find_mitmdump()
    if not mitmdump:
        status("mitmdump", "err", "not found")
        print(f"\n  {C.DIM}Install with:{C.RESET}  pip install mitmproxy\n")
        sys.exit(1)
    status("mitmdump", "ok", mitmdump)

    # ── Ensure certificate exists ─────────────────────────────────────────────
    if not ensure_cert():
        status("CA Certificate", "err", "could not generate")
        sys.exit(1)
    status("CA Certificate", "ok", str(CERT_PEM))

    # ── Start web server ──────────────────────────────────────────────────────
    web_server = start_web_server(local_ip, web_port, proxy_port)
    status("Web UI", "ok", f"http://{local_ip}:{web_port}")

    # ── Start mitmproxy ───────────────────────────────────────────────────────
    status("Proxy", "info", f"starting on port {proxy_port}...")
    proc = start_mitmdump(proxy_port)
    if not proc:
        status("Proxy", "err", "failed to start mitmdump")
        sys.exit(1)
    time.sleep(1)
    if proc.poll() is not None:
        status("Proxy", "err", "mitmdump exited immediately")
        sys.exit(1)
    status("Proxy", "ok", f"listening on :{proxy_port}")

    # ── Start background watchers ─────────────────────────────────────────────
    threading.Thread(target=watch_status_file, daemon=True).start()
    threading.Thread(target=read_proxy_output, args=(proc,), daemon=True).start()

    # ── Print instructions ────────────────────────────────────────────────────
    print(f"""
{C.CYAN}  {'─' * 55}{C.RESET}
{C.BOLD}  Open on your phone:{C.RESET}

    {C.GREEN}http://{local_ip}:{web_port}{C.RESET}

{C.BOLD}  Or configure proxy manually:{C.RESET}

    Server: {C.WHITE}{local_ip}{C.RESET}
    Port:   {C.WHITE}{proxy_port}{C.RESET}

{C.CYAN}  {'─' * 55}{C.RESET}
  {C.DIM}Waiting for Polyconnect app traffic...{C.RESET}
  {C.DIM}Press Ctrl+C to stop.{C.RESET}
""")

    # ── Main loop ─────────────────────────────────────────────────────────────
    try:
        while True:
            time.sleep(1)
            if proc.poll() is not None:
                status("Proxy", "err", "mitmdump exited unexpectedly")
                break
            if STATE.all_captured:
                print(f"\n  {C.GREEN}{C.BOLD}✓ All data captured successfully!{C.RESET}")
                print(f"  {C.DIM}Token: {TOKEN_FILE}{C.RESET}")
                print(f"  {C.DIM}IDs:   {IDS_FILE}{C.RESET}")
                print(f"\n  {C.DIM}Server still running — Ctrl+C to stop.{C.RESET}\n")
                # Keep running so the web UI stays available
                while proc.poll() is None:
                    time.sleep(1)
                break
    except KeyboardInterrupt:
        print(f"\n\n  {C.DIM}Shutting down...{C.RESET}")

    # ── Cleanup ───────────────────────────────────────────────────────────────
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    web_server.shutdown()

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n{C.CYAN}  {'─' * 55}{C.RESET}")
    print(f"  {C.BOLD}Results:{C.RESET}")
    if STATE.token:
        status("JWT Token", "ok", f"{len(STATE.token)} chars → {TOKEN_FILE.name}")
    else:
        status("JWT Token", "err", "not captured")
    for key, value in STATE.ids.items():
        label = key.replace("_", " ").title()
        if value:
            status(label, "ok", value)
        else:
            status(label, "err", "not captured")
    print(f"{C.CYAN}  {'─' * 55}{C.RESET}")

    # Cleanup temp files
    status_file = SCRIPT_DIR / ".capture_status.json"
    if status_file.exists():
        status_file.unlink()

    print(f"\n  {C.YELLOW}⚠{C.RESET}  Don't forget to remove the proxy from your phone's WiFi settings!\n")


if __name__ == "__main__":
    main()
