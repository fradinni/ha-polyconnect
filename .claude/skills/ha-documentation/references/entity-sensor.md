# Sensor Entity

Derive from `homeassistant.components.sensor.SensorEntity`.

## Key Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| native_value | str/int/float/date/datetime/Decimal/None | **Required** | Sensor value in native unit |
| native_unit_of_measurement | str/None | None | Unit of measurement |
| device_class | SensorDeviceClass/None | None | Sensor type |
| state_class | SensorStateClass/None | None | For long-term statistics |
| options | list[str]/None | None | Possible states (ENUM only) |
| suggested_display_precision | int/None | None | Display decimals |
| suggested_unit_of_measurement | str/None | None | Override auto-conversion |
| last_reset | datetime/None | None | When accumulating sensor was initialized |

## Device Classes (common)

| Class | Units | Description |
|-------|-------|-------------|
| TEMPERATURE | °C, °F, K | Temperature |
| HUMIDITY | % | Relative humidity |
| POWER | W, kW, MW | Power |
| ENERGY | Wh, kWh, MWh | Energy consumption |
| VOLTAGE | V, mV | Voltage |
| CURRENT | A, mA | Current |
| BATTERY | % | Battery percentage |
| PRESSURE | Pa, hPa, bar, psi | Pressure |
| ILLUMINANCE | lx | Light level |
| SIGNAL_STRENGTH | dB, dBm | Signal strength |
| ENUM | (none) | Limited set of states (use `options`) |
| TIMESTAMP | (none) | datetime with timezone |
| DATE | (none) | date object |
| CO2 | ppm | Carbon dioxide |
| MOISTURE | % | Moisture |
| PH | (none) | pH level |
| DISTANCE | m, km, mi | Distance |
| DURATION | s, min, h, d | Duration |
| SPEED | m/s, km/h, mph | Speed |
| VOLUME | L, mL, gal, m³ | Volume |
| VOLUME_FLOW_RATE | L/min, m³/h | Flow rate |
| WATER | L, gal, m³ | Water consumption |
| WEIGHT | kg, g, lb, oz | Mass |
| FREQUENCY | Hz, kHz, MHz | Frequency |

## State Classes

| Class | Description |
|-------|-------------|
| MEASUREMENT | Current measurement (temperature, humidity). Min/max/avg statistics. |
| TOTAL | Running total that can increase/decrease (net energy meter). |
| TOTAL_INCREASING | Monotonically increasing total that may reset to 0 (gas meter, daily consumption). |

**Choosing state_class:**
- Value never resets: `TOTAL` without `last_reset`
- Value resets to 0, only increases: `TOTAL_INCREASING`
- Value resets to 0, can increase/decrease: `TOTAL` with `last_reset` updated on reset

## Restoring State

Use `RestoreSensor` (not `RestoreEntity`):
```python
from homeassistant.components.sensor import RestoreSensor

class MySensor(RestoreSensor):
    async def async_added_to_hass(self):
        last_data = await self.async_get_last_sensor_data()
        if last_data:
            self._attr_native_value = last_data.native_value
            self._attr_native_unit_of_measurement = last_data.native_unit_of_measurement
```

## Best Practices
- Prefer creating additional sensor entities over `extra_state_attributes`
- Use `native_unit_of_measurement` with matching device_class
- Set `state_class` for sensors that should appear in statistics/energy dashboard

Source: https://developers.home-assistant.io/docs/core/entity/sensor
