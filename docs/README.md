# Polyconnect — Home Assistant Integration

> Control and monitor **Polyconnect / Ingeli pool heat pumps** from Home Assistant.

---

## How It Works

The Polyconnect cloud platform has **no public REST API**. All device interaction happens through a Blazor Server SignalR WebSocket rendered inside a mobile WebView. This integration works around that by running a headless Chromium browser (Playwright) inside a local HA add-on, which scrapes the Blazor UI for data and simulates clicks to send commands.

```
┌─────────────────────────────────────────────────────────────────────┐
│ Home Assistant                                                       │
│                                                                     │
│   ┌─────────────────────┐        ┌──────────────────────────────┐  │
│   │  Polyconnect         │  HTTP  │  Polyconnect Bridge Add-on   │  │
│   │  Integration         │◄──────►│  (Flask + Playwright)        │  │
│   │                     │        │                              │  │
│   │  • Climate entity   │        │  • Headless Chromium         │  │
│   │  • 6 Sensors        │        │  • DOM scraping (status)     │  │
│   │  • 4 Binary sensors │        │  • Simulated clicks (cmds)   │  │
│   │  • 1 Switch         │        │  • Credential capture (MITM) │  │
│   └─────────────────────┘        └───────────────┬──────────────┘  │
│                                                   │                 │
└───────────────────────────────────────────────────┼─────────────────┘
                                                    │ HTTPS (WebSocket)
                                          ┌─────────▼─────────────┐
                                          │  Polyconnect Cloud     │
                                          │  (Blazor SignalR)      │
                                          │  polytropic.user-app.  │
                                          │  pool.mytech-connect.io│
                                          └────────────────────────┘
```

### Data Flow

1. **Polling** (every 60s by default): Integration → `GET /status` → Bridge scrapes the Blazor DOM → returns JSON with all sensor data.
2. **Commands** (setpoint, mode, on/off): Integration → `POST /setpoint` etc. → Bridge navigates to the Blazor edit page, clicks the right buttons, confirms, then navigates back.
3. **Credential capture**: First-time setup where the user's phone traffic is proxied through mitmproxy to extract the session token and device IDs.

---

## Components

### Integration (`custom_components/polyconnect/`)

| File | Purpose |
|------|---------|
| `__init__.py` | Entry setup/unload, stores coordinator on `entry.runtime_data` |
| `config_flow.py` | Auto-discovers bridge add-on via Supervisor API, validates connectivity |
| `coordinator.py` | `DataUpdateCoordinator` — polls bridge every N seconds, creates repair issues on auth failure |
| `api.py` | Async HTTP client (`aiohttp`) talking to bridge REST endpoints |
| `entity.py` | Base `CoordinatorEntity` with shared `DeviceInfo` |
| `climate.py` | Thermostat: heat/cool/auto modes + Eco/Smart/Boost presets |
| `sensor.py` | 8 sensors (temperatures, power, COP, mode, regulation, alarm) |
| `binary_sensor.py` | 3 binary sensors (compressor, alarm, filtration) |
| `switch.py` | Power on/off switch |
| `const.py` | All constants, mode mappings |

**Version:** 2.0.0 · **IoT class:** `local_polling` · **Platforms:** sensor, climate, binary_sensor, switch

### Bridge Add-on (`polyconnect_bridge/`)

| File | Purpose |
|------|---------|
| `server.py` | Flask REST API + Playwright controller + ingress panel |
| `capture_manager.py` | Credential capture lifecycle (mitmproxy orchestration) |
| `setup_ui.py` | Phone-facing setup wizard served on port 8080 during capture |
| `mitm_addon.py` | mitmproxy addon that intercepts and extracts the session token |
| `config.yaml` | Add-on manifest (ports, schema, ingress) |
| `Dockerfile` | Debian + Chromium + Python |
| `run.sh` | Startup script |

**Version:** 2.0.0 · **Ports:** 8765 (API/ingress)

---

## Entities

### Climate Entity — `climate.polyconnect_heat_pump`

| Feature | Details |
|---------|---------|
| HVAC Modes | `heat` (Chauffage), `cool` (Froid), `auto` (Automatique) |
| Presets | Eco, Smart, Boost |
| Temperature range | 8–32°C, step 1°C |
| Turn on/off | Yes (via `ClimateEntityFeature.TURN_ON/TURN_OFF`) |

> **Note:** `HVACMode.OFF` is deliberately excluded from the mode selector. The heat pump on/off is a separate power button action, not a mode change.

**Optimistic updates:** Mode/preset changes update the UI immediately, then confirm with a delayed refresh after 8 seconds (Blazor re-render time).

### Sensors

| Entity | Data Key | Unit | Device Class |
|--------|----------|------|--------------|
| Water Temperature | `waterTemperature` | °C | temperature |
| Outside Temperature | `outsideTemperature` | °C | temperature |
| Setpoint Temperature | `setpointTemperature` | °C | temperature |
| Operating Mode | `operatingMode` | — | — |
| Regulation Mode | `regulationMode` | — | — |
| Alarm Message | `alarmMessage` | — | — |

### Binary Sensors

| Entity | Data Key | Device Class |
|--------|----------|--------------|
| Fan | `fanRunning` | running |
| Filtration Pump | `filtrationRunning` | running |
| Defrost | `defrostActive` | running |
| Alarm | `alarmActive` | problem |

### Switch

| Entity | Data Key | Action |
|--------|----------|--------|
| Power | `heatPumpActive` | POST `/on` or `/off` |

---

## Setup Flow

### Prerequisites

1. A working Polyconnect heat pump connected to the Polyconnect app
2. Home Assistant OS or Supervised (required for the add-on)
3. Phone on the same WiFi as HA (for credential capture)

### Steps

1. **Install the Bridge add-on** (via the local add-on repository)
2. **Start the add-on** — it exposes an ingress panel in the HA sidebar
3. **Capture credentials:**
   - Open the add-on panel in HA
   - Click "Start Capture"
   - On your phone, connect to the proxy shown (http://your-ha-ip:8080)
   - Follow the wizard — open the Polyconnect app while proxied
   - The bridge intercepts the session token and device IDs
4. **Add the integration** — go to Settings → Integrations → Add → "Polyconnect"
   - The bridge URL is auto-detected if running on the same machine
   - Config flow validates that the bridge is reachable and credentials are captured
5. **Done** — entities appear under a "Polyconnect Heat Pump" device

### Options

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| Scan interval | 1 min | 1–60 min | How often to poll the bridge for status |

---

## Config Flow Details

The config flow (`config_flow.py`) performs these checks:

1. **Auto-detect bridge URL** — queries the Supervisor REST API for an installed add-on with slug ending in `polyconnect_bridge`, gets its container IP, and constructs `http://<ip>:8765`
2. **Health check** — calls `GET /health` on the bridge
3. **Credential check** — verifies `credentials_configured: true` in the health response
4. **Unique ID** — uses the bridge URL as unique_id (prevents duplicate entries)

### Error States

| Error | Meaning |
|-------|---------|
| `cannot_connect` | Bridge add-on not running or unreachable |
| `credentials_missing` | Bridge running but no credentials captured yet |
| `auth_expired` | Session token expired — recapture needed |

When auth expires at runtime, the coordinator creates a **repair issue** (visible in Settings → Repairs) prompting the user to recapture credentials.

---

## Mode Mappings

### HVAC Modes (Polyconnect → HA)

| App Label | HA Mode | Notes |
|-----------|---------|-------|
| Chauffage | `heat` | Heating |
| Froid | `cool` | Cooling (shown as "Climatisation" in some views) |
| Automatique | `auto` | Automatic |
| Heating | `heat` | English firmware variant |
| Cooling | `cool` | English firmware variant |
| Auto | `auto` | English firmware variant |
| Off / Eteint | — | Not mapped as a mode; power is handled via switch |

### Regulation Presets (HA → Bridge Command)

| Preset | Bridge Command | Description |
|--------|---------------|-------------|
| Eco | `"Eco"` | Energy-saving regulation |
| Smart | `"Smart"` | Balanced regulation |
| Boost | `"Boost"` | Maximum performance |

---

## Error Handling

```
PolyconnectError (base)
├── AuthExpiredError      → ConfigEntryAuthFailed (HA reauth flow + repair issue)
└── CredentialsMissingError → UpdateFailed (coordinator stops polling, shows warning)
```

| Bridge HTTP Status | Integration Behavior |
|--------------------|---------------------|
| 200 | Success — parse JSON response |
| 401 | `AuthExpiredError` → repair issue created |
| 503 + `credentials_missing` | `CredentialsMissingError` → repair issue |
| Connection refused | `PolyconnectError` → "Is the add-on running?" |
| Any other error | `PolyconnectError` → `UpdateFailed` |
