"""Capture Manager — mitmproxy subprocess + credential state machine.

Manages the lifecycle of the mitmproxy capture process and tracks
captured credentials (token + device IDs) in /data/ persistent storage.

States:
  idle      → no capture running, credentials may or may not exist
  running   → mitmproxy + setup web UI active, waiting for phone traffic
  complete  → all credentials captured, auto-stopping in grace period
  stopping  → shutting down mitmproxy subprocess
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

log = logging.getLogger("polyconnect.capture")

# ── Paths ─────────────────────────────────────────────────────────────────────

DATA_DIR = Path(os.environ.get("POLYCONNECT_DATA_DIR", "/data"))
TOKEN_FILE = DATA_DIR / "token.txt"
IDS_FILE = DATA_DIR / "ids.json"
CAPTURE_STATUS_FILE = DATA_DIR / ".capture_status.json"
MITMPROXY_CONFDIR = DATA_DIR / ".mitmproxy"
CERT_PEM = MITMPROXY_CONFDIR / "mitmproxy-ca-cert.pem"

# The mitm addon script lives next to this file
MITM_ADDON_PATH = Path(__file__).parent / "mitm_addon.py"

PROXY_PORT = 8888
SETUP_PORT = 8080
AUTO_STOP_GRACE_SECONDS = 30


# ── State ─────────────────────────────────────────────────────────────────────

class CapturePhase(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETE = "complete"
    STOPPING = "stopping"


@dataclass
class Credentials:
    """Persistent credentials loaded from /data/."""

    token: str | None = None
    installation_id: str | None = None
    heat_pump_id: str | None = None

    @property
    def is_complete(self) -> bool:
        return bool(self.token and self.installation_id and self.heat_pump_id)

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "installation_id": self.installation_id,
            "heat_pump_id": self.heat_pump_id,
            "complete": self.is_complete,
        }


@dataclass
class CaptureStatus:
    """Live state of the capture process."""

    phase: CapturePhase = CapturePhase.IDLE
    token_captured: bool = False
    installation_id: str | None = None
    heat_pump_id: str | None = None
    requests_seen: int = 0
    target_requests: int = 0
    started_at: float | None = None
    local_ip: str = ""
    error: str | None = None

    @property
    def all_captured(self) -> bool:
        return (
            self.token_captured
            and self.installation_id is not None
            and self.heat_pump_id is not None
        )

    def to_dict(self) -> dict:
        return {
            "phase": self.phase.value,
            "token_captured": self.token_captured,
            "ids": {
                "installation_id": self.installation_id,
                "heat_pump_id": self.heat_pump_id,
            },
            "all_captured": self.all_captured,
            "requests_seen": self.requests_seen,
            "target_requests": self.target_requests,
            "uptime_seconds": int(time.time() - self.started_at) if self.started_at else 0,
            "local_ip": self.local_ip,
            "proxy_port": PROXY_PORT,
            "setup_port": SETUP_PORT,
            "error": self.error,
        }


# ── Manager ───────────────────────────────────────────────────────────────────

class CaptureManager:
    """Manages mitmproxy lifecycle and credential persistence."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._watcher_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._setup_server = None  # set by server.py when starting capture
        self.status = CaptureStatus()
        self.credentials = Credentials()
        self._load_credentials()

    # ── Credential persistence ────────────────────────────────────────────────

    def _load_credentials(self) -> None:
        """Load credentials from /data/ persistent storage."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        if TOKEN_FILE.exists() and TOKEN_FILE.stat().st_size > 0:
            self.credentials.token = TOKEN_FILE.read_text().strip()
            log.info("Loaded token from %s (%d chars)", TOKEN_FILE, len(self.credentials.token))

        if IDS_FILE.exists():
            try:
                data = json.loads(IDS_FILE.read_text())
                self.credentials.installation_id = data.get("installation_id")
                self.credentials.heat_pump_id = data.get("heat_pump_id")
                log.info("Loaded IDs from %s: %s", IDS_FILE, data)
            except Exception as e:
                log.warning("Failed to load IDs: %s", e)

    def _save_credentials(self) -> None:
        """Persist current credentials to /data/."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        if self.credentials.token:
            TOKEN_FILE.write_text(self.credentials.token)

        ids = {}
        if self.credentials.installation_id:
            ids["installation_id"] = self.credentials.installation_id
        if self.credentials.heat_pump_id:
            ids["heat_pump_id"] = self.credentials.heat_pump_id
        if ids:
            IDS_FILE.write_text(json.dumps(ids, indent=2) + "\n")

    def reset_credentials(self) -> None:
        """Clear all stored credentials."""
        with self._lock:
            self.credentials = Credentials()
            for f in (TOKEN_FILE, IDS_FILE, CAPTURE_STATUS_FILE):
                if f.exists():
                    f.unlink()
            log.info("Credentials reset")

    # ── Capture lifecycle ─────────────────────────────────────────────────────

    def start_capture(self) -> dict:
        """Start the mitmproxy capture process. Idempotent."""
        with self._lock:
            if self.status.phase == CapturePhase.RUNNING:
                return {"ok": True, "note": "already running", **self.status.to_dict()}

            if self.status.phase == CapturePhase.STOPPING:
                return {"ok": False, "error": "still stopping, try again in a moment"}

            # Reset status for fresh capture
            self.status = CaptureStatus(
                phase=CapturePhase.RUNNING,
                started_at=time.time(),
                local_ip=_get_local_ip(),
            )

            # Ensure CA certificate exists
            if not self._ensure_cert():
                self.status.error = "Failed to generate mitmproxy CA certificate"
                self.status.phase = CapturePhase.IDLE
                return {"ok": False, "error": self.status.error}

            # Start mitmproxy
            if not self._start_mitmdump():
                self.status.error = "Failed to start mitmproxy"
                self.status.phase = CapturePhase.IDLE
                return {"ok": False, "error": self.status.error}

            # Start background watcher
            self._stop_event.clear()
            self._watcher_thread = threading.Thread(
                target=self._watch_loop, daemon=True, name="capture-watcher"
            )
            self._watcher_thread.start()

            log.info(
                "Capture started — proxy on :%d, setup UI on :%d, IP=%s",
                PROXY_PORT, SETUP_PORT, self.status.local_ip,
            )
            return {"ok": True, **self.status.to_dict()}

    def stop_capture(self) -> dict:
        """Stop the mitmproxy capture. Idempotent."""
        with self._lock:
            return self._stop_internal()

    def _stop_internal(self) -> dict:
        """Internal stop (must hold self._lock)."""
        if self.status.phase in (CapturePhase.IDLE, CapturePhase.STOPPING):
            return {"ok": True, "note": "not running"}

        self.status.phase = CapturePhase.STOPPING
        self._stop_event.set()

        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

        self.status.phase = CapturePhase.IDLE
        log.info("Capture stopped")
        return {"ok": True, "credentials": self.credentials.to_dict()}

    def get_status(self) -> dict:
        """Return current capture status + credentials."""
        return {
            "capture": self.status.to_dict(),
            "credentials": self.credentials.to_dict(),
        }

    # ── mitmproxy management ──────────────────────────────────────────────────

    def _ensure_cert(self) -> bool:
        """Ensure mitmproxy CA certificate exists in persistent storage."""
        MITMPROXY_CONFDIR.mkdir(parents=True, exist_ok=True)

        if CERT_PEM.exists():
            return True

        mitmdump = _find_mitmdump()
        if not mitmdump:
            log.error("mitmdump not found — is mitmproxy installed?")
            return False

        log.info("Generating mitmproxy CA certificate (stored in /data/.mitmproxy/)...")
        proc = subprocess.Popen(
            [mitmdump, "--set", f"confdir={MITMPROXY_CONFDIR}", "--listen-port", "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(3)
        proc.terminate()
        proc.wait(timeout=5)
        return CERT_PEM.exists()

    def _start_mitmdump(self) -> bool:
        """Start the mitmdump process with our addon script."""
        mitmdump = _find_mitmdump()
        if not mitmdump:
            return False

        cmd = [
            mitmdump,
            "--set", f"confdir={MITMPROXY_CONFDIR}",
            "--listen-port", str(PROXY_PORT),
            "--ssl-insecure",
            "--set", "stream_large_bodies=1",
            "--set", "console_eventlog_verbosity=warn",
            "-s", str(MITM_ADDON_PATH),
        ]

        # Pass output paths via environment
        env = os.environ.copy()
        env["CAPTURE_TOKEN_FILE"] = str(TOKEN_FILE)
        env["CAPTURE_IDS_FILE"] = str(IDS_FILE)
        env["CAPTURE_STATUS_FILE"] = str(CAPTURE_STATUS_FILE)

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )

        # Give it a moment to start
        time.sleep(1.5)
        if self._proc.poll() is not None:
            log.error("mitmdump exited immediately")
            return False

        return True

    # ── Background watcher ────────────────────────────────────────────────────

    def _watch_loop(self) -> None:
        """Watch for captured data and auto-stop when complete."""
        last_status_mtime = 0.0
        complete_time: float | None = None

        while not self._stop_event.is_set():
            time.sleep(1.0)

            # Snapshot _proc under lock to avoid TOCTOU with stop_capture()
            with self._lock:
                proc = self._proc

            # Check if mitmproxy crashed
            if proc and proc.poll() is not None:
                log.warning("mitmproxy exited unexpectedly (code=%s)", proc.returncode)
                with self._lock:
                    self.status.phase = CapturePhase.IDLE
                    self.status.error = "mitmproxy crashed"
                break

            # Read status file written by mitm_addon.py
            try:
                if CAPTURE_STATUS_FILE.exists():
                    mtime = CAPTURE_STATUS_FILE.stat().st_mtime
                    if mtime > last_status_mtime:
                        last_status_mtime = mtime
                        data = json.loads(CAPTURE_STATUS_FILE.read_text())
                        self._sync_from_status(data)
            except Exception:
                pass

            # Also check credential files directly
            self._sync_from_files()

            # Auto-stop logic
            if self.status.all_captured:
                if complete_time is None:
                    complete_time = time.time()
                    with self._lock:
                        self.status.phase = CapturePhase.COMPLETE
                    self._save_credentials()
                    log.info("All credentials captured! Auto-stopping in %ds", AUTO_STOP_GRACE_SECONDS)
                elif time.time() - complete_time > AUTO_STOP_GRACE_SECONDS:
                    with self._lock:
                        self._stop_internal()
                    break

            # Read mitmdump output for request counting
            if proc and proc.stdout:
                self._drain_stdout()

    def _sync_from_status(self, data: dict) -> None:
        """Update status from the mitm addon's status file."""
        if data.get("token_captured"):
            self.status.token_captured = True
        for key in ("installation_id", "heat_pump_id"):
            val = data.get("ids", {}).get(key)
            if val:
                setattr(self.status, key, val)

    def _sync_from_files(self) -> None:
        """Sync credentials from written files."""
        if TOKEN_FILE.exists() and TOKEN_FILE.stat().st_size > 0:
            token = TOKEN_FILE.read_text().strip()
            if token and self.credentials.token != token:
                self.credentials.token = token
                self.status.token_captured = True
                log.info("Token captured (%d chars)", len(token))

        if IDS_FILE.exists():
            try:
                data = json.loads(IDS_FILE.read_text())
                for key in ("installation_id", "heat_pump_id"):
                    val = data.get(key)
                    if val and getattr(self.credentials, key) != val:
                        setattr(self.credentials, key, val)
                        setattr(self.status, key, val)
                        log.info("Captured %s: %s", key, val)
            except Exception:
                pass

    def _drain_stdout(self) -> None:
        """Non-blocking read of mitmdump stdout for stats."""
        import select

        try:
            while select.select([self._proc.stdout], [], [], 0)[0]:
                line = self._proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if "[CAPTURED]" in text or "[FOUND]" in text or "[WS-NAV]" in text:
                    self.status.target_requests += 1
                    log.info("MITM: %s", text)
                self.status.requests_seen += 1
        except Exception:
            pass

    # ── Certificate access ────────────────────────────────────────────────────

    def get_cert_pem(self) -> bytes | None:
        """Return the CA certificate content for download."""
        if CERT_PEM.exists():
            return CERT_PEM.read_bytes()
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_mitmdump() -> str | None:
    """Locate the mitmdump binary."""
    for candidate in ["mitmdump", "/usr/local/bin/mitmdump", "/usr/bin/mitmdump"]:
        if shutil.which(candidate):
            return candidate
    return None


def _get_local_ip() -> str:
    """Get the local IP address visible on the LAN."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        # Fallback: try to get from hostname
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "0.0.0.0"
