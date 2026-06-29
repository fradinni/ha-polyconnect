# Device Registry

## What is a Device?
A physical device with its own control unit, or a service. One device can have multiple entities.

## Device Properties

| Attribute | Description |
|-----------|-------------|
| identifiers | Set of (DOMAIN, id) tuples. Uniquely identifies device. |
| connections | Set of (type, id) tuples. E.g., MAC address. |
| name | Device name |
| manufacturer | Manufacturer name |
| model | Model name |
| model_id | Model identifier |
| sw_version | Firmware version |
| hw_version | Hardware version |
| serial_number | Serial number |
| via_device | Parent device identifier (for hubs) |
| suggested_area | Suggested area name |
| configuration_url | URL to configure device |
| entry_type | None or DeviceEntryType.SERVICE |

## Automatic Registration via Entity

```python
from homeassistant.helpers.device_registry import DeviceInfo

class MySensor(SensorEntity):
    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, device_serial)},
        name="My Device",
        manufacturer="Acme",
        model="Model X",
        model_id="MX-100",
        sw_version="1.0.0",
        hw_version="2.0",
        serial_number="SN12345",
        suggested_area="Living Room",
        via_device=(DOMAIN, hub_serial),
        configuration_url="http://192.168.1.100",
    )
```

## Manual Registration

```python
from homeassistant.helpers import device_registry as dr

device_registry = dr.async_get(hass)
device_registry.async_get_or_create(
    config_entry_id=entry.entry_id,
    connections={(dr.CONNECTION_NETWORK_MAC, mac_address)},
    identifiers={(DOMAIN, device_id)},
    manufacturer="Acme",
    name="My Device",
    model="Model X",
    sw_version="1.0.0",
    suggested_area="Kitchen",
)
```

## Removing Devices

Implement in `__init__.py`:
```python
async def async_remove_config_entry_device(hass, config_entry, device_entry):
    """Remove config entry from device."""
    # Clean up device
    return True
```

## Parent/Child Devices

Use `via_device` for hub-and-spoke topology:
```python
# Hub device
DeviceInfo(identifiers={(DOMAIN, hub_id)}, name="Hub")

# Child device
DeviceInfo(
    identifiers={(DOMAIN, child_id)},
    name="Sensor",
    via_device=(DOMAIN, hub_id),
)
```

Source: https://developers.home-assistant.io/docs/device_registry_index
