"""
Polyconnect native login POC.

Reverse-engineered from the decompiled .NET MAUI app:
  - PolyconnectUserAppMaui/MauiProgram.cs           -> AEID, public key, salts, base URL
  - IngeliStdMaui/Services/BlazorApplicationService -> password hash, Login()
  - IngeliStd/Cryptography/Encryption.cs            -> AES-CBC-PKCS7 + PBKDF2-SHA1
  - IngeliStdSecurity/Transaction/                  -> signed-transaction envelope
  - IngeliStdSecurity/Security/SecurityHelper.cs    -> SHA512 + Base64URL

Wire format for a request:
  Plain payload (JSON, Newtonsoft-style PascalCase keys, NullValueHandling=Ignore)
    -> wrapped in {"d":<iso-date>,"tp":<sha512(formatted-date + key)>,"sp":<plain-json>}
    -> AES-CBC-PKCS7 encrypt that JSON with passphrase derived from "key.Reverse().ToString()"
       (which in .NET returns the literal type-name string of the LINQ iterator -- a
       hilarious developer bug; both client and server share it so it works)
    -> Base64URL-encode the cipher (urlsafe '-' '_' WITHOUT '=' padding)
    -> compute "signature" = Base64URL(SHA512_hex(encoded_payload + key))
    -> final blob = signature + "." + encoded_payload
    -> wrap in:
         public:    {"tpv":1,"psp":blob}
         terminal:  {"tpv":1,"psp":blob,"tid":<terminal_id>}

The server validates the signature and timestamp, decrypts, and processes the inner JSON.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from Crypto.Cipher import AES
from Crypto.Protocol.KDF import PBKDF2
from Crypto.Random import get_random_bytes


# ── Constants (from MauiProgram.cs) ───────────────────────────────────────────
BASE_URL = "https://auth.pool.mytech-connect.io"
AEID = "userApp_polytropic"
PUBLIC_TRANSACTION_KEY = "ZZuo8EMfc93KtDU745gvzw8DsWY0"
PRE_SALT = "zLT6DV"
POST_SALT = "NEEJ9S"

# .NET DateTime format strings (raw bytes confirm Unicode curly quotes, not ASCII)
# These are passed to `time.SetUtc():<format>` interpolation.
# In .NET custom DateTime format:
#   - Unrecognized chars (T, U+2018, U+2019, -) are literal output
#   - MM, yyyy, dd, HH, mm, ss are standard specifiers
#   - `:` is the time separator (locale-dependent, ":" in invariant/most cultures)
#   - `-` is the date separator (locale-dependent; ":" or "/" -- see below)
# We try the invariant-culture interpretation first.
RSQUO = "\u2019"  # ’
LSQUO = "\u2018"  # ‘
PUBLIC_FMT = f"T{RSQUO}{LSQUO}MM{RSQUO}yyyy{RSQUO}-{LSQUO}dd{RSQUO}HH{RSQUO}:{RSQUO}mm{RSQUO}:{RSQUO}ss"
TERMINAL_FMT = f"T{RSQUO}{LSQUO}dd{RSQUO}HH{RSQUO}{LSQUO}MM{RSQUO}yyyy{RSQUO}-:{RSQUO}mm{RSQUO}:{RSQUO}ss"

# The .Reverse().ToString() developer bug: in modern .NET,
# `Enumerable.Reverse(string).ToString()` returns the LINQ iterator's type-name
# (no ToString() override -> falls back to Object.ToString()).
# This is the constant passphrase used for AES-PBKDF2.
DOTNET_BUG_KEY = "System.Linq.Enumerable+ReverseIterator`1[System.Char]"


# ── .NET custom-format DateTime replicator ────────────────────────────────────
# Recognized specifiers (longest match wins). All other chars output literal.
_SPECIFIERS: list[tuple[str, str]] = [
    ("yyyy", "%Y"),
    ("MM",   "%m"),
    ("dd",   "%d"),
    ("HH",   "%H"),
    ("mm",   "%M"),
    ("ss",   "%S"),
]


def dotnet_format(time: dt.datetime, fmt: str, date_sep: str = "-", time_sep: str = ":") -> str:
    """Apply a .NET custom DateTime format string to a UTC datetime."""
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
        ch = fmt[i]
        if ch == ":":
            out.append(time_sep)
        elif ch == "/":
            out.append(date_sep)
        else:
            # everything else (including U+2018, U+2019, T, -, etc.) is literal
            out.append(ch)
        i += 1
    return "".join(out)


# ── Crypto primitives ─────────────────────────────────────────────────────────
def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def sha512_hex(data: str) -> str:
    return hashlib.sha512(data.encode("utf-8")).hexdigest()


def b64url(data: bytes) -> str:
    """Microsoft.IdentityModel.Tokens Base64UrlEncoder: standard base64url, NO padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def aes_encrypt(plaintext: str, passphrase: str, salt: bytes | None = None, iv: bytes | None = None) -> bytes:
    """Replicate IngeliStd.Cryptography.Encryption.Encrypt:
      salt = 16 random bytes
      iv   = 16 random bytes
      key  = PBKDF2-SHA1(passphrase, salt, 1000 iters, 16 bytes)
      cipher = AES-128-CBC + PKCS7, output = salt || iv || ciphertext
      result = base64.b64encode(output)  -- NOTE: STANDARD base64 here, not url-safe
    """
    if salt is None:
        salt = get_random_bytes(16)
    if iv is None:
        iv = get_random_bytes(16)
    key = PBKDF2(passphrase, salt, dkLen=16, count=1000)  # default prf = HMAC-SHA1
    cipher = AES.new(key, AES.MODE_CBC, IV=iv)
    # PKCS7 padding
    pad_len = 16 - (len(plaintext.encode("utf-8")) % 16)
    padded = plaintext.encode("utf-8") + bytes([pad_len] * pad_len)
    ct = cipher.encrypt(padded)
    blob = salt + iv + ct
    # The .NET code returns Convert.ToBase64String() -> standard base64 WITH padding.
    return base64.b64encode(blob)


def aes_decrypt(b64_str: str, passphrase: str) -> str:
    blob = base64.b64decode(b64_str)
    salt, iv, ct = blob[:16], blob[16:32], blob[32:]
    key = PBKDF2(passphrase, salt, dkLen=16, count=1000)
    cipher = AES.new(key, AES.MODE_CBC, IV=iv)
    padded = cipher.decrypt(ct)
    pad_len = padded[-1]
    return padded[:-pad_len].decode("utf-8")


# ── Newtonsoft-like JSON serialization ────────────────────────────────────────
# Matches:
#   - DateFormatString = "yyyy-MM-ddTHH:mm:ss"  (no timezone, no millis)
#   - NullValueHandling = Ignore               (drop None values)
def _convert(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(value, dict):
        return {k: _convert(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_convert(v) for v in value]
    return value


def dumps(obj: Any) -> str:
    """Newtonsoft-style serialize: drop None, ISO datetime without tz, compact."""
    return json.dumps(_convert(obj), separators=(",", ":"))


# ── Protected-payload + transaction envelope ─────────────────────────────────
def build_protected_payload(payload_json: str, time: dt.datetime, key: str, fmt: str) -> str:
    """Build the inner {d,tp,sp} protected payload, AES-encrypt it, return base64url."""
    formatted = dotnet_format(time, fmt)
    time_print = sha512_hex(formatted + key)
    protected = {
        "d": time,         # serialized with the Newtonsoft DateFormatString
        "tp": time_print,
        "sp": payload_json,
    }
    plain = dumps(protected)
    # AES-encrypt with the buggy passphrase (.NET's `key.Reverse().ToString()`)
    cipher_b64 = aes_encrypt(plain, DOTNET_BUG_KEY)
    # Then Base64Url-encode the WHOLE base64-string (yes, double-encoded -- that's
    # what `Base64UrlEncoder.Encode(Encryption.Encrypt(...))` does because Encrypt
    # already returns a base64 string).
    return b64url(cipher_b64)


def sign_payload(encoded_payload: str, key: str) -> str:
    """Base64Url(SHA512_hex(encoded_payload + key)) -- the signature."""
    return b64url(sha512_hex(encoded_payload + key).encode("utf-8"))


def make_public_signed(payload_obj: Any, time: dt.datetime | None = None) -> dict:
    """Build a {tpv,psp} envelope signed with the PublicTransactionKey."""
    time = time or dt.datetime.now(dt.UTC).replace(microsecond=0, tzinfo=None)
    inner_json = dumps(payload_obj)
    encoded = build_protected_payload(inner_json, time, PUBLIC_TRANSACTION_KEY, PUBLIC_FMT)
    sig = sign_payload(encoded, PUBLIC_TRANSACTION_KEY)
    return {"tpv": 1, "psp": f"{sig}.{encoded}"}


def make_terminal_signed(payload_obj: Any, terminal_id: str, terminal_key: str,
                          time: dt.datetime | None = None) -> dict:
    """Build a {tpv,psp,tid} envelope signed with the TerminalTransactionKey."""
    time = time or dt.datetime.now(dt.UTC).replace(microsecond=0, tzinfo=None)
    inner_json = dumps(payload_obj)
    encoded = build_protected_payload(inner_json, time, terminal_key, TERMINAL_FMT)
    sig = sign_payload(encoded, terminal_key)
    return {"tpv": 1, "psp": f"{sig}.{encoded}", "tid": terminal_id}


def decode_public_signed(envelope: dict) -> dict:
    psp = envelope["psp"]
    sig, encoded = psp.split(".", 1)
    cipher_b64 = b64url_decode(encoded).decode("ascii")
    plain = aes_decrypt(cipher_b64, DOTNET_BUG_KEY)
    return json.loads(plain)


def decode_terminal_signed(envelope: dict, terminal_key: str) -> dict:
    return decode_public_signed(envelope)  # decryption path is identical


# ── High-level API calls ─────────────────────────────────────────────────────
@dataclass
class Terminal:
    Manufacturer: str = "Google"
    Model: str = "Pixel 7"
    OperatingSystem: int = 2     # Android = 2 (best guess; enum order in IngeliStd.Enums)
    OperatingSystemVersion: int = 14
    TerminalPrint: str = field(default_factory=lambda: b64url(hashlib.sha256(
        str(uuid.uuid4()).encode()).digest()))


def register_terminal(terminal: Terminal | None = None) -> dict:
    """POST /Irc/Terminal/RegisterTerminal -- public-signed transaction.
    Returns {state, terminal_id, terminal_transaction_key}.
    """
    terminal = terminal or Terminal()
    args = {"terminal": terminal.__dict__}
    # Newtonsoft default contract resolver keeps property names as-is (PascalCase).
    # But the JsonProperty attribute on TerminalRegisterTerminalArgs.Terminal is "terminal".
    # The Terminal class fields have no JsonProperty attrs, so they stay PascalCase.

    envelope = make_public_signed(args)
    body = dumps(envelope)
    r = requests.post(f"{BASE_URL}/Irc/Terminal/RegisterTerminal",
                      data=body,
                      headers={"Content-Type": "application/json"},
                      timeout=30)
    if not r.ok:
        raise RuntimeError(f"RegisterTerminal HTTP {r.status_code}: {r.text[:500]}")

    resp_env = r.json()
    inner = decode_public_signed(resp_env)
    return inner


def login(email: str, password: str, terminal_id: str, terminal_key: str) -> dict:
    """POST /Irc/Application/Login -- terminal-signed transaction."""
    pwd_hash = sha256_hex(PRE_SALT + password + POST_SALT)
    login_args = {
        "aeid": AEID,
        "e": email.strip().lower(),
        "h": pwd_hash,
        "tid": terminal_id,
        "av": "5.3",                  # ApplicationVersion
        "pn": "com.polytropic.pool",  # PackageName (best guess from APK metadata)
    }
    body_inner = {"args": login_args}
    envelope = make_terminal_signed(body_inner, terminal_id, terminal_key)
    body = dumps(envelope)

    r = requests.post(f"{BASE_URL}/Irc/Application/Login",
                      data=body,
                      headers={"Content-Type": "application/json"},
                      timeout=30)
    if not r.ok:
        raise RuntimeError(f"Login HTTP {r.status_code}: {r.text[:500]}")

    resp_env = r.json()
    inner = decode_terminal_signed(resp_env, terminal_key)
    return inner


# ── Driver ────────────────────────────────────────────────────────────────────
def _load_env() -> tuple[str, str]:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    email = os.environ.get("POLYCONNECT_EMAIL")
    password = os.environ.get("POLYCONNECT_PASSWORD")
    if env_path.exists() and (not email or not password):
        for line in env_path.read_text().splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            k, _, v = line.strip().partition("=")
            v = v.strip().strip('"').strip("'")
            if k == "POLYCONNECT_EMAIL" and not email:
                email = v
            elif k == "POLYCONNECT_PASSWORD" and not password:
                password = v
    if not email or not password:
        sys.exit("Missing POLYCONNECT_EMAIL / POLYCONNECT_PASSWORD")
    return email, password


def main() -> None:
    print("─" * 70)
    print("Polyconnect native login POC")
    print("─" * 70)

    # Demo: print what the format produces
    now = dt.datetime(2026, 6, 30, 17, 30, 45)
    print(f"DateTime format demo (UTC = {now.isoformat()}):")
    print(f"  public_fmt  = {dotnet_format(now, PUBLIC_FMT)!r}")
    print(f"  terminal_fmt= {dotnet_format(now, TERMINAL_FMT)!r}")
    print(f"  AES passphrase (.NET bug) = {DOTNET_BUG_KEY!r}")
    print()

    # Step 1: register terminal
    print("[1] POST /Irc/Terminal/RegisterTerminal …")
    try:
        result = register_terminal()
        print(f"    raw response: {result!r}")
        inner = result.get("sp") or result
        if isinstance(inner, str):
            inner = json.loads(inner)
        print(f"    state={inner.get('s')}, terminal_id={inner.get('ti')}, ttk={inner.get('ttk')}")
        terminal_id = inner.get("ti")
        terminal_key = inner.get("ttk")
        if not terminal_id or not terminal_key:
            sys.exit("RegisterTerminal returned no terminal credentials.")
    except Exception as e:
        print(f"    FAILED: {e}")
        sys.exit(2)
    print()

    # Step 2: login
    email, password = _load_env()
    print(f"[2] POST /Irc/Application/Login as {email} …")
    try:
        result = login(email, password, terminal_id, terminal_key)
        print(f"    raw response: {result!r}")
        inner = result.get("sp") or result
        if isinstance(inner, str):
            inner = json.loads(inner)
        print(f"    state={inner.get('s')}, token={inner.get('t', '')[:60]!r}…, url={inner.get('url')}")
    except Exception as e:
        print(f"    FAILED: {e}")
        sys.exit(3)


if __name__ == "__main__":
    main()
