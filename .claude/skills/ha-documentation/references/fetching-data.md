# Fetching Data

## Push vs Poll

**Push**: Subscribe to API, get notified on changes. Set `should_poll = False`, call `async_write_ha_state()` on updates.

**Poll**: Fetch data at intervals. Default behavior (`should_poll = True`).

## DataUpdateCoordinator (Recommended for Polling)

Use when a single API call fetches data for all entities.

```python
from datetime import timedelta
import logging
import async_timeout
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity, DataUpdateCoordinator, UpdateFailed)

_LOGGER = logging.getLogger(__name__)

class MyCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, config_entry, my_api):
        super().__init__(
            hass, _LOGGER,
            name="My sensor",
            config_entry=config_entry,
            update_interval=timedelta(seconds=30),
            always_update=True  # Set False if data supports __eq__
        )
        self.my_api = my_api

    async def _async_setup(self):
        """One-time setup during async_config_entry_first_refresh."""
        self._device = await self.my_api.get_device()

    async def _async_update_data(self):
        """Fetch data from API."""
        try:
            async with async_timeout.timeout(10):
                listening_idx = set(self.async_contexts())
                return await self.my_api.fetch_data(listening_idx)
        except ApiAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ApiError as err:
            raise UpdateFailed(f"Error communicating with API: {err}")
```

### CoordinatorEntity

```python
class MyEntity(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, idx):
        super().__init__(coordinator, context=idx)
        self.idx = idx
        self._attr_unique_id = f"{coordinator.device_id}_{idx}"

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_native_value = self.coordinator.data[self.idx]["value"]
        self.async_write_ha_state()
```

### Setup in __init__.py

```python
async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = MyCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    async_add_entities(
        MyEntity(coordinator, idx) for idx, ent in enumerate(coordinator.data)
    )
```

## Push API with Coordinator

For push APIs, create coordinator without polling params, then push data:

```python
class PushCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, config_entry):
        super().__init__(hass, _LOGGER, name="Push sensor", config_entry=config_entry)

# When push data arrives:
coordinator.async_set_updated_data(new_data)
```

## Per-Entity Polling

For APIs with one endpoint per entity:

```python
class MyEntity(SensorEntity):
    async def async_update(self):
        self._attr_native_value = await self.api.get_value()

SCAN_INTERVAL = timedelta(seconds=30)  # In platform file
```

Pass `update_before_add=True` to `add_entities` if initial fetch is needed.

## Request Parallelism

HA manages parallelism via semaphores per integration:
- Default: 0 if entity has `async_update`, else 1
- Override with `PARALLEL_UPDATES` constant in platform file
- Value 0 = integration manages its own parallelism

## Optimistic Updates

For entities that send commands:
```python
async def async_turn_on(self, **kwargs):
    self._attr_is_on = True
    self.async_write_ha_state()
    await self.api.turn_on()
    await asyncio.sleep(1)
    await self.coordinator.async_request_refresh()
```

Source: https://developers.home-assistant.io/docs/integration_fetching_data
