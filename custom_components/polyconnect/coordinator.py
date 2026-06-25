"""DataUpdateCoordinator for Polyconnect."""
from __future__ import annotations
from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .api import PolyconnectAPI, PolyconnectError, AuthExpiredError
from .const import DOMAIN, LOGGER, CONF_BRIDGE_URL, DEFAULT_SCAN_INTERVAL


class PolyconnectCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.api = PolyconnectAPI(bridge_url=entry.data[CONF_BRIDGE_URL])
        scan_interval = entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass, LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict:
        try:
            return await self.api.get_status()
        except AuthExpiredError as err:
            raise ConfigEntryAuthFailed(
                "Polyconnect session token expired on bridge server. "
                "Restart polyconnect-server.py with a fresh token."
            ) from err
        except PolyconnectError as err:
            raise UpdateFailed(f"Error from Polyconnect bridge: {err}") from err
