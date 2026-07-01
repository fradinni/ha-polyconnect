"""Polyconnect Home Assistant Integration.

Provides control and monitoring of Polyconnect / Ingeli pool heat pumps.
Multi-pump aware: discovers all heat pumps from the bridge at setup and
creates one HA device per pump.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .coordinator import PolyconnectCoordinator
from .const import DOMAIN, PLATFORMS, LOGGER


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Polyconnect from a config entry (called on HA startup or manual add)."""
    coordinator = PolyconnectCoordinator(hass, entry)

    # Discover the heat-pump list FIRST so the platform setup callbacks can
    # see it via coordinator.pumps. This call triggers bridge-side discovery
    # on first boot (Playwright SPA scrape).
    try:
        pumps = await coordinator.async_discover_pumps()
    except Exception as err:
        raise ConfigEntryNotReady(f"Cannot reach Polyconnect bridge: {err}") from err

    if not pumps:
        # Could happen on a brand-new install where the bridge hasn't logged
        # in yet. Trigger a /status fetch which forces the bridge to launch
        # Playwright + discover pumps, then try again.
        LOGGER.info("No pumps reported by bridge — forcing first status fetch to trigger discovery")
        try:
            await coordinator.async_config_entry_first_refresh()
            pumps = await coordinator.async_discover_pumps()
        except Exception as err:
            raise ConfigEntryNotReady(f"Bridge has no pumps yet: {err}") from err
        if not pumps:
            raise ConfigEntryNotReady(
                "Polyconnect bridge reports no heat pumps. "
                "Check the add-on configuration (email/password) and logs."
            )

    LOGGER.info("Polyconnect: %d heat pump(s) discovered: %s",
                len(pumps), [p["name"] for p in pumps])

    # First real data fetch (after we know the pump list).
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Initial status fetch failed: {err}") from err

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry (called on HA shutdown or integration removal)."""
    if hasattr(entry, "runtime_data") and entry.runtime_data is not None:
        await entry.runtime_data.api.close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing a device from the UI when it no longer maps to a live pump.

    Called by HA when the user clicks "Delete device". Returns True if the
    device is safe to remove — i.e. no coordinator entry references its
    identifier. This covers v1→v2 migration orphans (identifier without
    a pump_id suffix) and pumps that disappeared from the cloud account.
    """
    coordinator: PolyconnectCoordinator = entry.runtime_data
    live_ids = {
        f"{entry.entry_id}_{p['id']}" for p in (coordinator.pumps or [])
    }
    device_ids = {ident[1] for ident in device.identifiers if ident[0] == DOMAIN}
    # Safe if none of the device's identifiers is currently reported by a pump.
    return not (device_ids & live_ids)
