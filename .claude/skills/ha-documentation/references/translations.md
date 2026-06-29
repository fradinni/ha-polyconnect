# Translations (strings.json)

## File Location
`custom_components/<domain>/strings.json` with matching `translations/en.json`.

## Structure

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Step Title",
        "description": "Markdown description",
        "data": {"host": "Host", "api_key": "API Key"},
        "sections": {"advanced": {"name": "Advanced"}}
      }
    },
    "error": {
      "cannot_connect": "Failed to connect",
      "invalid_auth": "Invalid authentication"
    },
    "abort": {
      "already_configured": "[%key:common::config_flow::abort::already_configured_device%]",
      "reauth_successful": "[%key:common::config_flow::abort::reauth_successful%]"
    }
  },
  "options": {
    "step": {
      "init": {
        "data": {"scan_interval": "Update interval (seconds)"}
      }
    }
  },
  "entity": {
    "sensor": {
      "water_temperature": {"name": "Water Temperature"},
      "phase": {
        "state": {"heating": "Heating", "idle": "Idle", "cooling": "Cooling"}
      }
    },
    "binary_sensor": {
      "heat_pump_active": {"name": "Heat Pump Active"}
    },
    "climate": {
      "thermostat": {
        "state_attributes": {
          "preset_mode": {
            "state": {"eco": "Eco", "comfort": "Comfort", "boost": "Boost"}
          }
        }
      }
    }
  },
  "entity_component": {
    "_": {
      "state": {
        "off": "[%key:common::state::off%]",
        "on": "[%key:common::state::on%]"
      }
    }
  },
  "device": {
    "heat_pump": {"name": "Heat Pump"}
  },
  "exceptions": {
    "connection_error": {
      "message": "Failed to connect to {host}"
    }
  },
  "services": {
    "set_mode": {
      "name": "Set Mode",
      "description": "Set the operating mode",
      "fields": {
        "mode": {"name": "Mode", "description": "The mode to set"}
      }
    }
  }
}
```

## Common References
Use `[%key:...]` to reference shared strings:
- `[%key:common::state::on%]` / `off`
- `[%key:common::config_flow::abort::already_configured_device%]`
- `[%key:common::config_flow::abort::reauth_successful%]`
- `[%key:component::sensor::entity_component::temperature::name%]`

## Entity Name Translation
```json
{
  "entity": {
    "sensor": {
      "my_sensor": {"name": "My Sensor Name"}
    }
  }
}
```
Entity must have `translation_key = "my_sensor"` and `has_entity_name = True`.

## Entity State Translation
```json
{
  "entity": {
    "sensor": {
      "mode": {
        "state": {"auto": "Automatic", "manual": "Manual"}
      }
    }
  }
}
```
States must be `snake_case`.

## Unit of Measurement Translation
```json
{
  "entity": {
    "sensor": {
      "steps": {"unit_of_measurement": "steps"}
    }
  }
}
```

## Device Name Translation
```json
{
  "device": {
    "power_strip": {"name": "Power Strip"}
  }
}
```
Device must have `translation_key = "power_strip"`.

## Testing
```bash
python3 -m script.translations develop
```

Source: https://developers.home-assistant.io/docs/internationalization/core
