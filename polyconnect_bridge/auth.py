"""Polyconnect native authentication — replaces the v1 mitmproxy capture flow.

Replicates the .NET MAUI app's signed-transaction wire format against
`auth.pool.mytech-connect.io`. See docs/native-login.md for the full protocol
breakdown. The protocol details live here; the lifecycle below wraps them in
the same `Credentials` shape the rest of the bridge already consumes, so
swapping CaptureManager → AuthManager is a one-line change in server.py.

Public API (parity with v1 CaptureManager):
  - mgr.credentials                  -> Credentials dataclass (.token, .installation_id,
                                        .heat_pump_id, .is_complete)
  - mgr.get_status() / .reset_credentials()
  - mgr.refresh()                    -> force a fresh login (new addition)
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Random import get_random_bytes

log = logging.getLogger("polyconnect.auth")

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA_DIR = Path(os.environ.get("POLYCONNECT_DATA_DIR", "/data"))
TERMINAL_FILE = DATA_DIR / "terminal.json"   # { terminal_id, terminal_transaction_key } — long-lived
SESSION_FILE = DATA_DIR / "session.json"     # { token, url, issued_at } — short-lived
IDS_FILE = DATA_DIR / "ids.json"             # { installation_id, heat_pump_id } — discovered/configured
LEGACY_TOKEN_FILE = DATA_DIR / "token.txt"   # v1 leftover; migrated on first run

# ── Protocol constants (from PolyconnectUserAppMaui/MauiProgram.cs) ──────────
BASE_URL = "https://auth.pool.mytech-connect.io"
AEID = "userApp_polytropic"
PUBLIC_TRANSACTION_KEY = "ZZuo8EMfc93KtDU745gvzw8DsWY0"
PRE_SALT = "zLT6DV"
POST_SALT = "NEEJ9S"

# .NET DateTime format strings (raw bytes are U+2019 / U+2018, NOT ASCII apostrophes —
# .NET treats them as literal chars; MM/yyyy/dd/HH/mm/ss are real specifiers).
RSQUO = "\u2019"
LSQUO = "\u2018"
PUBLIC_FMT = f"T{RSQUO}{LSQUO}MM{RSQUO}yyyy{RSQUO}-{LSQUO}dd{RSQUO}HH{RSQUO}:{RSQUO}mm{RSQUO}:{RSQUO}ss"
TERMINAL_FMT = f"T{RSQUO}{LSQUO}dd{RSQUO}HH{RSQUO}{LSQUO}MM{RSQUO}yyyy{RSQUO}-:{RSQUO}mm{RSQUO}:{RSQUO}ss"

# `key.Reverse().ToString()` in .NET returns the LINQ iterator type-name string —
# a developer bug both client and server share. The AES passphrase is constant.
DOTNET_BUG_KEY = "System.Linq.Enumerable+ReverseIterator`1[System.Char]"

# Heuristic: a session token is considered "stale" after this many seconds and
# the bridge will silently re-login on the next 401/expired response.
SESSION_TTL_SECONDS = 60 * 60 * 12  # 12h — conservative; servers may expire earlier


# ── .NET-compatible primitives ────────────────────────────────────────────────
_SPECIFIERS: list[tuple[str, str]] = [
    ("yyyy", "%Y"), ("MM", "%m"), ("dd", "%d"),
    ("HH", "%H"), ("mm", "%M"), ("ss", "%S"),
]


def _dotnet_format(time: dt.datetime, fmt: str) -> str:
    """Apply a .NET custom DateTime format string (invariant culture)."""
    out = []
    i = 0
    while i < len(fmt):
        matched = False
        for spec, strftime_spec in _SPECIFIERS:
            if fmt.startswith(spec, i):
                out.append(time.strftime(strftime_spec))
                i += len(spec)
                matched = True
                break
        if matched:
            continue
        # Everything else (including U+2018/U+2019, T, -, :) is output literal.
        out.append(fmt[i])
        i += 1
    return "".join(out)


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _sha512_hex(data: str) -> str:
    return hashlib.sha512(data.encode("utf-8")).hexdigest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _aes_encrypt(plaintext: str, passphrase: str) -> bytes:
    salt = get_random_bytes(16)
    iv = get_random_bytes(16)
    key = PBKDF2(passphrase, salt, dkLen=16, count=1000)  # default prf = HMAC-SHA1
    cipher = AES.new(key, AES.MODE_CBC, IV=iv)
    pad_len = 16 - (len(plaintext.encode("utf-8")) % 16)
    padded = plaintext.encode("utf-8") + bytes([pad_len] * pad_len)
    return base64.b64encode(salt + iv + cipher.encrypt(padded))


def _aes_decrypt(b64_str: str, passphrase: str) -> str:
    blob = base64.b64decode(b64_str)
    salt, iv, ct = blob[:16], blob[16:32], blob[32:]
    key = PBKDF2(passphrase, salt, dkLen=16, count=1000)
    cipher = AES.new(key, AES.MODE_CBC, IV=iv)
    padded = cipher.decrypt(ct)
    return padded[:-padded[-1]].decode("utf-8")


def _convert(value: Any) -> Any:
    """Newtonsoft.Json-compatible normalization: drop None, ISO datetime without tz."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(value, dict):
        return {k: _convert(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_convert(v) for v in value]
    return value


def _dumps(obj: Any) -> str:
    return json.dumps(_convert(obj), separators=(",", ":"))


def _build_protected_payload(payload_json: str, time: dt.datetime, key: str, fmt: str) -> str:
    formatted = _dotnet_format(time, fmt)
    time_print = _sha512_hex(formatted + key)
    protected = {"d": time, "tp": time_print, "sp": payload_json}
    plain = _dumps(protected)
    cipher_b64 = _aes_encrypt(plain, DOTNET_BUG_KEY)
    return _b64url(cipher_b64)


def _sign(encoded_payload: str, key: str) -> str:
    return _b64url(_sha512_hex(encoded_payload + key).encode("utf-8"))


def _make_public_signed(payload_obj: Any) -> dict:
    time = dt.datetime.now(dt.UTC).replace(microsecond=0, tzinfo=None)
    encoded = _build_protected_payload(_dumps(payload_obj), time, PUBLIC_TRANSACTION_KEY, PUBLIC_FMT)
    sig = _sign(encoded, PUBLIC_TRANSACTION_KEY)
    return {"tpv": 1, "psp": f"{sig}.{encoded}"}


def _make_terminal_signed(payload_obj: Any, terminal_id: str, terminal_key: str) -> dict:
    time = dt.datetime.now(dt.UTC).replace(microsecond=0, tzinfo=None)
    encoded = _build_protected_payload(_dumps(payload_obj), time, terminal_key, TERMINAL_FMT)
    sig = _sign(encoded, terminal_key)
    return {"tpv": 1, "psp": f"{sig}.{encoded}", "tid": terminal_id}


def _decode_envelope(envelope: dict) -> dict:
    """Decrypt the inner protected payload; returns the {d,tp,sp} dict."""
    sig, encoded = envelope["psp"].split(".", 1)
    cipher_b64 = _b64url_decode(encoded).decode("ascii")
    plain = _aes_decrypt(cipher_b64, DOTNET_BUG_KEY)
    return json.loads(plain)


# ── HTTP calls ────────────────────────────────────────────────────────────────
def _register_terminal_remote() -> tuple[str, str]:
    """POST /Irc/Terminal/RegisterTerminal. Returns (terminal_id, terminal_transaction_key)."""
    # Stable per-install device fingerprint (random uuid hashed; same shape the
    # mobile app uses for TerminalPrint).
    terminal = {
        "Manufacturer": "HomeAssistant",
        "Model": "ha-polyconnect-bridge",
        "OperatingSystem": 2,  # IngeliStd.Enums.OperatingSystem.Android (= ordinal 2)
        "OperatingSystemVersion": 14,
        "TerminalPrint": _b64url(hashlib.sha256(str(uuid.uuid4()).encode()).digest()),
    }
    envelope = _make_public_signed({"terminal": terminal})
    r = requests.post(f"{BASE_URL}/Irc/Terminal/RegisterTerminal",
                      data=_dumps(envelope),
                      headers={"Content-Type": "application/json"},
                      timeout=30)
    r.raise_for_status()
    inner = _decode_envelope(r.json())
    payload = json.loads(inner["sp"])
    if payload.get("s") != 100:  # TerminalRegisterResultState.Success = 100
        raise AuthError(f"RegisterTerminal returned state={payload.get('s')!r}")
    return payload["ti"], payload["ttk"]


def _login_remote(email: str, password: str, terminal_id: str, terminal_key: str) -> tuple[str, str]:
    """POST /Irc/Application/Login. Returns (token, url) on success."""
    pwd_hash = _sha256_hex(PRE_SALT + password + POST_SALT)
    args = {
        "aeid": AEID,
        "e": email.strip().lower(),
        "h": pwd_hash,
        "tid": terminal_id,
        "av": "5.3",
        "pn": "com.polytropic.pool",
    }
    envelope = _make_terminal_signed({"args": args}, terminal_id, terminal_key)
    r = requests.post(f"{BASE_URL}/Irc/Application/Login",
                      data=_dumps(envelope),
                      headers={"Content-Type": "application/json"},
                      timeout=30)
    r.raise_for_status()
    inner = _decode_envelope(r.json())
    payload = json.loads(inner["sp"])
    state = payload.get("s")
    if state != 0:  # AuthenticationResult.Success = 0
        raise AuthError(_describe_auth_state(state))
    return payload["t"], payload["url"]


_AUTH_STATES = {
    0: "Success", 1: "BadCredentials", 2: "UserDisabled", 3: "InactiveUser",
    4: "ApplicationNotAllowed", 5: "ApplicationEndpointInvalid",
    6: "CredentialScopeInvalid", 7: "InvalidRestrictedScopes",
}


def _describe_auth_state(state: int | None) -> str:
    return f"Login failed (state={state}: {_AUTH_STATES.get(state, 'Unknown')})"


# ── Exceptions ────────────────────────────────────────────────────────────────
class AuthError(Exception):
    """Raised when the auth API returns a non-success state."""


# ── Credentials snapshot (multi-pump aware) ───────────────────────────────────
@dataclass
class Credentials:
    token: str | None = None
    installation_id: str | None = None
    installation_name: str | None = None
    # Each pump = {"id": "<24-char hex>", "name": "<display name>"}.
    # The list shape supports multi-pump installations; index 0 is the "default"
    # pump (what legacy /status, /setpoint, ... aliases target).
    heat_pumps: list[dict] = field(default_factory=list)

    @property
    def heat_pump_id(self) -> str | None:
        """Back-compat: first pump's id, or None."""
        return self.heat_pumps[0]["id"] if self.heat_pumps else None

    @property
    def is_complete(self) -> bool:
        return bool(self.token and self.installation_id and self.heat_pumps)

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "installation_id": self.installation_id,
            "installation_name": self.installation_name,
            "heat_pumps": list(self.heat_pumps),
            # Back-compat aliases for older clients
            "heat_pump_id": self.heat_pump_id,
            "complete": self.is_complete,
        }


# ── Manager ──────────────────────────────────────────────────────────────────
class AuthManager:
    """Owns the auth state machine: terminal registration, login, refresh, IDs."""

    def __init__(self,
                 email: str | None = None,
                 password: str | None = None,
                 installation_id: str | None = None,
                 heat_pump_id: str | None = None) -> None:
        self._lock = threading.Lock()
        self._email = (email or os.environ.get("POLYCONNECT_EMAIL", "")).strip()
        self._password = (password or os.environ.get("POLYCONNECT_PASSWORD", "")).strip()
        self._cfg_installation = (installation_id
                                  or os.environ.get("POLYCONNECT_INSTALLATION_ID", "")).strip()
        self._cfg_heat_pump = (heat_pump_id
                               or os.environ.get("POLYCONNECT_HEAT_PUMP_ID", "")).strip()
        self._terminal_id: str | None = None
        self._terminal_key: str | None = None
        self._session_url: str | None = None
        self._session_issued_at: float = 0.0
        self.credentials = Credentials()
        self._last_error: str | None = None
        self._load_state()
        # Best-effort initial login if credentials are configured but no session yet.
        if self._email and self._password and not self.credentials.token:
            try:
                self._ensure_session()
            except Exception as e:
                log.warning("Initial login failed: %s", e)

    # ── persistence ───────────────────────────────────────────────────────────
    def _load_state(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        if TERMINAL_FILE.exists():
            try:
                t = json.loads(TERMINAL_FILE.read_text())
                self._terminal_id = t.get("terminal_id")
                self._terminal_key = t.get("terminal_transaction_key")
                if self._terminal_id:
                    log.info("Loaded terminal %s from %s", self._terminal_id, TERMINAL_FILE)
            except Exception as e:
                log.warning("Failed to load terminal state: %s", e)

        if SESSION_FILE.exists():
            try:
                s = json.loads(SESSION_FILE.read_text())
                self.credentials.token = s.get("token")
                self._session_url = s.get("url")
                self._session_issued_at = float(s.get("issued_at") or 0)
                if self.credentials.token:
                    log.info("Loaded session from %s (%d chars, age=%ds)",
                             SESSION_FILE, len(self.credentials.token),
                             int(time.time() - self._session_issued_at))
            except Exception as e:
                log.warning("Failed to load session: %s", e)

        if IDS_FILE.exists():
            try:
                d = json.loads(IDS_FILE.read_text())
                self.credentials.installation_id = d.get("installation_id") or None
                self.credentials.installation_name = d.get("installation_name") or None
                # Two on-disk shapes are supported:
                #   v2.0:  {"installation_id": "...", "heat_pump_id": "<one>"}
                #   v2.1+: {"installation_id": "...", "installation_name": "...",
                #           "heat_pumps": [{"id","name"}, ...]}
                if "heat_pumps" in d and isinstance(d["heat_pumps"], list):
                    self.credentials.heat_pumps = [
                        {"id": p["id"], "name": p.get("name") or f"Heat pump {i+1}"}
                        for i, p in enumerate(d["heat_pumps"]) if p.get("id")
                    ]
                elif d.get("heat_pump_id"):
                    # Migrate single-pump shape -> single-entry list
                    self.credentials.heat_pumps = [
                        {"id": d["heat_pump_id"], "name": "Heat pump"}
                    ]
                    log.info("Migrated single-pump ids.json -> multi-pump list shape")
            except Exception as e:
                log.warning("Failed to load IDs: %s", e)

        # Config-supplied IDs win over persisted ones (user can override via add-on options).
        if self._cfg_installation:
            self.credentials.installation_id = self._cfg_installation
        if self._cfg_heat_pump:
            # User-pinned single pump via env -> replace the list with a single entry.
            self.credentials.heat_pumps = [
                {"id": self._cfg_heat_pump, "name": "Heat pump"}
            ]

        # v1 → v2 migration: a leftover token.txt from mitm capture stays usable
        # until the session is refreshed. We do NOT delete it — refresh writes
        # session.json which takes precedence on next load.
        if not self.credentials.token and LEGACY_TOKEN_FILE.exists():
            legacy = LEGACY_TOKEN_FILE.read_text().strip()
            if legacy:
                self.credentials.token = legacy
                self._session_url = f"https://polytropic.user-app.pool.mytech-connect.io/from-native"
                log.info("Loaded legacy mitm-captured token (will be replaced on next refresh)")

    def _save_terminal(self) -> None:
        TERMINAL_FILE.write_text(json.dumps({
            "terminal_id": self._terminal_id,
            "terminal_transaction_key": self._terminal_key,
        }, indent=2) + "\n")

    def _save_session(self) -> None:
        SESSION_FILE.write_text(json.dumps({
            "token": self.credentials.token,
            "url": self._session_url,
            "issued_at": self._session_issued_at,
        }, indent=2) + "\n")

    def _save_ids(self) -> None:
        IDS_FILE.write_text(json.dumps({
            "installation_id": self.credentials.installation_id,
            "installation_name": self.credentials.installation_name,
            "heat_pumps": list(self.credentials.heat_pumps),
        }, indent=2) + "\n")

    # ── core flow ─────────────────────────────────────────────────────────────
    def _ensure_terminal(self) -> None:
        if self._terminal_id and self._terminal_key:
            return
        log.info("Registering new terminal with auth.pool.mytech-connect.io …")
        self._terminal_id, self._terminal_key = _register_terminal_remote()
        self._save_terminal()
        log.info("Terminal registered: %s", self._terminal_id)

    def _ensure_session(self) -> None:
        if not self._email or not self._password:
            raise AuthError("POLYCONNECT_EMAIL / POLYCONNECT_PASSWORD not configured")
        self._ensure_terminal()
        log.info("Logging in as %s …", self._email)
        token, url = _login_remote(self._email, self._password,
                                   self._terminal_id, self._terminal_key)  # type: ignore[arg-type]
        self.credentials.token = token
        self._session_url = url
        self._session_issued_at = time.time()
        self._last_error = None
        self._save_session()
        log.info("Session acquired (%d chars, url=%s)", len(token), url)

    # ── public API ────────────────────────────────────────────────────────────
    def get_app_url(self) -> str | None:
        """Returns `<url>/<token>` ready for Playwright to load, or None if unauth."""
        with self._lock:
            if not self.credentials.token or not self._session_url:
                return None
            return f"{self._session_url.rstrip('/')}/{self.credentials.token}"

    def refresh(self) -> dict:
        """Force a new login (keeps the same terminal). Idempotent under lock."""
        with self._lock:
            try:
                self._ensure_session()
                return {"ok": True, "credentials": self.credentials.to_dict()}
            except Exception as e:
                self._last_error = str(e)
                log.warning("Refresh failed: %s", e)
                return {"ok": False, "error": str(e)}

    def set_credentials(self, email: str, password: str,
                        installation_id: str | None = None,
                        heat_pump_id: str | None = None) -> dict:
        """Update credentials at runtime (e.g. from the bridge UI) and re-login.
        Passing a single `heat_pump_id` pins the list to that one pump (single-pump
        mode); pass None to leave the discovered list untouched."""
        with self._lock:
            self._email = (email or "").strip()
            self._password = (password or "").strip()
            if installation_id is not None:
                self.credentials.installation_id = installation_id.strip() or None
            if heat_pump_id is not None:
                pinned = heat_pump_id.strip()
                self.credentials.heat_pumps = (
                    [{"id": pinned, "name": "Heat pump"}] if pinned else []
                )
            self._save_ids()
            try:
                self._ensure_session()
                return {"ok": True, "credentials": self.credentials.to_dict()}
            except Exception as e:
                self._last_error = str(e)
                return {"ok": False, "error": str(e)}

    def set_pumps(self, installation_id: str | None,
                  heat_pumps: list[dict],
                  installation_name: str | None = None) -> None:
        """Persist discovered pump list without touching session/credentials.
        `heat_pumps` items must be dicts with at least an `id` key; `name`
        defaults to 'Heat pump <N>' if absent. Used by Playwright auto-discovery."""
        with self._lock:
            normalized: list[dict] = []
            for i, p in enumerate(heat_pumps or []):
                pid = (p or {}).get("id")
                if not pid:
                    continue
                normalized.append({
                    "id": pid,
                    "name": (p.get("name") or f"Heat pump {i+1}").strip() or f"Heat pump {i+1}",
                })
            changed = False
            if installation_id and installation_id != self.credentials.installation_id:
                self.credentials.installation_id = installation_id
                changed = True
            if installation_name is not None:
                clean = installation_name.strip() or None
                if clean != self.credentials.installation_name:
                    self.credentials.installation_name = clean
                    changed = True
            # Compare by (id, name) tuples — preserve order.
            old = [(p["id"], p["name"]) for p in self.credentials.heat_pumps]
            new = [(p["id"], p["name"]) for p in normalized]
            if old != new:
                self.credentials.heat_pumps = normalized
                changed = True
            if changed:
                self._save_ids()
                log.info("Persisted installation=%s (%r) with %d pump(s): %s",
                         self.credentials.installation_id,
                         self.credentials.installation_name,
                         len(normalized),
                         [(p["id"], p["name"]) for p in normalized])

    def reset_credentials(self) -> None:
        """Clear ALL persisted state. Forces a fresh terminal registration next time."""
        with self._lock:
            self.credentials = Credentials()
            self._terminal_id = None
            self._terminal_key = None
            self._session_url = None
            self._session_issued_at = 0.0
            for f in (TERMINAL_FILE, SESSION_FILE, IDS_FILE, LEGACY_TOKEN_FILE):
                if f.exists():
                    f.unlink()
            log.info("Auth state reset")

    def get_status(self) -> dict:
        """Bridge status payload. Schema kept close to v1 CaptureManager for the UI."""
        with self._lock:
            return {
                "email": self._email,
                "email_configured": bool(self._email),
                "password_configured": bool(self._password),
                "terminal_registered": bool(self._terminal_id),
                "terminal_id": self._terminal_id,
                "session_age_seconds": int(time.time() - self._session_issued_at) if self._session_issued_at else None,
                "session_stale": self._session_issued_at != 0 and (time.time() - self._session_issued_at) > SESSION_TTL_SECONDS,
                "last_error": self._last_error,
                "credentials": self.credentials.to_dict(),
            }


# ── CLI smoke test (POC parity) ───────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    mgr = AuthManager()
    res = mgr.refresh()
    print(json.dumps({"refresh": res, "status": mgr.get_status(), "app_url": mgr.get_app_url()}, indent=2))
