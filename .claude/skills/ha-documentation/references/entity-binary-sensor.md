# Binary Sensor Entity

Derive from `homeassistant.components.binary_sensor.BinarySensorEntity`.

## Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| is_on | bool/None | None | **Required**. Current on/off state |
| device_class | BinarySensorDeviceClass/None | None | Type of binary sensor |

## Device Classes

| Class | On means | Off means |
|-------|----------|-----------|
| BATTERY | Low | Normal |
| BATTERY_CHARGING | Charging | Not charging |
| CO | CO detected | Clear |
| COLD | Cold | Normal |
| CONNECTIVITY | Connected | Disconnected |
| DOOR | Open | Closed |
| GARAGE_DOOR | Open | Closed |
| GAS | Gas detected | Clear |
| HEAT | Hot | Normal |
| LIGHT | Light detected | No light |
| LOCK | Unlocked | Locked |
| MOISTURE | Wet | Dry |
| MOTION | Motion detected | Clear |
| MOVING | Moving | Stopped |
| OCCUPANCY | Occupied | Clear |
| OPENING | Open | Closed |
| PLUG | Plugged in | Unplugged |
| POWER | Power detected | No power |
| PRESENCE | Home | Away |
| PROBLEM | Problem detected | OK |
| RUNNING | Running | Not running |
| SAFETY | Unsafe | Safe |
| SMOKE | Smoke detected | Clear |
| SOUND | Sound detected | Clear |
| TAMPER | Tampering detected | Clear |
| VIBRATION | Vibration detected | No vibration |
| WINDOW | Open | Closed |

Source: https://developers.home-assistant.io/docs/core/entity/binary-sensor
