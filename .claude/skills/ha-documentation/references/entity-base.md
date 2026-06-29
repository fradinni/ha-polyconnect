# Entity Base Class

## Basic Implementation

```python
from homeassistant.components.switch import SwitchEntity

class MySwitch(SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self):
        self._is_on = False
        self._attr_device_info = ...  # For automatic device registration
        self._attr_unique_id = ...

    @property
    def is_on(self):
        return self._is_on

    def turn_on(self, **kwargs):
        self._is_on = True

    def turn_off(self, **kwargs):
        self._is_on = False
```

## Generic Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| assumed_state | bool | False | State is based on assumption |
| attribution | str/None | None | Branding text from API provider |
| available | bool | True | Entity can read/control device |
| device_class | str/None | None | Entity type classification |
| entity_picture | str/None | None | URL of entity picture |
| extra_state_attributes | dict/None | None | Extra info in state machine |
| has_entity_name | bool | False | Required True for new integrations |
| name | str/None | None | Entity name (use translations) |
| should_poll | bool | True | HA polls for updates |
| supported_features | int/None | None | Feature flags |
| translation_key | str/None | None | Key for state translations |
| translation_placeholders | dict/None | None | Placeholders for translated name |

## Registry Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| device_info | DeviceInfo/None | None | Device registry descriptor |
| entity_category | EntityCategory/None | None | CONFIG or DIAGNOSTIC |
| entity_registry_enabled_default | bool | True | Enabled on first add |
| entity_registry_visible_default | bool | True | Visible on first add |
| unique_id | str/None | None | Unique within platform |

## Entity Naming (has_entity_name = True)

**Mandatory for new integrations.**

- Entity name = data point only (e.g., "Power usage"), NOT device name + type
- Main feature of device: `name = None` (uses device name)
- Non-main feature: use `translation_key`

Friendly name generation:
- No device: `friendly_name = entity.name`
- Device + name: `friendly_name = f"{device.name} {entity.name}"`
- Device + None name: `friendly_name = f"{device.name}"`

Entity ID generation:
- No device: `binary_sensor.everyone_is_home`
- Device + name: `sensor.nightlight_battery`
- Device + None: `light.nightlight`

## Property Implementation

### Property function
```python
@property
def icon(self) -> str | None:
    return "mdi:door"
```

### Class/instance attributes (preferred)
```python
class MySwitch(SwitchEntity):
    _attr_icon = "mdi:door"
```

### Entity descriptions (for many entity types)
```python
@dataclass(kw_only=True)
class MySensorDescription(SensorEntityDescription):
    value_fn: Callable[[Device], StateType]

SENSORS = (
    MySensorDescription(
        key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        value_fn=lambda device: device.temp,
    ),
)
```

## Lifecycle Hooks

- `async_added_to_hass()`: Called when entity gets entity_id and hass. Use for: restore state, subscribe to updates.
- `async_will_remove_from_hass()`: Called before removal. Use for: unsubscribe, disconnect.

## Icons

### Icon translations (preferred)
In `icons.json`:
```json
{
  "entity": {
    "sensor": {
      "phase": {
        "default": "mdi:moon",
        "state": {
          "new_moon": "mdi:moon-new",
          "full_moon": "mdi:moon-full"
        }
      }
    }
  }
}
```

### Icon property (discouraged)
```python
@property
def icon(self) -> str | None:
    return "mdi:door"
```

## Excluding Attributes from Recorder

```python
class MyEntity(SensorEntity):
    _unrecorded_attributes: frozenset[str] = frozenset({"entity_picture", "preset_modes"})
```

Must be class attributes, not instance attributes.

Source: https://developers.home-assistant.io/docs/core/entity
