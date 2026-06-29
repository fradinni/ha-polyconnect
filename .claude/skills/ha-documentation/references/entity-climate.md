# Climate Entity

Derive from `homeassistant.components.climate.ClimateEntity`. Controls temperature, humidity, fans (A/C, heat pumps, humidifiers).

## Key Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| hvac_mode | HVACMode/None | **Required** | Current mode (heat, cool, etc.) |
| hvac_modes | list[HVACMode] | **Required** | Available modes |
| hvac_action | HVACAction/None | None | Current action (heating, cooling, idle) |
| current_temperature | float/None | None | Current temperature |
| target_temperature | float/None | None | Target temperature |
| target_temperature_high | float/None | None | Upper bound (for range) |
| target_temperature_low | float/None | None | Lower bound (for range) |
| target_temperature_step | float/None | None | Step size for temperature |
| current_humidity | float/None | None | Current humidity |
| target_humidity | float/None | None | Target humidity |
| temperature_unit | str | **Required** | UnitOfTemperature.CELSIUS or FAHRENHEIT |
| precision | float | Auto | Temperature precision |
| min_temp | float | 7°C | Minimum temperature |
| max_temp | float | 35°C | Maximum temperature |
| min_humidity | float | 30 | Minimum humidity |
| max_humidity | float | 99 | Maximum humidity |
| fan_mode | str/None | Required by FAN_MODE | Current fan mode |
| fan_modes | list[str]/None | Required by FAN_MODE | Available fan modes |
| preset_mode | str/None | Required by PRESET_MODE | Active preset |
| preset_modes | list[str]/None | Required by PRESET_MODE | Available presets |
| swing_mode | str/None | Required by SWING_MODE | Swing setting |
| swing_modes | list[str]/None | Required by SWING_MODE | Available swing modes |

## HVAC Modes

| Mode | Description |
|------|-------------|
| OFF | Device is off |
| HEAT | Heating to target temperature |
| COOL | Cooling to target temperature |
| HEAT_COOL | Heating/cooling to target range |
| AUTO | Schedule/learned/AI |
| DRY | Dehumidify mode |
| FAN_ONLY | Fan only, no heating/cooling |

## HVAC Actions

| Action | Description |
|--------|-------------|
| OFF | Device off |
| PREHEATING | Preheating |
| HEATING | Actively heating |
| COOLING | Actively cooling |
| DRYING | Drying |
| FAN | Fan running |
| IDLE | Idle (target reached) |
| DEFROSTING | Defrosting |

## Presets (built-in)

NONE, ECO, AWAY, BOOST, COMFORT, HOME, SLEEP, ACTIVITY

## Fan Modes (built-in)

FAN_ON, FAN_OFF, FAN_AUTO, FAN_LOW, FAN_MEDIUM, FAN_HIGH, FAN_MIDDLE, FAN_FOCUS, FAN_DIFFUSE

## Swing Modes

SWING_OFF, SWING_ON, SWING_VERTICAL, SWING_HORIZONTAL, SWING_BOTH

## Supported Features

Combine with `|`:
```python
_attr_supported_features = (
    ClimateEntityFeature.TARGET_TEMPERATURE
    | ClimateEntityFeature.FAN_MODE
    | ClimateEntityFeature.PRESET_MODE
    | ClimateEntityFeature.TURN_ON
    | ClimateEntityFeature.TURN_OFF
)
```

| Feature | Description |
|---------|-------------|
| TARGET_TEMPERATURE | Supports target temperature |
| TARGET_TEMPERATURE_RANGE | Supports temperature range |
| TARGET_HUMIDITY | Supports target humidity |
| FAN_MODE | Supports fan modes |
| PRESET_MODE | Supports presets |
| SWING_MODE | Supports swing |
| SWING_HORIZONTAL_MODE | Supports horizontal swing |
| TURN_ON | Supports turn on |
| TURN_OFF | Supports turn off |

## Methods

```python
class MyClimate(ClimateEntity):
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""

    async def async_set_temperature(self, **kwargs) -> None:
        """Set target temperature. kwargs may include:
        temperature, target_temp_high, target_temp_low, hvac_mode"""

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan mode."""

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset."""

    async def async_set_humidity(self, humidity: int) -> None:
        """Set target humidity."""

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        """Set swing mode."""

    async def async_turn_on(self) -> None:
        """Turn on (set hvac_mode to non-OFF)."""

    async def async_turn_off(self) -> None:
        """Turn off (set hvac_mode to OFF)."""

    async def async_toggle(self) -> None:
        """Toggle (optional, base calls turn_on/turn_off)."""
```

## Example Implementation

```python
class MyHeatPump(ClimateEntity):
    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_preset_modes = [PRESET_NONE, PRESET_ECO, PRESET_COMFORT]
    _attr_min_temp = 15
    _attr_max_temp = 35
    _attr_target_temperature_step = 0.5

    def __init__(self, coordinator, device_id):
        self._coordinator = coordinator
        self._attr_unique_id = f"{device_id}_climate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name="Heat Pump",
        )

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.HEAT if self._coordinator.data["active"] else HVACMode.OFF

    @property
    def current_temperature(self) -> float:
        return self._coordinator.data["water_temperature"]

    @property
    def target_temperature(self) -> float:
        return self._coordinator.data["target_temperature"]

    @property
    def hvac_action(self) -> HVACAction:
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        return HVACAction.HEATING if self._coordinator.data["heating"] else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs):
        await self._coordinator.api.set_temperature(kwargs["temperature"])
        await self._coordinator.async_request_refresh()
```

Source: https://developers.home-assistant.io/docs/core/entity/climate
