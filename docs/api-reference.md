# Polyconnect — API Reference

> Technical reference for the Bridge REST API and the underlying Polyconnect cloud platform.

---

## Part 1: Bridge REST API

The bridge add-on (`polyconnect_bridge`) exposes a local REST API on port **8765**. The HA integration is the primary consumer.

### Base URL

- **Via HA ingress:** `http://<container-ip>:8765` (auto-detected by config flow)
- **Direct access:** `http://homeassistant.local:8765` (if port is exposed)

### Endpoints

#### `GET /health`

Health check — always responds, even without credentials.

**Response:**
```json
{
  "ok": true,
  "service": "polyconnect-bridge",
  "version": "2.0.0",
  "credentials_configured": true,
  "capture_phase": "idle"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `ok` | bool | Always `true` if the bridge is running |
| `credentials_configured` | bool | Whether token + device IDs are captured |
| `capture_phase` | string | `idle`, `running`, or `complete` |

---

#### `GET /status`

Returns the current heat pump state by scraping the Blazor DOM.

**Response (200):**
```json
{
  "waterTemperature": 28.5,
  "outsideTemperature": 22.1,
  "setpointTemperature": 30.0,
  "operatingMode": "Chauffage",
  "regulationMode": "Smart",
  "heatPumpActive": true,
  "compressorRunning": true,
  "filtrationRunning": false,
  "alarmActive": false,
  "alarmMessage": null,
  "errorCode": 0
}
```

| Field | Type | Description |
|-------|------|-------------|
| `waterTemperature` | float\|null | Pool water temperature (°C) |
| `outsideTemperature` | float\|null | Ambient/outside temperature (°C) |
| `setpointTemperature` | float\|null | Target temperature (°C), range 8–32 |
| `operatingMode` | string\|null | `Chauffage`, `Froid`, `Automatique` |
| `regulationMode` | string\|null | `Eco`, `Smart`, `Boost`, or null |
| `heatPumpActive` | bool\|null | Whether the heat pump is powered on |
| `compressorRunning` | bool | Whether the compressor is currently running |
| `filtrationRunning` | bool | Whether the filtration pump is running |
| `alarmActive` | bool | Whether an alarm condition exists |
| `alarmMessage` | string\|null | Alarm text (French), null if no alarm |
| `errorCode` | int | 0 = no error, 1 = alarm active |

**Error responses:**
- `401` — session token expired (`{"error": "...", "auth_expired": true}`)
- `503` — credentials not configured (`{"error": "...", "credentials_missing": true}`)
- `500` — Playwright/DOM error

---

#### `POST /setpoint`

Change the target temperature. The bridge drags the circular slider in the Blazor UI.

**Request:**
```json
{ "temperature": 28.0 }
```

**Response:** `{"ok": true}` or `{"ok": true, "note": "already at target"}`

**Timing:** ~2–4 seconds (slider drag animation + validate button click).

---

#### `POST /mode`

Change the operating mode or regulation preset.

**Request:**
```json
{ "mode": "Chauffage" }
```

**Valid values:**
- Operating modes: `Chauffage`, `Froid`, `Automatique`
- Regulation presets: `Eco`, `Smart`, `Boost`

The bridge navigates to the correct edit page based on the mode type:
- Operating modes → `/heat-pump-edit-mode/<heat_pump_id>`
- Regulation presets → `/heat-pump-edit-power-mode/<heat_pump_id>`

**Timing:** ~4–8 seconds (navigate to edit page, click mode button, click "Valider", navigate back).

---

#### `POST /on`

Turn the heat pump on (clicks the power button if currently off).

**Response:** `{"ok": true}` or `{"ok": true, "note": "already ON"}`

---

#### `POST /off`

Turn the heat pump off (clicks the power button if currently on).

**Response:** `{"ok": true}` or `{"ok": true, "note": "already OFF"}`

---

#### `POST /filtration/start`

Start the filtration pump (clicks the filtration toggle if currently off).

**Response:** `{"ok": true}` or `{"ok": true, "note": "already running"}`

---

#### `POST /filtration/stop`

Stop the filtration pump (clicks the filtration toggle if currently on).

**Response:** `{"ok": true}` or `{"ok": true, "note": "already stopped"}`

---

### Capture API

These endpoints manage the credential capture process (mitmproxy-based).

#### `GET /capture/status`

Returns current capture state and credential info.

#### `POST /capture/start`

Starts the mitmproxy capture process and the phone-facing setup UI on port 8080.

#### `POST /capture/stop`

Stops capture and the setup UI.

#### `POST /capture/reset`

Clears all stored credentials. Next start will require recapture.

---

### Ingress Panel (`GET /`)

Returns an HTML control panel for the HA ingress sidebar. Shows:
- Credential status (token, heat pump ID, installation ID)
- Capture controls (start/stop/reset)
- Phone URL for capture setup
- Bridge status

---

## Part 2: Polyconnect Cloud Platform (Reverse-Engineered)

> Reverse-engineered from `Polyconnect_5.3_APKPure.xapk` (.NET MAUI).
> Platform: **Ingeli** (`ingeli.fr`). Polytropic is one tenant among many.
> All endpoints inferred from decompiled .NET assemblies + live probing.

### Base URLs

| Server | URL | Notes |
|--------|-----|-------|
| Auth API | `https://auth.pool.mytech-connect.io` | Cloudflare Zero Trust |
| Pairing API | `https://pairing.pool.mytech-connect.io` | Cloudflare Zero Trust |
| User App | `https://polytropic.user-app.pool.mytech-connect.io` | Blazor Server app |
| MQTT Broker | `mqtts://pairing.pool.mytech-connect.io:8883` | VerneMQ, JWT auth |
| Pro Portal | `https://mypolyconnect.polytropic.com` | Publicly accessible |

**Cloudflare Zero Trust bypass** — required on every request to auth/pairing:
```
CF-Access-Client-Id: zLT6DV
CF-Access-Client-Secret: NEEJ9S
```

---

### Authentication

#### POST /Irc/Application/Login

> **Status:** Returns `500` from outside the Kubernetes cluster (confirmed June 2026).
> The handler forwards to an internal NATS JetStream microservice that is not externally reachable.
> Obtain your token via the mobile app using `capture.py` or `get-jwt.py`.

**Request:**
```http
POST https://auth.pool.mytech-connect.io/Irc/Application/Login
Content-Type: application/json
CF-Access-Client-Id: zLT6DV
CF-Access-Client-Secret: NEEJ9S

{
  "email": "user@example.com",
  "password": "yourpassword",
  "applicationEndpointId": "userApp_polytropic"
}
```

**Response (observed via mitmproxy):**
```json
{ "tid": "<transaction-id>", "tpv": 1, "psp": "<session-token>" }
```

The `psp` field is the session token used for all subsequent calls. It is a custom-encoded opaque token (not a standard `eyJ...` JWT).

**State values:** `Success` · `BadCredentials` · `UserDisabled` · `InactiveUser` · `Unknown`

---

### True Architecture: Blazor Server SignalR

**There is no REST API for device data or control.** Everything goes through a Blazor Server SignalR WebSocket connection.

```
Mobile App
  │
  ├─ POST /Irc/Application/Login  → gets psp session token
  │
  └─ Opens WebView → polytropic.user-app.pool.mytech-connect.io/from-native/<token>
       │
       ├─ GET /_blazor/initializers
       ├─ POST /_blazor/negotiate?negotiateVersion=1  → connectionToken
       └─ WebSocket wss://.../_blazor?id=<token>
            │
            ├─ Client → Server: ConnectCircuit, DispatchEventAsync
            └─ Server → Client: JS.RenderBatch, JS.BeginInvokeJS
                                 └─ Server internally calls NATS/MQTT
```

**Key insight:** `DispatchEventAsync` handler IDs are dynamic per-session — they're assigned by the Blazor server at render time and cannot be hardcoded. This is why we use DOM scraping (Playwright) instead of raw WebSocket commands.

---

### Blazor Page Routes

| Page | URL Pattern |
|------|-------------|
| Session entry | `/from-native/<token>` |
| Installation overview | `/installation-overview/<installation_id>` |
| Heat pump view | `/heat-pump-view/<heat_pump_id>` |
| Edit operating mode | `/heat-pump-edit-mode/<heat_pump_id>` |
| Edit regulation mode | `/heat-pump-edit-power-mode/<heat_pump_id>` |
| Devices management | `/devices-management/<device_mgmt_id>` |
| Pool info | `/pool-info-edit/<device_mgmt_id>` |
| Support | `/support/<heat_pump_id>` |
| Account | `/account` |

---

### MQTT (Not Used by Integration)

> Port 8883 is **firewall-blocked** from external networks. MQTT is only accessible from the mobile device's network context. Documented here for completeness.

**Broker:** `mqtts://pairing.pool.mytech-connect.io:8883`
**Auth:** `username = <session_token>`, `password = ""`

#### Topic Structure

```
{endpointId}/in/{dataType}/{serialNumber}    ← subscribe (device → cloud)
{endpointId}/out/{dataType}/{serialNumber}   ← publish (cloud → device)
```

#### Data Types

| Topic Suffix | Description |
|---|---|
| `Device.Reading.ModbusData` | Modbus register values |
| `Installation.Reading.InstallationStateData` | Full device state snapshot |
| `Device.Reading.BatteryData` | Battery level/voltage |
| `Device.Reading.LevelData` | Water/fluid level sensor |
| `Alarm` | Alarm start/clear events |
| `KpiReading` | KPI instant reading |
| `KpiSummary` | KPI aggregated summary |

#### InstallationStateData Payload

```json
{
  "endpointId": "userApp_polytropic",
  "serialNumber": "PAC-2024-001",
  "timestamp": "2024-06-24T10:30:00Z",
  "waterTemperature": 28.5,
  "setpointTemperature": 30.0,
  "outsideTemperature": 22.1,
  "operatingMode": "Heating",
  "compressorRunning": true,
  "filtrationRunning": true,
  "alarmActive": false,
  "errorCode": 0,
  "cop": 4.2,
  "powerConsumptionW": 1050
}
```

#### Commands (publish to `out`)

Available commands: `SetSetpoint` · `SetMode` · `Restart` · `StartFiltration` · `StopFiltration`
Operating modes: `Heating` · `Cooling` · `Auto` · `Off` · `Smart`

---

### Device Pairing (BLE)

Devices use **ESP-IDF Unified Provisioning** over BLE:

1. Phone connects via BLE to the ESP32 in the heat pump
2. Curve25519 ECDH handshake (Sec1 + AES-256-CTR)
3. Proof of Possession: `vcetdip48z`
4. Phone sends WiFi credentials to device
5. Device connects to WiFi → MQTT broker
6. Phone calls `POST /pairing/set-last-ble-pairing` → gets `deviceSecret` (MQTT password)

**BLE GATT characteristics:** `prov-session` · `prov-scan` · `prov-config` · `custom-data`

---

### Application Endpoint IDs

#### User Apps

| Endpoint ID | Brand |
|-------------|-------|
| `userApp_polytropic` | **Polytropic / Polyconnect** |
| `userApp_warmeo` | Warmeo |
| `userApp_mytechConnect` | MytechConnect |
| `userApp_ingeli` | Ingeli |
| `userApp_pentair` | Pentair |
| `userApp_bht` | BHT / Windhager |
| `userApp_bluedrops` | BlueDrops |
| `userApp_livePool` | LivePool |

#### Pro Apps

| Endpoint ID | Brand |
|-------------|-------|
| `proApp_polytropic` | Polytropic Pro |
| `proApp_ingeli` | Ingeli Pro |
| `proApp_mytechConnect` | MytechConnect Pro |
| `proApp_warmeo` | Warmeo Pro |
| `proApp_pentair` | Pentair Pro |
| `proApp_bht` | BHT Pro |
| `proApp_bluedrops` | BlueDrops Pro |

#### Special Apps

| Endpoint ID | Description |
|-------------|-------------|
| `maintainerApp_polytropic` | Polytropic Maintainer |
| `proSpaceApp_polytropic` | Pro Space Dashboard |
| `heatPumpApp_default` | Generic heat pump |
| `simulatorApp_polytropic` | Device simulator |

---

### Data Models

```typescript
interface InstallationStateData {
  endpointId: string;
  serialNumber: string;
  timestamp: string;            // ISO 8601
  waterTemperature: number;     // °C
  setpointTemperature: number;  // °C
  outsideTemperature: number;   // °C
  operatingMode: "Heating" | "Cooling" | "Auto" | "Off" | "Smart";
  compressorRunning: boolean;
  filtrationRunning: boolean;
  alarmActive: boolean;
  errorCode: number;
  cop?: number;
  powerConsumptionW?: number;
}

interface AlarmData {
  endpointId: string;
  serialNumber: string;
  timestamp: string;
  alarmType: string;
  alarmLevel: "Info" | "Warning" | "Error" | "Critical";
  value?: number;
  active: boolean;
  alarmCategoryIds: string[];
}
```

---

### Error Handling

| HTTP Status | Meaning |
|-------------|---------|
| `200` | Success |
| `400` | Validation error |
| `401` | Invalid / expired token |
| `403` | Insufficient permissions |
| `404` | Not found (or cluster-internal only) |
| `500` | Server error (often: NATS unreachable from outside) |

| MQTT rc | Meaning |
|---------|---------|
| `0` | Connected |
| `4` | Bad credentials (token invalid/expired) |
| `5` | Not authorized |
