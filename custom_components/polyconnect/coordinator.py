"""DataUpdateCoordinator for Polyconnect.

Multi-pump aware: ``data`` is a dict keyed by pump_id, each value is the
status dict the bridge returns for that pump. The list of pumps is fetched
once at setup and refreshed on every poll (a user can add/remove a heat
pump in the cloud without re-installing the integration).
"""
from __future__ import annotations
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import issue_registry as ir

from .api import (
    PolyconnectAPI,
    PolyconnectError,
    AuthExpiredError,
    CredentialsMissingError,
    PumpNotFoundError,
)
from .const import DOMAIN, LOGGER, CONF_BRIDGE_URL, DEFAULT_SCAN_INTERVAL


class PolyconnectCoordinator(DataUpdateCoordinator):
    """Coordinator that maintains per-pump status dicts.

    Attributes:
        pumps: list of {id, name} entries discovered by the bridge. Refreshed
               on every poll cycle so newly-added pumps appear without an
               integration reload.
        data:  dict[pump_id, status_dict] populated by ``_async_update_data``.
    """

    pumps: list[dict[str, Any]]

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.api = PolyconnectAPI(bridge_url=entry.data[CONF_BRIDGE_URL])
        self.pumps = []
        scan_interval = entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass, LOGGER, name=DOMAIN,
            update_interval=timedelta(minutes=scan_interval),
        )

    def get_pump_data(self, pump_id: str) -> dict[str, Any] | None:
        """Return the most recent status payload for a single pump, or None."""
        if not self.data:
            return None
        return self.data.get(pump_id)

    async def async_discover_pumps(self) -> list[dict[str, Any]]:
        """Hit /pumps on the bridge and cache the result. Called at setup."""
        self.pumps = await self.api.get_pumps()
        return self.pumps

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            # Refresh the pump list cheaply; new pumps surface here.
            self.pumps = await self.api.get_pumps()
            if not self.pumps:
                raise UpdateFailed(
                    "Bridge has no heat pumps yet — first-boot discovery may "
                    "still be running. Will retry on next poll."
                )

            # Per-pump status (sequential — smart-nav in the bridge means
            # each call may pay a navigation cost; concurrent requests would
            # contend on the single Chromium page).
            out: dict[str, dict[str, Any]] = {}
            partial_errors: list[str] = []
            for pump in self.pumps:
                pid = pump["id"]
                try:
                    out[pid] = await self.api.get_status(pid)
                except PumpNotFoundError as e:
                    # Pump went away mid-poll (rare). Drop from cache.
                    LOGGER.warning("Pump %s no longer exists on bridge: %s", pid, e)
                except PolyconnectError as e:
                    partial_errors.append(f"{pid}: {e}")
                    # Keep stale data for this pump rather than wiping the entity
                    if self.data and pid in self.data:
                        out[pid] = self.data[pid]

            if not out:
                raise UpdateFailed(
                    "All pump status fetches failed: " + "; ".join(partial_errors)
                )

            # Validate the first pump's payload — same heuristic as v1.
            sample = next(iter(out.values()))
            null_fields = sum(1 for v in sample.values() if v is None)
            if null_fields >= 6:
                raise UpdateFailed(
                    f"Bridge returned {null_fields}/11 null fields for at least one pump — "
                    "DOM likely not rendered (transient; will retry)"
                )

            ir.async_delete_issue(self.hass, DOMAIN, "auth_expired")
            return out

        except AuthExpiredError as err:
            ir.async_create_issue(
                self.hass, DOMAIN, "auth_expired",
                is_fixable=False, severity=ir.IssueSeverity.ERROR,
                translation_key="auth_expired", translation_placeholders={},
            )
            raise ConfigEntryAuthFailed(
                "Polyconnect session expired and the bridge could not refresh. "
                "Check the add-on logs and credentials."
            ) from err
        except CredentialsMissingError as err:
            ir.async_create_issue(
                self.hass, DOMAIN, "credentials_missing",
                is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key="credentials_missing", translation_placeholders={},
            )
            raise UpdateFailed(
                "Polyconnect credentials not configured. "
                "Set email/password in the Polyconnect Bridge add-on options."
            ) from err
        except PolyconnectError as err:
            raise UpdateFailed(f"Error from Polyconnect bridge: {err}") from err
