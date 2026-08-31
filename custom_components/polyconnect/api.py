"""HTTP API client for Polyconnect — talks to the polyconnect_bridge add-on.

The add-on runs inside HA's Docker environment (Debian/glibc) where Playwright
and Chromium work natively. This client talks to it via the supervisor ingress
proxy or directly via its exposed port.

v2.1+: multi-pump aware. Each method that targets a single pump takes a
``pump_id`` argument. The legacy single-pump endpoints are still hit when
``pump_id`` is omitted (bridge aliases them to the first discovered pump).
"""
from __future__ import annotations
from typing import Any
import aiohttp
from homeassistant.exceptions import HomeAssistantError
from .const import LOGGER


class PolyconnectError(HomeAssistantError):
    """Base exception for Polyconnect API errors.

    Subclasses HomeAssistantError so failures raised from entity service calls
    surface to the user instead of appearing as an unexpected traceback.
    """


class AuthExpiredError(PolyconnectError):
    """Session token expired — bridge could not refresh."""


class CredentialsMissingError(PolyconnectError):
    """Credentials not configured in the bridge (email/password)."""


class PumpNotFoundError(PolyconnectError):
    """The requested heat pump id is not in the bridge's discovered list."""


class CommandFailedError(PolyconnectError):
    """The bridge returned 200 but reported the command was never applied."""


class PolyconnectAPI:
    """Async HTTP client for the polyconnect_bridge add-on REST API."""

    # Status reads only scrape the already-loaded page.
    _READ_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=10)
    # Writes are Playwright-driven and verified server-side: the bridge may
    # spend ~30s re-navigating the SPA, up to ~35s driving the temperature
    # slider, then poll its own status to confirm the change. See the timing
    # budget in polyconnect_bridge/server.py. Cutting this short would surface
    # as an aiohttp timeout carrying no diagnostic, instead of the bridge's
    # structured error — and would report failure for a command that landed.
    _WRITE_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=10)

    def __init__(self, bridge_url: str) -> None:
        self._bridge_url = bridge_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _session_(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._READ_TIMEOUT)
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> dict[str, Any]:
        s = await self._session_()
        url = f"{self._bridge_url}{path}"
        try:
            async with s.request(
                method,
                url,
                json=(json_body or {}) if method == "POST" else None,
                timeout=timeout or self._READ_TIMEOUT,
            ) as r:
                if r.status == 401:
                    raise AuthExpiredError("Session token expired — bridge could not refresh")
                if r.status == 404:
                    try:
                        data = await r.json()
                    except Exception:
                        data = {}
                    if data.get("pump_not_found"):
                        raise PumpNotFoundError(data.get("error", "Pump not found"))
                if r.status == 503:
                    data = await r.json()
                    if data.get("credentials_missing"):
                        raise CredentialsMissingError(
                            "Credentials not configured — set email/password in the Polyconnect Bridge add-on"
                        )
                if r.status >= 400:
                    # The bridge explains failures in the body (e.g. 409
                    # "setpoint slider unavailable (heat pump off?)"). Surface
                    # that instead of letting raise_for_status() reduce it to a
                    # bare status line.
                    try:
                        data = await r.json()
                    except Exception:
                        data = {}
                    detail = data.get("error")
                    if detail:
                        LOGGER.error("Polyconnect bridge rejected %s %s: %s",
                                     method, path, detail)
                        raise PolyconnectError(detail)
                r.raise_for_status()
                return await r.json()
        except (AuthExpiredError, CredentialsMissingError, PumpNotFoundError,
                PolyconnectError):
            raise
        except aiohttp.ClientConnectorError as e:
            raise PolyconnectError(
                f"Cannot reach Polyconnect Bridge add-on at {self._bridge_url}. "
                "Is the add-on running?"
            ) from e
        except Exception as e:
            raise PolyconnectError(f"Request failed ({method} {path}): {e}") from e

    async def _get(self, path: str) -> dict[str, Any]:
        return await self._request("GET", path)

    async def _post(self, path: str, data: dict | None = None) -> dict[str, Any]:
        return await self._request("POST", path, data)

    async def _command(self, path: str, data: dict | None = None) -> None:
        """POST a write command and raise unless the bridge confirms it.

        The bridge answers ``{"ok": false, ...}`` when it located the control
        but the device state never changed, and only escalates to a non-2xx
        status for hard failures. Without this check a silently-ignored command
        would look like a success to Home Assistant.
        """
        body = await self._request("POST", path, data, timeout=self._WRITE_TIMEOUT)
        if body.get("ok") is False:
            error = body.get("error") or "command was not applied"
            observed = body.get("observed")
            if observed is not None:
                error = f"{error} (device still reports {observed!r})"
            LOGGER.error("Polyconnect command failed (%s): %s", path, error)
            raise CommandFailedError(error)

    # ── Health + auth ────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        try:
            data = await self._get("/health")
            return data.get("ok", False)
        except PolyconnectError:
            return False

    async def get_health(self) -> dict[str, Any]:
        return await self._get("/health")

    async def get_auth_status(self) -> dict[str, Any]:
        return await self._get("/auth/status")

    async def refresh_auth(self) -> dict[str, Any]:
        return await self._post("/auth/refresh")

    # ── Pump discovery ───────────────────────────────────────────────────────

    async def get_pumps(self) -> dict[str, Any]:
        """Return the full pump listing.

        Response shape (bridge v2.2+):
            {"pumps": [{"id": ..., "name": ...}, ...],
             "installation_id": "...",
             "installation_name": "..."}

        Older bridges return just `{"pumps": [...]}` — callers should treat
        installation_id / installation_name as optional."""
        return await self._get("/pumps")

    # ── Per-pump device control (v2.1+) ──────────────────────────────────────

    async def get_status(self, pump_id: str) -> dict[str, Any]:
        return await self._get(f"/pumps/{pump_id}/status")

    async def set_setpoint(self, pump_id: str, temp: float) -> None:
        await self._command(f"/pumps/{pump_id}/setpoint", {"temperature": temp})

    async def set_mode(self, pump_id: str, mode: str) -> None:
        await self._command(f"/pumps/{pump_id}/mode", {"mode": mode})

    async def turn_on(self, pump_id: str) -> None:
        await self._command(f"/pumps/{pump_id}/on")

    async def turn_off(self, pump_id: str) -> None:
        await self._command(f"/pumps/{pump_id}/off")

    async def start_filtration(self, pump_id: str) -> None:
        await self._command(f"/pumps/{pump_id}/filtration/start")

    async def stop_filtration(self, pump_id: str) -> None:
        await self._command(f"/pumps/{pump_id}/filtration/stop")

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
