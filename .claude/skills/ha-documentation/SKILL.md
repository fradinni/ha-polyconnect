---
name: ha-documentation
description: Comprehensive Home Assistant developer documentation for building custom integrations, add-ons, and entity platforms. Use when creating or updating custom_components/, HA add-ons, entity platforms, config flows, coordinators, translations, device registry, or integration manifests. Sourced from https://developers.home-assistant.io/docs/.
---

# Home Assistant Documentation Skill

Comprehensive Home Assistant developer documentation for building custom integrations, add-ons, and entity platforms. Sourced from https://developers.home-assistant.io/docs/.

## When to Use

Load this skill when creating or updating:
- Custom integrations (`custom_components/`)
- Home Assistant add-ons (`polyconnect_bridge/`)
- Entity platforms (sensor, binary_sensor, climate, switch, etc.)
- Config flows, coordinators, or translations
- Device registry configurations
- Integration manifests

## Reference Files

Load only the files relevant to the current task:

| Task | Reference File(s) |
|------|-------------------|
| Creating a new integration | `references/integration-manifest.md`, `references/integration-setup.md` |
| Building a config flow | `references/config-flow.md` |
| Fetching data / polling / push | `references/fetching-data.md` |
| Creating base entities | `references/entity-base.md` |
| Sensor entities | `references/entity-sensor.md` |
| Binary sensor entities | `references/entity-binary-sensor.md` |
| Climate entities | `references/entity-climate.md` |
| Switch entities | `references/entity-switch.md` |
| Handling setup failures | `references/integration-setup.md` |
| Translations / strings.json | `references/translations.md` |
| Device registry / device_info | `references/device-registry.md` |
| Add-on configuration | `references/addon-config.md` |
| Add-on repository | `references/addon-repository.md` |
| Full integration (all aspects) | Load all files as needed |

## Quick Reference

### Integration File Structure
```
custom_components/<domain>/
├── __init__.py          # async_setup_entry / async_unload_entry
├── manifest.json        # Integration metadata
├── config_flow.py       # Config flow handler
├── const.py             # Constants, DOMAIN, entity descriptions
├── coordinator.py       # DataUpdateCoordinator
├── api.py               # HTTP/API client
├── entity.py            # Base entity class
├── sensor.py            # Sensor platform
├── binary_sensor.py     # Binary sensor platform
├── climate.py           # Climate platform
├── switch.py            # Switch platform
├── strings.json         # Translation strings
└── translations/
    └── en.json          # English translations
```

### Add-on File Structure
```
<addon_name>/
├── config.yaml          # Add-on manifest
├── Dockerfile           # Container definition
├── run.sh               # Startup script
├── translations/
│   └── en.yaml          # Translations
├── DOCS.md              # User documentation
├── CHANGELOG.md         # Version history
├── icon.png             # Add-on icon
├── logo.png             # Add-on logo
└── README.md            # Repository readme
```

### Key Patterns

**DataUpdateCoordinator** (coordinated polling):
```python
class MyCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, config_entry, my_api):
        super().__init__(hass, _LOGGER, name="My sensor",
            config_entry=config_entry,
            update_interval=timedelta(seconds=30))
        self.my_api = my_api

    async def _async_update_data(self):
        try:
            async with async_timeout.timeout(10):
                return await self.my_api.fetch_data()
        except ApiAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ApiError as err:
            raise UpdateFailed(f"Error: {err}")
```

**Entity with _attr_ pattern**:
```python
class MySensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, description):
        super().__init__(coordinator, context=description.key)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_id}_{description.key}"
```

**Config entry setup**:
```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = MyCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True
```

### Sources
- Integration manifest: https://developers.home-assistant.io/docs/creating_integration_manifest
- Config flow: https://developers.home-assistant.io/docs/config_entries_config_flow_handler
- Fetching data: https://developers.home-assistant.io/docs/integration_fetching_data
- Entity base: https://developers.home-assistant.io/docs/core/entity
- Sensor: https://developers.home-assistant.io/docs/core/entity/sensor
- Binary sensor: https://developers.home-assistant.io/docs/core/entity/binary-sensor
- Climate: https://developers.home-assistant.io/docs/core/entity/climate
- Switch: https://developers.home-assistant.io/docs/core/entity/switch
- Setup failures: https://developers.home-assistant.io/docs/integration_setup_failures
- Translations: https://developers.home-assistant.io/docs/internationalization/core
- Device registry: https://developers.home-assistant.io/docs/device_registry_index
- Add-on config: https://developers.home-assistant.io/docs/add-ons/configuration
- Add-on repository: https://developers.home-assistant.io/docs/add-ons/repository
