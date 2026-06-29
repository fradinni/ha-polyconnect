# Integration Manifest (manifest.json)

Every integration requires a `manifest.json` in its directory.

## Required Fields

```json
{
  "domain": "your_domain",
  "name": "Your Integration",
  "version": "1.0.0",
  "codeowners": ["@github_user"],
  "config_flow": true,
  "documentation": "https://example.com",
  "integration_type": "hub",
  "iot_class": "cloud_polling",
  "requirements": []
}
```

## Field Reference

### domain (required)
Short name with lowercase letters and underscores. Must be unique and match the directory name.

### name (required)
Human-readable name. Rules:
- Cloud variant: append "Cloud" (e.g., "LIFX Cloud")
- Local variant: plain name, no "Local" suffix
- Cloud-only products: use name as-is

### version (required for custom integrations)
Must be valid CalVer or SemVer. Omit for core integrations.

### integration_type
| Type | Description |
|------|-------------|
| device | Single device (e.g., ESPHome) |
| entity | Basic entity platform (rare) |
| helper | Automation helper (input_boolean, derivative) |
| hub | Hub with multiple devices/services (Philips Hue) |
| service | Single service (DuckDNS) |
| system | System integration (reserved) |
| virtual | Points to another integration or IoT standard |

### iot_class
- `assumed_state`: Cannot get real state, uses assumptions
- `cloud_polling`: Cloud API, polled
- `cloud_push`: Cloud API, push notifications
- `local_polling`: Direct communication, polled
- `local_push`: Direct communication, push notifications
- `calculated`: Derived from other data

### config_flow
Set to `true` if integration has a config flow. Requires `config_flow.py`.

### single_config_entry
Set to `true` to allow only one config entry.

### dependencies
Other integrations that must be set up first. Example: `["mqtt"]`.

### after_dependencies
Optional dependencies - integration waits for them if configured, but loads without them.

### requirements
Python packages to install. Format: `["package==1.0.0"]`. Custom integrations should only include packages not in HA core's requirements.txt.

Can use git URLs: `"package@git+https://github.com/user/repo.git@branch"`

### codeowners
GitHub usernames of maintainers: `["@user1", "@user2"]`

### loggers
List of logger names used by requirements for `getLogger` calls.

### quality_scale
Integration quality level: `"bronze"`, `"silver"`, `"gold"`, `"platinum"`.

## Discovery Protocols

### Bluetooth
```json
{
  "bluetooth": [
    {"local_name": "Prodigio_*"},
    {"service_uuid": "cba20d00-224d-11e6-9fb8-0002a5d5c51b"},
    {"service_data_uuid": "0000fd3d-0000-1000-8000-00805f9b34fb"},
    {"manufacturer_id": 76, "manufacturer_data_start": [6]}
  ]
}
```

### Zeroconf
```json
{
  "zeroconf": ["_googlecast._tcp.local."]
}
```
With filters for generic types:
```json
{
  "zeroconf": [
    {"type": "_http._tcp.local.", "properties": {"macaddress": "00408c*"}},
    {"type": "_http._tcp.local.", "name": "example*"}
  ]
}
```

### SSDP
```json
{
  "ssdp": [
    {"st": "roku:ecp", "manufacturer": "Roku", "deviceType": "urn:roku-com:device:player:1-0"}
  ]
}
```

### DHCP
```json
{
  "dhcp": [
    {"hostname": "rachio-*", "macaddress": "009D6B*"},
    {"registered_devices": true}
  ]
}
```

### USB
```json
{
  "usb": [
    {"vid": "AAAA", "pid": "AAAA"},
    {"vid": "1234", "pid": "ABCD", "serial_number": "12345678", "manufacturer": "*midway*", "description": "*zigbee*"}
  ]
}
```

### HomeKit
```json
{
  "homekit": {"models": ["LIFX"]}
}
```

### MQTT
```json
{
  "mqtt": ["tasmota/discovery/#"]
}
```

## Full Example
```json
{
  "domain": "hue",
  "name": "Philips Hue",
  "after_dependencies": ["http"],
  "codeowners": ["@balloob"],
  "dependencies": ["mqtt"],
  "documentation": "https://www.home-assistant.io/components/hue",
  "integration_type": "hub",
  "iot_class": "local_polling",
  "issue_tracker": "https://github.com/balloob/hue/issues",
  "loggers": ["aiohue"],
  "requirements": ["aiohue==1.9.1"],
  "quality_scale": "platinum"
}
```

Source: https://developers.home-assistant.io/docs/creating_integration_manifest
