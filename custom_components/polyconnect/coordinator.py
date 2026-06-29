"""DataUpdateCoordinator for Polyconnect."""
from __future__ import annotations
from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import issue_registry as ir
from .api import PolyconnectAPI, PolyconnectError, AuthExpiredError, CredentialsMissingError
from .const import DOMAIN, LOGGER, CONF_BRIDGE_URL, DEFAULT_SCAN_INTERVAL


class PolyconnectCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.api = PolyconnectAPI(bridge_url=entry.data[CONF_BRIDGE_URL])
        scan_interval = entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass, LOGGER, name=DOMAIN,
            update_interval=timedelta(minutes=scan_interval),
        )

    async def _async_update_data(self) -> dict:
        try:
            data = await self.api.get_status()
            null_fields = sum(1 for v in data.values() if v is None)
            if null_fields >= 6:
                raise UpdateFailed(
                    f"Bridge returned {null_fields}/11 null fields — "
                    "DOM likely not rendered (possible token expiry)"
                )
            ir.async_delete_issue(self.hass, DOMAIN, "auth_expired")
            return data
        except AuthExpiredError as err:
            # Create a repair issue so user can recapture
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                "auth_expired",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="auth_expired",
                translation_placeholders={},
            )
            raise ConfigEntryAuthFailed(
                "Polyconnect session token expired. "
                "Open the Polyconnect Bridge add-on to recapture credentials."
            ) from err
        except CredentialsMissingError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                "credentials_missing",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="credentials_missing",
                translation_placeholders={},
            )
            raise UpdateFailed(
                "Polyconnect credentials not configured. "
                "Open the Polyconnect Bridge add-on to run the capture wizard."
            ) from err
        except PolyconnectError as err:
            raise UpdateFailed(f"Error from Polyconnect bridge: {err}") from err
