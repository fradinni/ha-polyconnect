#!/usr/bin/env python3
"""
Polyconnect JWT Capture Tool
=============================
Interactive guide to capture your JWT token from the Polyconnect iOS app
using mitmproxy as an HTTPS proxy on your local network.

Usage:
    python3 get-jwt.py

Requirements:
    pip install mitmproxy requests
"""

import os
import sys
import time
import json
import socket
import signal
import threading
import subprocess
import textwrap
import shutil
from pathlib import Path

# ─── Colors ───────────────────────────────────────────────────────────────────

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"

def bold(s):   return f"{C.BOLD}{s}{C.RESET}"
def green(s):  return f"{C.GREEN}{s}{C.RESET}"
def yellow(s): return f"{C.YELLOW}{s}{C.RESET}"
def red(s):    return f"{C.RED}{s}{C.RESET}"
def cyan(s):   return f"{C.CYAN}{s}{C.RESET}"
def dim(s):    return f"{C.DIM}{s}{C.RESET}"

# ─── Config ───────────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent.resolve()
JWT_FILE     = SCRIPT_DIR / "captured_token.txt"
MITM_SCRIPT  = SCRIPT_DIR / "mitm_addon.py"
CERT_DIR     = Path.home() / ".mitmproxy"
CERT_FILE    = CERT_DIR / "mitmproxy-ca-cert.pem"
PROXY_PORT   = 8080
CERT_PORT    = 8888

# ─── Helpers ──────────────────────────────────────────────────────────────────

def clear():
    os.system("clear")

def header(title: str):
    width = 62
    print()
    print(f"{C.CYAN}{'─' * width}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  {title}{C.RESET}")
    print(f"{C.CYAN}{'─' * width}{C.RESET}")
    print()

def step(n: int, total: int, title: str):
    print(f"\n{C.BOLD}{C.BLUE}[{n}/{total}]{C.RESET} {C.BOLD}{title}{C.RESET}")
    print(f"      {C.DIM}{'─' * 50}{C.RESET}")

def info(msg: str):
    print(f"  {C.CYAN}ℹ{C.RESET}  {msg}")

def ok(msg: str):
    print(f"  {C.GREEN}✓{C.RESET}  {msg}")

def warn(msg: str):
    print(f"  {C.YELLOW}⚠{C.RESET}  {msg}")

def err(msg: str):
    print(f"  {C.RED}✗{C.RESET}  {msg}")

def instruction(msg: str):
    print(f"  {C.YELLOW}→{C.RESET}  {msg}")

def code(cmd: str):
    print(f"\n      {C.DIM}${C.RESET} {C.WHITE}{cmd}{C.RESET}")

def box(lines: list, color=C.CYAN):
    width = max(len(l) for l in lines) + 4
    print(f"\n  {color}┌{'─' * width}┐{C.RESET}")
    for line in lines:
        pad = width - len(line) - 2
        print(f"  {color}│{C.RESET}  {C.BOLD}{line}{C.RESET}{' ' * pad}  {color}│{C.RESET}")
    print(f"  {color}└{'─' * width}┘{C.RESET}\n")

def wait_enter(prompt="  Press Enter to continue..."):
    try:
        input(f"\n{C.DIM}{prompt}{C.RESET}")
    except (KeyboardInterrupt, EOFError):
        print("\n\nAborted.")
        sys.exit(0)

def get_local_ip() -> str:
    """Get the machine's LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "192.168.x.x"

def check_dependency(name: str) -> bool:
    return shutil.which(name) is not None or (Path.home() / ".local" / "bin" / name).exists()

def find_mitmdump() -> str | None:
    for candidate in ["mitmdump", str(Path.home() / ".local" / "bin" / "mitmdump")]:
        if shutil.which(candidate) or Path(candidate).exists():
            return candidate
    return None

def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0

# ─── mitmproxy addon (written to disk) ────────────────────────────────────────

ADDON_CODE = r'''
"""
Polyconnect JWT extractor — mitmproxy addon.
Auto-captures the JWT token from Polyconnect app traffic.

Logs every proxied request so you can see what's flowing through.
"""
from mitmproxy import http, ctx
import json
import datetime

JWT_FILE = "PLACEHOLDER_JWT_FILE"
found = set()

TARGET_HOSTS = ("mytech-connect.io", "polytropic")

def _ts():
    return datetime.datetime.now().strftime("%H:%M:%S")

def _save(token: str, source: str):
    if token in found:
        return
    found.add(token)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(JWT_FILE, "w") as f:
        f.write(token)
    with open(JWT_FILE + ".info", "w") as f:
        f.write("# Polyconnect JWT Token\n")
        f.write(f"# Captured : {ts}\n")
        f.write(f"# Source   : {source}\n\n")
        f.write(token + "\n")
    ctx.log.warn("\n" + "="*60)
    ctx.log.warn("  JWT TOKEN CAPTURED!")
    ctx.log.warn(f"  Source : {source}")
    ctx.log.warn(f"  Token  : {token[:80]}...")
    ctx.log.warn(f"  Saved  : {JWT_FILE}")
    ctx.log.warn("="*60 + "\n")

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

def _scan_json_for_tokens(data, path=""):
    results = []
    if isinstance(data, dict):
        for k, v in data.items():
            child_path = f"{path}.{k}" if path else k
            if isinstance(v, str) and _looks_like_token(v):
                results.append((child_path, v))
            elif isinstance(v, (dict, list)):
                results.extend(_scan_json_for_tokens(v, child_path))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            results.extend(_scan_json_for_tokens(v, f"{path}[{i}]"))
    return results

def request(flow: http.HTTPFlow):
    host = flow.request.pretty_host
    method = flow.request.method
    path = flow.request.path
    ctx.log.info(f"[{_ts()}] >> {method} {host}{path[:120]}")
    if not _is_target(host):
        return
    ctx.log.info(f"[{_ts()}]    [TARGET] checking request...")
    auth = flow.request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
        ctx.log.warn(f"[{_ts()}]    [FOUND] Bearer token in Authorization header ({len(token)} chars)")
        _save(token, f"Authorization header -> {host}{path}")
        return
    if "/from-native/" in path:
        raw = path.split("/from-native/", 1)[1].split("?")[0]
        ctx.log.warn(f"[{_ts()}]    [FOUND] /from-native/ token in URL path ({len(raw)} chars)")
        _save(raw, f"/from-native/ URL path -> {host}")
        return
    ctx.log.info(f"[{_ts()}]    no token in request")

def response(flow: http.HTTPFlow):
    host = flow.request.pretty_host
    method = flow.request.method
    path = flow.request.path
    status = flow.response.status_code
    ctx.log.info(f"[{_ts()}] << {method} {host}{path[:80]}  ->  HTTP {status}")
    if not _is_target(host):
        return
    ct = flow.response.headers.get("content-type", "")
    ctx.log.info(f"[{_ts()}]    [TARGET] Content-Type: {ct!r}")
    if "json" not in ct:
        ctx.log.info(f"[{_ts()}]    skipping (not JSON)")
        return
    try:
        body = flow.response.content
        ctx.log.info(f"[{_ts()}]    response body ({len(body)} bytes):")
        ctx.log.info(f"[{_ts()}]    {body.decode('utf-8', errors='replace')}")
        data = json.loads(body)
        candidates = _scan_json_for_tokens(data)
        if not candidates:
            ctx.log.info(f"[{_ts()}]    no token-shaped values found in JSON")
            return
        for field_path, value in candidates:
            ctx.log.warn(f"[{_ts()}]    [FOUND] token candidate in field '{field_path}' ({len(value)} chars): {value[:60]}...")
            _save(value, f"JSON field '{field_path}' in response -> {host}{path}")
    except Exception as e:
        ctx.log.warn(f"[{_ts()}]    failed to parse response JSON: {e}")
        ctx.log.info(f"[{_ts()}]    raw body: {flow.response.content[:500]}")
'''

# ─── Steps ────────────────────────────────────────────────────────────────────

def step_check_deps():
    header("Step 1 — Checking dependencies")

    mitmdump = find_mitmdump()
    has_requests = False
    try:
        import requests  # noqa
        has_requests = True
    except ImportError:
        pass

    if mitmdump:
        ok(f"mitmproxy found: {mitmdump}")
    else:
        err("mitmproxy not found")
        print()
        warn("Install it with:")
        code("pip install mitmproxy")
        print()
        answer = input("  Install now? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            subprocess.run([sys.executable, "-m", "pip", "install", "--user", "mitmproxy"], check=True)
            ok("mitmproxy installed")
        else:
            err("Cannot continue without mitmproxy. Exiting.")
            sys.exit(1)

    if has_requests:
        ok("requests library found")
    else:
        warn("requests not found — installing...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "requests"], check=True)
        ok("requests installed")

    wait_enter()


def step_generate_cert():
    header("Step 2 — Generating mitmproxy CA certificate")

    if CERT_FILE.exists():
        ok(f"Certificate already exists: {CERT_FILE}")
        info("If you've never installed it on your iPhone, continue to the next step.")
    else:
        info("Generating certificate by starting mitmproxy briefly...")
        mitmdump = find_mitmdump()
        proc = subprocess.Popen(
            [mitmdump, "--listen-port", str(PROXY_PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(2)
        proc.terminate()
        proc.wait()

        if CERT_FILE.exists():
            ok(f"Certificate generated: {CERT_FILE}")
        else:
            err("Failed to generate certificate. Try running mitmdump manually once.")
            sys.exit(1)

    # Copy cert to script dir for easy access
    dest = SCRIPT_DIR / "mitmproxy-ca-cert.pem"
    import shutil as _sh
    _sh.copy2(CERT_FILE, dest)
    ok(f"Certificate copied to: {dest}")

    wait_enter()


def step_serve_cert(local_ip: str):
    header("Step 3 — Install certificate on iPhone")

    cert_url = f"http://{local_ip}:{CERT_PORT}/mitmproxy-ca-cert.pem"

    # Start HTTP server to serve the cert
    server_proc = None
    try:
        server_proc = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(CERT_PORT),
             "--directory", str(SCRIPT_DIR)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        ok(f"Certificate server started on port {CERT_PORT}")
    except Exception as e:
        warn(f"Could not start cert server: {e}")
        warn(f"Manually copy {CERT_FILE} to your iPhone via AirDrop or email.")

    print()
    box([
        "On your iPhone:",
        "",
        f"1. Open Safari and go to:",
        f"   {cert_url}",
        "",
        "2. Tap 'Allow' to download the profile",
        "",
        "3. Go to: Settings → General",
        "   → VPN & Device Management",
        "   → mitmproxy → Install → Install",
        "",
        "4. Go to: Settings → General → About",
        "   → Certificate Trust Settings",
        "   → Enable 'mitmproxy' → Continue",
    ], color=C.YELLOW)

    wait_enter("  Press Enter once you've installed the certificate...")

    if server_proc:
        server_proc.terminate()
        server_proc.wait()


def step_configure_proxy(local_ip: str):
    header("Step 4 — Configure iPhone WiFi proxy")

    box([
        "On your iPhone:",
        "",
        "1. Settings → WiFi",
        "2. Tap the (i) next to your WiFi network",
        "3. Scroll down → HTTP Proxy → Manual",
        "",
        f"   Server:  {local_ip}",
        f"   Port:    {PROXY_PORT}",
        "   Auth:    Off",
        "",
        "4. Tap Save (top right)",
    ], color=C.BLUE)

    wait_enter("  Press Enter once the proxy is configured...")


def step_capture(local_ip: str):
    header("Step 5 — Capture JWT token")

    mitmdump = find_mitmdump()

    # Write the addon script (strip leading newline from raw string)
    addon_code = ADDON_CODE.lstrip("\n").replace("PLACEHOLDER_JWT_FILE", str(JWT_FILE))
    MITM_SCRIPT.write_text(addon_code)

    print()
    info("Starting mitmproxy proxy on port 8080...")
    info("Waiting for Polyconnect app traffic...")
    print()
    box([
        "On your iPhone:",
        "",
        "1. Open the Polyconnect app",
        "2. Log in with your email and password",
        "3. Wait for the app to load your data",
        "",
        "The JWT will be captured automatically below.",
    ], color=C.GREEN)

    # Start mitmdump
    cmd = [
        mitmdump,
        "--listen-port", str(PROXY_PORT),
        "--ssl-insecure",
        "-v",
        "-s", str(MITM_SCRIPT),
    ]

    print(f"  {dim('Running:')} {dim(' '.join(cmd))}\n")

    proc = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr)

    # Poll for the JWT file
    print(f"  {C.DIM}Listening... (Ctrl+C to stop){C.RESET}\n")
    try:
        while proc.poll() is None:
            if JWT_FILE.exists() and JWT_FILE.stat().st_size > 0:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n\n  Stopping proxy...")

    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()

    return JWT_FILE.exists() and JWT_FILE.stat().st_size > 0


def step_show_token():
    header("Step 6 — Token captured!")

    token = JWT_FILE.read_text().strip()

    ok(f"JWT saved to: {JWT_FILE}")
    print()

    # Decode JWT payload
    try:
        import base64, json as _json
        parts = token.split(".")
        if len(parts) == 3:
            payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
            print(f"  {bold('Token info:')}")
            if "email" in payload:
                print(f"    Email    : {payload['email']}")
            if "sub" in payload:
                print(f"    User ID  : {payload['sub']}")
            if "exp" in payload:
                import datetime
                exp = datetime.datetime.fromtimestamp(payload["exp"])
                remaining = exp - datetime.datetime.now()
                hours = int(remaining.total_seconds() // 3600)
                print(f"    Expires  : {exp.strftime('%Y-%m-%d %H:%M')} ({hours}h remaining)")
            print()
    except Exception:
        pass

    print(f"  {bold('Token (first 80 chars):')}")
    print(f"  {C.GREEN}{token[:80]}...{C.RESET}")
    print()

    # Show how to use it
    poc_script = SCRIPT_DIR / "polyconnect-poc.py"
    if poc_script.exists():
        print(f"  {bold('Use it now:')}")
        code(f"python3 {poc_script} --token $(cat {JWT_FILE}) --probe")
        code(f"python3 {poc_script} --token $(cat {JWT_FILE}) --mqtt")
    else:
        print(f"  {bold('Use it:')}")
        code(f"export POLYCONNECT_TOKEN=$(cat {JWT_FILE})")

    print()


def step_cleanup_proxy():
    header("Step 7 — Remove proxy from iPhone")

    box([
        "On your iPhone:",
        "",
        "1. Settings → WiFi",
        "2. Tap the (i) next to your WiFi network",
        "3. HTTP Proxy → Off",
        "4. Tap Save",
    ], color=C.YELLOW)

    wait_enter("  Press Enter once the proxy is removed...")
    ok("Done! Your iPhone is back to normal.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    clear()

    # Banner
    print(f"""
{C.CYAN}{C.BOLD}
  ██████╗  ██████╗ ██╗  ██╗   ██╗ ██████╗ ██████╗ ███╗   ██╗███╗   ██╗███████╗ ██████╗████████╗
  ██╔══██╗██╔═══██╗██║  ╚██╗ ██╔╝██╔════╝██╔═══██╗████╗  ██║████╗  ██║██╔════╝██╔════╝╚══██╔══╝
  ██████╔╝██║   ██║██║   ╚████╔╝ ██║     ██║   ██║██╔██╗ ██║██╔██╗ ██║█████╗  ██║        ██║
  ██╔═══╝ ██║   ██║██║    ╚██╔╝  ██║     ██║   ██║██║╚██╗██║██║╚██╗██║██╔══╝  ██║        ██║
  ██║     ╚██████╔╝███████╗██║   ╚██████╗╚██████╔╝██║ ╚████║██║ ╚████║███████╗╚██████╗   ██║
  ╚═╝      ╚═════╝ ╚══════╝╚═╝    ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═══╝╚══════╝ ╚═════╝   ╚═╝
{C.RESET}""")

    print(f"  {bold('JWT Token Capture Tool')}  {dim('— Linux + iOS via mitmproxy')}")
    print(f"  {dim('Reverse-engineered from Polyconnect v5.3 APK')}")
    print()

    local_ip = get_local_ip()
    print(f"  {dim('Detected Linux IP:')} {bold(local_ip)}")
    print(f"  {dim('Proxy port:       ')} {bold(str(PROXY_PORT))}")
    print(f"  {dim('Output file:      ')} {bold(str(JWT_FILE))}")
    print()

    # Check if token already exists
    if JWT_FILE.exists() and JWT_FILE.stat().st_size > 0:
        age = time.time() - JWT_FILE.stat().st_mtime
        hours = int(age // 3600)
        print(f"  {C.YELLOW}⚠{C.RESET}  A token was previously captured ({hours}h ago).")
        answer = input("  Use existing token? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            step_show_token()
            return

    wait_enter("  Press Enter to start the capture process...")

    TOTAL_STEPS = 7

    # Run steps
    step(1, TOTAL_STEPS, "Check dependencies")
    step_check_deps()

    step(2, TOTAL_STEPS, "Generate CA certificate")
    step_generate_cert()

    step(3, TOTAL_STEPS, "Install certificate on iPhone")
    step_serve_cert(local_ip)

    step(4, TOTAL_STEPS, "Configure iPhone proxy")
    step_configure_proxy(local_ip)

    step(5, TOTAL_STEPS, "Capture JWT token")
    success = step_capture(local_ip)

    if success:
        step(6, TOTAL_STEPS, "Token captured!")
        step_show_token()
        step(7, TOTAL_STEPS, "Remove proxy from iPhone")
        step_cleanup_proxy()
        print(f"\n  {green(bold('All done!'))} Your JWT token is ready to use.\n")
    else:
        print()
        err("No token was captured.")
        print()
        warn("Possible reasons:")
        print("    1. The app did not make any API requests")
        print("    2. Certificate was not trusted on iPhone")
        print("    3. Proxy was not configured correctly")
        print("    4. Certificate pinning blocked mitmproxy")
        print()
        info("Try running the capture step again after verifying the setup.")
        print()
        info(f"Troubleshooting guide: {SCRIPT_DIR / 'README.md'}")
        print()


if __name__ == "__main__":
    main()
