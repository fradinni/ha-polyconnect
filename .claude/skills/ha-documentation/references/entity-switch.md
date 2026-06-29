# Switch Entity

Derive from `homeassistant.components.switch.SwitchEntity`.

Use for controllable on/off devices. For read-only on/off state, use binary_sensor. For momentary actions, use button or event.

## Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| is_on | bool | None | If the switch is currently on or off |

## Methods

```python
class MySwitch(SwitchEntity):
    async def async_turn_on(self, **kwargs):
        """Turn on."""

    async def async_turn_off(self, **kwargs):
        """Turn off."""

    async def async_toggle(self, **kwargs):
        """Toggle (optional, defaults to checking is_on)."""
```

## Device Classes

| Class | Description |
|-------|-------------|
| OUTLET | Power outlet |
| SWITCH | Generic switch |

Source: https://developers.home-assistant.io/docs/core/entity/switch
