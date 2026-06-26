"""Polyconnect mitmproxy addon — captures token + device IDs from traffic.

Designed to run inside the Polyconnect Bridge add-on container.
Reads output paths from environment variables set by capture_manager.py.

Captures:
- JWT/session tokens from Authorization headers and /from-native/ URLs
- installation_id and heat_pump_id from URL navigation paths
- IDs from WebSocket SignalR frames (Blazor navigation)
"""
from mitmproxy import http, ctx
import json
import datetime
import os
import re

# ── Output paths (set by capture_manager.py via env) ──────────────────────────

TOKEN_FILE = os.environ.get("CAPTURE_TOKEN_FILE", "/data/token.txt")
IDS_FILE = os.environ.get("CAPTURE_IDS_FILE", "/data/ids.json")
STATUS_FILE = os.environ.get("CAPTURE_STATUS_FILE", "/data/.capture_status.json")

# ── Target hosts ──────────────────────────────────────────────────────────────

TARGET_HOSTS = ("mytech-connect.io", "polytropic")

# ── Patterns ──────────────────────────────────────────────────────────────────

# URL path patterns for the two IDs we need
NAV_URL_RE = re.compile(
    r"/(installation-overview|heat-pump-view|heat-pump-edit-mode|"
    r"heat-pump-edit-power-mode|devices-management)/([0-9a-f]{24})"
)

# Map URL segment → ID type (only unambiguous paths)
NAV_SEGMENT_TO_TYPE = {
    "installation-overview": "installation_id",
    "heat-pump-view": "heat_pump_id",
    "heat-pump-edit-mode": "heat_pump_id",
    "heat-pump-edit-power-mode": "heat_pump_id",
}

# MongoDB ObjectID in URL paths
URL_ID_RE = re.compile(r"/([0-9a-f]{24})(?:/|$|\?)")

# URL path segments that hint at the type of ID that follows
URL_PATH_HINTS = {
    "installation_id": ["installation", "installations", "install"],
    "heat_pump_id": ["heat-pump", "heatpump", "device", "devices", "equipment"],
}

# ── State ─────────────────────────────────────────────────────────────────────

found_tokens: set = set()
found_ids: dict = {}


def _ts():
    return datetime.datetime.now().strftime("%H:%M:%S")


def _is_target(host: str) -> bool:
    return any(t in host for t in TARGET_HOSTS)


def _looks_like_token(s: str) -> bool:
    """Detect session tokens — Polyconnect uses custom-encoded opaque tokens."""
    if not isinstance(s, str) or len(s) < 40:
        return False
    # Standard JWT prefix
    if s.startswith("eyJ"):
        return True
    # Long opaque tokens (Polyconnect's custom encoding)
    if len(s) > 100 and "/" not in s[:20] and " " not in s:
        return True
    return False


def _save_token(token: str, source: str):
    if token in found_tokens:
        return
    found_tokens.add(token)
    with open(TOKEN_FILE, "w") as f:
        f.write(token)
    ctx.log.warn("")
    ctx.log.warn("=" * 60)
    ctx.log.warn("  [CAPTURED] SESSION TOKEN!")
    ctx.log.warn(f"  Source : {source}")
    ctx.log.warn(f"  Length : {len(token)} chars")
    ctx.log.warn(f"  Saved  : {TOKEN_FILE}")
    ctx.log.warn("=" * 60)
    ctx.log.warn("")
    _update_status()


def _save_id(key: str, value: str, source: str = ""):
    """Save an ID — only keeps installation_id and heat_pump_id."""
    if key not in ("installation_id", "heat_pump_id"):
        return
    if found_ids.get(key) == value:
        return
    found_ids[key] = value

    # Merge with existing file
    data = {}
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
    ctx.log.warn(f"[{_ts()}]  [CAPTURED] {label}: {value} ({source})")
    _update_status()


def _update_status():
    """Write status file for the capture manager to read."""
    data = {
        "token_captured": bool(found_tokens),
        "ids": found_ids,
        "last_update": datetime.datetime.now().isoformat(),
    }
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _guess_id_type_from_url(url: str, id_value: str) -> str:
    """Guess the ID type from the URL path context."""
    url_lower = url.lower()
    parts = url_lower.split("/")
    for i, part in enumerate(parts):
        if id_value in part or (len(part) == 24 and re.fullmatch(r"[0-9a-f]{24}", part)):
            if i > 0:
                prev = parts[i - 1]
                for id_type, hints in URL_PATH_HINTS.items():
                    for hint in hints:
                        if hint in prev:
                            return id_type
    return "unknown_id"


def _extract_ids_from_url(url: str, source: str):
    """Extract IDs from URL path."""
    matches = URL_ID_RE.findall(url)
    for id_val in matches:
        id_type = _guess_id_type_from_url(url, id_val)
        _save_id(id_type, id_val, f"URL: {source}")


# ── HTTP request/response hooks ───────────────────────────────────────────────

def request(flow: http.HTTPFlow):
    host = flow.request.pretty_host
    path = flow.request.path
    url = flow.request.pretty_url

    if not _is_target(host):
        return

    method = flow.request.method
    source = f"{method} {host}{path}"
    ctx.log.debug(f"[{_ts()}] >> {source}")

    # Extract IDs from URL
    _extract_ids_from_url(url, source)

    # Check Authorization header
    auth = flow.request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
        ctx.log.warn(f"[{_ts()}]    [FOUND] Bearer token ({len(token)} chars)")
        _save_token(token, f"Authorization header → {source}")
        return

    # Check /from-native/ URL pattern (primary token delivery method)
    if "/from-native/" in path:
        raw = path.split("/from-native/", 1)[1].split("?")[0]
        if raw and len(raw) > 40:
            ctx.log.warn(f"[{_ts()}]    [FOUND] /from-native/ token ({len(raw)} chars)")
            _save_token(raw, f"/from-native/ URL → {host}")
            return


def response(flow: http.HTTPFlow):
    host = flow.request.pretty_host
    path = flow.request.path
    url = flow.request.pretty_url

    if not _is_target(host):
        return

    # Extract IDs from URL (response confirms the resource exists)
    method = flow.request.method
    source = f"{method} {host}{path}"
    _extract_ids_from_url(url, source)

    # Check JSON responses for IDs
    ct = flow.response.headers.get("content-type", "")
    if "json" not in ct:
        return

    try:
        body = flow.response.content
        if not body:
            return
        data = json.loads(body)
        _scan_json(data, source=source)
    except Exception:
        pass


def _scan_json(data, path: str = "", source: str = ""):
    """Recursively scan JSON for tokens and ObjectIDs."""
    if isinstance(data, dict):
        for k, v in data.items():
            child_path = f"{path}.{k}" if path else k
            if isinstance(v, str):
                if _looks_like_token(v):
                    _save_token(v, f"JSON '{child_path}' in {source}")
                elif len(v) == 24 and re.fullmatch(r"[0-9a-f]{24}", v):
                    _guess_and_save_id(k, v, f"JSON '{child_path}' in {source}")
            elif isinstance(v, (dict, list)):
                _scan_json(v, child_path, source)
    elif isinstance(data, list):
        for i, v in enumerate(data):
            _scan_json(v, f"{path}[{i}]", source)


def _guess_and_save_id(key: str, value: str, source: str):
    """Guess ID type from JSON key name and save."""
    key_lower = key.lower()
    id_hints = {
        "installation_id": [
            "installationid", "installation_id", "idinstallation", "id_installation",
        ],
        "heat_pump_id": [
            "heatpumpid", "heat_pump_id", "deviceid", "device_id",
            "idheatpump", "id_heat_pump", "iddevice", "id_device",
        ],
    }
    for id_type, hints in id_hints.items():
        if key_lower in hints:
            _save_id(id_type, value, source)
            return


# ── WebSocket hooks (SignalR / Blazor navigation) ─────────────────────────────

def websocket_message(flow: http.HTTPFlow):
    """Intercept WebSocket messages — Blazor navigation contains IDs."""
    host = flow.request.pretty_host
    if not _is_target(host):
        return

    message = flow.websocket.messages[-1]
    is_text = message.type == 1

    content = None
    if is_text:
        content = (
            message.content.decode("utf-8", errors="replace")
            if isinstance(message.content, bytes)
            else message.content
        )
    else:
        try:
            content = (
                message.content.decode("utf-8", errors="replace")
                if isinstance(message.content, bytes)
                else str(message.content)
            )
        except Exception:
            return

    if not content:
        return

    direction = "C→S" if message.from_client else "S→C"

    # Look for navigation URLs with IDs
    nav_matches = NAV_URL_RE.findall(content)
    for segment, id_val in nav_matches:
        id_type = NAV_SEGMENT_TO_TYPE.get(segment)
        if not id_type:
            continue
        if found_ids.get(id_type) == id_val:
            continue
        ctx.log.warn(f"[{_ts()}]    [WS-NAV] /{segment}/{id_val} → {id_type}")
        _save_id(id_type, id_val, f"WebSocket navigation /{segment}/")

    # Look for tokens in the frame
    if "eyJ" in content or len(content) > 200:
        for match in re.finditer(
            r"eyJ[A-Za-z0-9_-]{2,}\.eyJ[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}", content
        ):
            token = match.group(0)
            _save_token(token, f"WebSocket frame {direction}")

    # Try to parse as JSON (SignalR text frames)
    parts = content.split("\x1e") if "\x1e" in content else [content]
    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            data = json.loads(part)
            _scan_json(data, source=f"WebSocket {direction}")
        except (json.JSONDecodeError, ValueError):
            pass
