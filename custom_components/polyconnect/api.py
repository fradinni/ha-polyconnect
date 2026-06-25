"""HTTP API client for Polyconnect — talks to the polyconnect_bridge add-on.

The add-on runs inside HA's Docker environment (Debian/glibc) where Playwright
and Chromium work natively. This client talks to it via the supervisor ingress
proxy or directly via its exposed port.
"""
from __future__ import annotations
from typing import Any
import aiohttp
from .const import LOGGER


class PolyconnectError(Exception):
    """Base exception for Polyconnect API errors."""


class AuthExpiredError(PolyconnectError):
    """Session token expired — user must refresh it in the add-on config."""


class PolyconnectAPI:
    """Async HTTP client for the polyconnect_bridge add-on REST API."""

    def __init__(self, bridge_url: str) -> None:
        self._bridge_url = bridge_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _session_(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),  # Playwright can be slow
            )
        return self._session

    async def _get(self, path: str) -> dict[str, Any]:
        s = await self._session_()
        try:
            async with s.get(f"{self._bridge_url}{path}") as r:
                if r.status == 401:
                    raise AuthExpiredError("Token expired — update it in the add-on configuration")
                r.raise_for_status()
                return await r.json()
        except AuthExpiredError:
            raise
        except aiohttp.ClientConnectorError as e:
            raise PolyconnectError(
                f"Cannot reach Polyconnect Bridge add-on at {self._bridge_url}. "
                "Is the add-on running?"
            ) from e
        except Exception as e:
            raise PolyconnectError(f"Request failed ({path}): {e}") from e

    async def _post(self, path: str, data: dict | None = None) -> dict[str, Any]:
        s = await self._session_()
        try:
            async with s.post(f"{self._bridge_url}{path}", json=data or {}) as r:
                if r.status == 401:
                    raise AuthExpiredError("Token expired — update it in the add-on configuration")
                r.raise_for_status()
                return await r.json()
        except AuthExpiredError:
            raise
        except aiohttp.ClientConnectorError as e:
            raise PolyconnectError(
                f"Cannot reach Polyconnect Bridge add-on at {self._bridge_url}."
            ) from e
        except Exception as e:
            raise PolyconnectError(f"Request failed ({path}): {e}") from e

    async def health_check(self) -> bool:
        try:
            data = await self._get("/health")
            return data.get("ok", False)
        except PolyconnectError:
            return False

    async def get_status(self) -> dict[str, Any]:
        return await self._get("/status")

    async def set_setpoint(self, temp: float) -> None:
        await self._post("/setpoint", {"temperature": temp})

    async def set_mode(self, mode: str) -> None:
        await self._post("/mode", {"mode": mode})

    async def turn_on(self) -> None:
        await self._post("/on")

    async def turn_off(self) -> None:
        await self._post("/off")

    async def start_filtration(self) -> None:
        await self._post("/filtration/start")

    async def stop_filtration(self) -> None:
        await self._post("/filtration/stop")

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
