"""Polyconnect Home Assistant Integration.

Provides control and monitoring of Polyconnect / Ingeli pool heat pumps.
Uses a Playwright-based API client since the backend exposes no REST API
— all device interaction goes through a Blazor Server SignalR WebSocket.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .coordinator import PolyconnectCoordinator
from .const import DOMAIN, PLATFORMS, LOGGER


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Polyconnect from a config entry (called on HA startup or manual add)."""
    coordinator = PolyconnectCoordinator(hass, entry)

    # Do the first data fetch. If it fails with a transient error, ConfigEntryNotReady
    # tells HA to retry automatically after a backoff delay.
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Cannot connect to Polyconnect: {err}") from err

    # Store coordinator on the entry so all platform files can access it via entry.runtime_data
    entry.runtime_data = coordinator

    # Forward setup to each platform (sensor.py, climate.py, etc.)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry when the user changes options (e.g., scan interval)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry (called on HA shutdown or integration removal)."""
    # Clean up Playwright browser resources
    if hasattr(entry, "runtime_data") and entry.runtime_data is not None:
        await entry.runtime_data.api.close()

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
