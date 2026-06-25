# ha-polyconnect — Home Assistant Integration for Polyconnect

Control and monitor your **Polyconnect / Ingeli pool heat pump** directly from Home Assistant.

> **Note:** The Polyconnect backend has no public REST API. All device interaction
> goes through a Blazor Server SignalR WebSocket. This integration uses a bridge
> server (headless Chromium via Playwright) to read state and send commands.

---

## Architecture

```
Home Assistant                    Bridge Server (Playwright)
┌────────────────────────────┐    ┌───────────────────────────────────┐
│ custom_components/          │    │ polyconnect_bridge/ (HA Add-on)   │
│ polyconnect/api.py          │───▶│ server.py + playwright_worker.py  │
│ (aiohttp HTTP client)       │    │ Flask :8765                       │
└────────────────────────────┘    │                                   │
                                   │  OR                               │
                                   │ scripts/polyconnect-server.py     │
                                   │ (standalone, persistent browser)  │
                                   └───────────────────────────────────┘
                                              │
                                              ▼
                                   https://polytropic.user-app.pool.
                                   mytech-connect.io  (Blazor SignalR)
```

Two bridge deployment options:

| Option | Where | Best for |
|--------|-------|----------|
| **Polyconnect Bridge add-on** (recommended) | `polyconnect_bridge/` — runs inside HA OS | HA OS / Supervised |
| **Standalone server** | `scripts/polyconnect-server.py` — runs on any Linux machine | Bare HA / Docker |

---

## Prerequisites

### 1. Session token

You need a `psp` session token from the Polyconnect iOS app.
Capture it using the included script:

```bash
cd ~/Desktop/ha-polyconnect/scripts
pip install mitmproxy
python3 get-jwt.py
```

Follow the on-screen instructions (sets your iPhone as a proxy, captures the JWT automatically).
The token is saved to `scripts/captured_token.txt`.

> Tokens expire after a few hours. Re-run `get-jwt.py` to refresh.

### 2. Bridge server

**Option A — HA Add-on (recommended for HA OS):**
1. In HA: Settings → Add-ons → Store → ⋮ → Repositories
2. Add: `https://github.com/your-user/ha-polyconnect`
3. Install **Polyconnect Bridge**, configure token in the add-on options
4. Start the add-on

**Option B — Standalone server (any Linux machine):**
```bash
pip install flask playwright
python3 -m playwright install chromium
cd ~/Desktop/ha-polyconnect/scripts
python3 polyconnect-server.py
# Runs on 0.0.0.0:8765 — use http://<linux-ip>:8765 as bridge URL
```

---

## Installation

### Via HACS (recommended)

#### Add Custom Repository:
[![🔌 Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fradinni&repository=ha-polyconnect&category=integration)
  
OR
  
1. Open HACS → ⋮ → Custom repositories
2. Add: `https://github.com/fradinni/ha-polyconnect` as **Integration**

#### Install
1. Open HACS 
2. Search for "Polyconnect" and install
3. Restart Home Assistant

### Manual

```bash
cp -r custom_components/polyconnect ~/.homeassistant/custom_components/
# Restart HA
```

---

## Configuration

1. HA → Settings → Devices & Services → Add Integration → **Polyconnect**
2. Enter the bridge URL (auto-detected if the add-on is running)
3. Set scan interval (default: 60 seconds)

### Token refresh

When you see authentication errors:
1. Re-run `scripts/get-jwt.py`
2. Update the token in the add-on options (or restart `polyconnect-server.py`)
3. HA will recover automatically on the next poll

---

## Entities

| Entity | Type | Description |
|--------|------|-------------|
| `climate.polyconnect_heat_pump` | Climate | Set target temperature (8–32°C) and operating mode |
| `sensor.polyconnect_water_temperature` | Sensor | Current water temperature (°C) |
| `sensor.polyconnect_setpoint_temperature` | Sensor | Target setpoint (°C) |
| `sensor.polyconnect_outside_temperature` | Sensor | Outdoor air temperature (°C) |
| `sensor.polyconnect_power_consumption` | Sensor | Power consumption (W) |
| `sensor.polyconnect_coefficient_of_performance` | Sensor | COP efficiency ratio |
| `sensor.polyconnect_operating_mode` | Sensor | Raw operating mode string |
| `sensor.polyconnect_regulation_mode` | Sensor | Regulation preset (Eco/Smart/Boost) |
| `sensor.polyconnect_alarm_message` | Sensor | Alarm text if active |
| `binary_sensor.polyconnect_compressor` | Binary | Compressor running state |
| `binary_sensor.polyconnect_alarm` | Binary | Active alarm indicator |
| `binary_sensor.polyconnect_filtration_pump` | Binary | Heat pump power on state |
| `switch.polyconnect_power` | Switch | Heat pump power on/off |

### Operating Modes (Climate)

| HA Mode | Polyconnect Mode |
|---------|-----------------|
| Heat | Chauffage |
| Cool | Froid |
| Auto | Automatique |

### Regulation Presets

| Preset | Description |
|--------|-------------|
| Eco | Eco regulation |
| Smart | Smart regulation |
| Boost | Boost mode |

---

## Project Structure

```
ha-polyconnect/
├── README.md
├── hacs.json
├── docs/
│   ├── polyconnect-api-spec.md     API reference (endpoints, MQTT, Blazor)
│   └── polyconnect-findings.md     Reverse engineering notes and security findings
├── scripts/
│   ├── get-jwt.py                  Interactive token capture (mitmproxy + iOS)
│   └── polyconnect-server.py       Standalone bridge server (persistent browser)
├── custom_components/polyconnect/
│   ├── __init__.py                 Integration setup/teardown
│   ├── manifest.json               Integration metadata
│   ├── api.py                      HTTP client for the bridge server
│   ├── coordinator.py              DataUpdateCoordinator (polls bridge every N seconds)
│   ├── entity.py                   Shared base entity class
│   ├── config_flow.py              UI setup wizard (auto-detects add-on)
│   ├── const.py                    Constants and mode mappings
│   ├── sensor.py                   Temperature / power / mode sensors
│   ├── climate.py                  Heat pump thermostat (setpoint + modes + presets)
│   ├── binary_sensor.py            Running states and alarm indicator
│   ├── switch.py                   Filtration pump switch
│   └── strings.json                UI labels
└── polyconnect_bridge/             HA Supervisor add-on
    ├── config.yaml                 Add-on manifest
    ├── Dockerfile                  python:3.12-slim-bookworm (glibc, Playwright works)
    ├── requirements.txt
    ├── run.sh                      Reads token from add-on options, starts server
    ├── server.py                   Flask REST bridge (dispatches to worker)
    └── playwright_worker.py        Subprocess Playwright worker (all commands)
```

---

## Known Limitations

- **Token expiry**: Session tokens expire after a few hours. Must be refreshed manually.
- **Slow operations**: Each command spawns a fresh Chromium browser (~5–15s).
  Increase the scan interval to reduce resource usage.
- **No MQTT**: Port 8883 is firewall-blocked externally. MQTT would allow real-time
  push updates but is unreachable from outside the Ingeli Kubernetes cluster.
- **Blazor DOM scraping**: Reads UI state via CSS selectors. If Polytropic updates
  their app, selectors may need updating.
- **Water temperature**: Displayed as `-` in the app when the pump is off or water
  flow is insufficient — `waterTemperature` will be `null` in that case.

---

## API Reference

See [`docs/polyconnect-api-spec.md`](docs/polyconnect-api-spec.md) for the full
reverse-engineered API reference (auth flow, Blazor SignalR, MQTT topics, discovered IDs).

See [`docs/polyconnect-findings.md`](docs/polyconnect-findings.md) for security findings
and infrastructure details discovered from the APK.
