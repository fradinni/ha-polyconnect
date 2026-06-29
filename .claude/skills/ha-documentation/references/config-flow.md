# Config Flow

Config flows let users set up integrations via the UI. Requires `config_flow: true` in manifest.json and a `config_flow.py` file.

## Basic Structure

```python
from homeassistant import config_entries
from homeassistant.core import callback
import voluptuous as vol
from .const import DOMAIN

class MyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                # Validate input
                info = await validate_input(user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("host"): str,
                vol.Required("api_key"): str,
            }),
            errors=errors,
        )
```

## Reserved Step Names

| Step | Purpose |
|------|---------|
| user | User-initiated flow |
| reauth | Re-authentication (expired credentials) |
| reconfigure | User reconfigures existing entry |
| zeroconf | Zeroconf discovery |
| ssdp | SSDP discovery |
| dhcp | DHCP discovery |
| bluetooth | Bluetooth discovery |
| homekit | HomeKit discovery |
| mqtt | MQTT discovery |
| usb | USB discovery |
| hassio | Supervisor add-on discovery |
| import | YAML migration |

## Unique IDs

Prevent duplicate setups. Must be stable, not user-changeable, string type.

```python
await self.async_set_unique_id(unique_id)
self._abort_if_unique_id_configured()
```

Good sources: serial number, MAC address (from device API), unique geo location.
Bad sources: IP address, device name, user-changeable hostname, URL.

Update config entry on IP change:
```python
await self.async_set_unique_id(serial_number)
self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})
```

## Discovery Steps

Discovery steps must:
1. Check no other flow is in progress for the same device
2. Verify device is not already set up
3. Never auto-create entry - always confirm with user

## Reauthentication

```python
async def async_step_reauth(self, entry_data):
    return await self.async_step_reauth_confirm()

async def async_step_reauth_confirm(self, user_input=None):
    if user_input is None:
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({}))
    return await self.async_step_user()
```

In `__init__.py`:
```python
from homeassistant.exceptions import ConfigEntryAuthFailed

async def async_setup_entry(hass, entry):
    try:
        await auth.refresh_tokens()
    except TokenExpiredError as err:
        raise ConfigEntryAuthFailed(err) from err
```

strings.json:
```json
{
  "config": {
    "step": {
      "reauth_confirm": {
        "title": "[%key:common::config_flow::title::reauth%]",
        "description": "Re-authenticate your account"
      }
    },
    "abort": {
      "reauth_successful": "[%key:common::config_flow::abort::reauth_successful%]"
    }
  }
}
```

## Reconfigure

```python
async def async_step_reconfigure(self, user_input=None):
    if user_input is not None:
        self.async_set_unique_id(user_id)
        self._abort_if_unique_id_mismatch()
        return self.async_update_reload_and_abort(
            self._get_reconfigure_entry(), data_updates=data)
    return self.async_show_form(
        step_id="reconfigure",
        data_schema=vol.Schema({vol.Required("host"): str}))
```

## Config Entry Migration

```python
class MyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2
    MINOR_VERSION = 2
```

In `__init__.py`:
```python
async def async_migrate_entry(hass, config_entry):
    if config_entry.version == 1:
        new_data = {**config_entry.data}
        if config_entry.minor_version < 2:
            pass  # migrate 1.1 -> 1.2
        hass.config_entries.async_update_entry(
            config_entry, data=new_data, minor_version=3, version=1)
    return True
```

## Options Flow

```python
@staticmethod
@callback
def async_get_options_flow(config_entry):
    return MyOptionsFlow(config_entry)

class MyOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional("scan_interval", default=self.config_entry.options.get("scan_interval", 30)): int,
            }))
```

## SchemaConfigFlowHandler (Simple Flows)

For helpers and simple integrations:
```python
from homeassistant.helpers.schema_config_entry_flow import (
    SchemaConfigFlowHandler, SchemaFlowFormStep)

class MyFlow(SchemaConfigFlowHandler, domain=DOMAIN):
    config_flow = {
        "user": SchemaFlowFormStep(schema=vol.Schema({
            vol.Required("name"): str,
        })),
    }
```

## Translations

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Connect to Device",
        "data": {"host": "Host", "api_key": "API Key"}
      }
    },
    "error": {
      "cannot_connect": "Failed to connect",
      "invalid_auth": "Invalid authentication"
    },
    "abort": {
      "already_configured": "[%key:common::config_flow::abort::already_configured_device%]"
    }
  }
}
```

Source: https://developers.home-assistant.io/docs/config_entries_config_flow_handler
