# Integration Setup & Error Handling

## __init__.py Structure

```python
from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from .const import DOMAIN
from .coordinator import MyCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.CLIMATE, Platform.SWITCH]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    api = MyApi(entry.data["host"], entry.data["api_key"])
    coordinator = MyCoordinator(hass, entry, api)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

async def update_listener(hass: HomeAssistant, entry: ConfigEntry):
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
```

## Handling Setup Failures

### ConfigEntryNotReady (device offline)
```python
async def async_setup_entry(hass, entry):
    try:
        await device.async_setup()
    except (asyncio.TimeoutError, TimeoutException) as ex:
        raise ConfigEntryNotReady(f"Timeout connecting to {device.ip}") from ex
```
HA auto-retries with backoff. Don't log warnings yourself.

### ConfigEntryAuthFailed (expired credentials)
```python
async def async_setup_entry(hass, entry):
    try:
        await auth.refresh_tokens()
    except TokenExpiredError as err:
        raise ConfigEntryAuthFailed(f"Credentials expired") from err
```
Cancels updates and starts reauth flow.

### PlatformNotReady (YAML platforms)
```python
async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    try:
        await device.async_setup()
    except ConnectionError as ex:
        raise PlatformNotReady(f"Connection error: {ex}") from ex
```

## Config Entry Migration

```python
async def async_migrate_entry(hass, config_entry):
    if config_entry.version == 1:
        new = {**config_entry.data}
        if config_entry.minor_version < 2:
            new["new_field"] = "default"
        hass.config_entries.async_update_entry(
            config_entry, data=new, minor_version=2, version=1)
    return True
```

Source: https://developers.home-assistant.io/docs/integration_setup_failures
