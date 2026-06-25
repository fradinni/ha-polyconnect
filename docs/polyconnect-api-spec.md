# Polyconnect / Ingeli Platform — API Reference

> Reverse-engineered from `Polyconnect_5.3_APKPure.xapk` (.NET MAUI).  
> Platform: **Ingeli** (`ingeli.fr`). Polytropic is one tenant among many.  
> All endpoints inferred from decompiled .NET assemblies + live probing.

---

## Base URLs

| Server | URL | Notes |
|--------|-----|-------|
| Auth API | `https://auth.pool.mytech-connect.io` | Cloudflare Zero Trust |
| Pairing API | `https://pairing.pool.mytech-connect.io` | Cloudflare Zero Trust |
| MQTT broker | `mqtts://pairing.pool.mytech-connect.io:8883` | VerneMQ, JWT auth |
| Pro portal | `https://mypolyconnect.polytropic.com` | Publicly accessible |

**Cloudflare Zero Trust bypass** — include on every request to auth/pairing:
```
CF-Access-Client-Id: zLT6DV
CF-Access-Client-Secret: NEEJ9S
```

---

## Authentication

All API calls require `Authorization: Bearer <JWT>`.  
**Getting a JWT:** run `get-jwt.py` (mitmproxy capture from the iOS app). The token is the `psp` field in the login response — a custom-encoded opaque token.

### POST /Irc/Application/Login

> ❌ Returns `500` from outside the Kubernetes cluster (confirmed June 2026).
> The handler calls an internal NATS microservice that is not externally reachable.
> Obtain your JWT via the mobile app using `get-jwt.py`.
>
> **Response format** (observed via mitmproxy):
> `{"tid": "<id>", "tpv": 1, "psp": "<token>"}` — the `psp` field is the JWT.

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

Response:
```json
{ "token": "<JWT>", "state": "Success" }
```

`state` values: `Success` · `BadCredentials` · `UserDisabled` · `InactiveUser` · `Unknown`

### POST /Irc/Application/Register

```json
{
  "email": "user@example.com",
  "password": "yourpassword",
  "passwordConfirmation": "yourpassword",
  "applicationEndpointId": "userApp_polytropic"
}
```

Response: `{ "state": "Success" }`  
Additional states: `AlreadyExists` · `RestrictedAccess` · `InvalidEmailFormat`

### POST /Irc/Application/ForgotPassword

```json
{ "email": "user@example.com", "applicationEndpointId": "userApp_polytropic" }
```

Sends a reset email. Reset link: `https://<base>/reset-password-default/<token>`

### GET /Irc/Terminal/RegisterTerminal

Returns a session challenge token (no auth required):

```json
{
  "s": 100,
  "ttk": "<64-byte session token>",
  "ti": "644d053f230b64649a60e524"
}
```

### POST /Irc/Terminal/RegisterTerminal

Registers a physical device to a user account:

```json
{
  "terminalId": "<device-uuid>",
  "applicationEndpointId": "userApp_polytropic",
  "email": "user@example.com",
  "passwordHash": "<bcrypt-hash>",
  "packageName": "com.ckc_net.polytropic",
  "applicationVersion": "5.3"
}
```

### Email / Account Management URLs

| Action | URL |
|--------|-----|
| Confirm email | `https://<base>/confirm-email-default/<token>` |
| Reset password | `https://<base>/reset-password-default/<token>` |
| Delete account | `https://<base>/delete-account/<token>` |

---

## User Management

> ❌ All user management endpoints return `404` from external networks (confirmed June 2026).
> They are only reachable from within the Kubernetes cluster. User data is served
> via the Blazor SignalR WebSocket, not public REST.

All require `Authorization: Bearer <JWT>`.

| Method | Path | Body / Notes |
|--------|------|--------------|
| GET | `/Irc/Application/GetUser` | Returns user profile (cluster-internal) |
| PUT | `/Irc/Application/UpdateUserLanguage` | `{"language": "fr"}` — `fr en de es it nl pt` |
| PUT | `/Irc/Application/UpdateUserTimezone` | `{"timezoneId": "Europe/Paris"}` |
| POST | `/Irc/Application/ChangePassword` | `{"oldPassword":"…","password":"…","passwordConfirmation":"…"}` |
| POST | `/Irc/Application/RequestDeleteAccount` | Sends confirmation email |

---

## Device Pairing (BLE + REST)

Devices use **ESP-IDF Unified Provisioning** over BLE, then register with the cloud.

### BLE Pairing Flow

```
Phone                    Device (ESP32)              Cloud
  │                           │                        │
  │── BLE scan & connect ────>│                        │
  │── Curve25519 handshake ──>│                        │
  │   PoP = "vcetdip48z"      │                        │
  │<─ Session established ────│                        │
  │── Scan WiFi networks ────>│                        │
  │<─ Network list ───────────│                        │
  │── Send SSID + password ──>│                        │
  │<─ Apply OK ───────────────│                        │
  │                           │── Connect to WiFi ────>│
  │                           │<─ MQTT connected ──────│
  │── POST /pairing/set-last-ble-pairing ─────────────>│
  │<─ {success, deviceSecret} ─────────────────────────│
```

**BLE GATT characteristics:** `prov-session` · `prov-scan` · `prov-config` · `custom-data`  
**Security:** Sec1 — Curve25519 ECDH + AES-256-CTR  
**Proof of Possession:** `vcetdip48z`

### POST /pairing/set-last-ble-pairing

```http
POST https://pairing.pool.mytech-connect.io/pairing/set-last-ble-pairing
Authorization: Bearer <JWT>
Content-Type: application/json

{ "deviceId": "<uuid>", "applicationEndpointId": "userApp_polytropic" }
```

Response:
```json
{ "success": true, "deviceSecret": "<mqtt-password>" }
```

### Cloud Config via BLE `custom-data` characteristic

```protobuf
message CmdGetSetDetails {
  string UserID    = 1;  // from JWT
  string SecretKey = 2;  // app secret key
}
message RespGetSetDetails {
  CloudConfigStatus Status = 1;
  string DeviceSecret      = 2;  // becomes MQTT password
}
```

---

## MQTT — Real-time Device Data

**Broker:** `mqtts://pairing.pool.mytech-connect.io:8883`  
**Auth:** `username = <JWT>`, `password = ""`

```python
import paho.mqtt.client as mqtt, ssl

client = mqtt.Client(client_id="my-app")
client.username_pw_set(username=JWT_TOKEN, password="")
client.tls_set(cert_reqs=ssl.CERT_NONE)
client.tls_insecure_set(True)
client.connect("pairing.pool.mytech-connect.io", 8883)
```

### Topic Structure

```
{endpointId}/in/{dataType}/{serialNumber}    ← device → cloud (subscribe)
{endpointId}/out/{dataType}/{serialNumber}   ← cloud → device (publish)
```

Subscribe to everything from one device:
```
userApp_polytropic/in/#/PAC-2024-001
```

Subscribe to all devices in your account:
```
userApp_polytropic/in/#
```

### Data Types (subscribe)

| Topic suffix | Description |
|---|---|
| `Device.Reading.ModbusData` | Modbus register values |
| `Installation.Reading.InstallationStateData` | Full device state snapshot |
| `Device.Reading.BatteryData` | Battery level/voltage |
| `Device.Reading.LevelData` | Water/fluid level sensor |
| `Alarm` | Alarm start/clear events |
| `KpiReading` | KPI instant reading |
| `KpiSummary` | KPI aggregated summary |
| `Reading.LoraLivenessResult` | LoRa device heartbeat |

### Message Payloads

**InstallationStateData** (full device state):
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

**ModbusData** (raw registers):
```json
{
  "endpointId": "userApp_polytropic",
  "serialNumber": "PAC-2024-001",
  "timestamp": "2024-06-24T10:30:00Z",
  "registers": {
    "waterTemperature": 28.5,
    "outsideTemperature": 22.1,
    "setpointTemperature": 30.0,
    "compressorStatus": 1,
    "errorCode": 0,
    "operatingMode": 2,
    "filtrationPumpStatus": 1,
    "heatingPower": 4200
  }
}
```

**Alarm**:
```json
{
  "endpointId": "userApp_polytropic",
  "serialNumber": "PAC-2024-001",
  "timestamp": "2024-06-24T10:30:00Z",
  "alarmType": "HighPressure",
  "alarmLevel": "Critical",
  "value": 42.1,
  "active": true,
  "alarmCategoryIds": ["thermal", "compressor"]
}
```

`alarmLevel` values: `Info` · `Warning` · `Error` · `Critical`

**KpiReading**:
```json
{
  "endpointId": "userApp_polytropic",
  "serialNumber": "PAC-2024-001",
  "timestamp": "2024-06-24T10:30:00Z",
  "kpiType": "EnergyConsumption",
  "kpiData": { "value": 12.4, "unit": "kWh", "interval": "Hourly" }
}
```

**BatteryData / LevelData**:
```json
{ "serialNumber": "SENSOR-001", "batteryLevel": 85, "batteryVoltage": 3.7 }
{ "serialNumber": "LEVEL-001", "level": 1.42, "unit": "m" }
```

### Commands (publish to `out`)

```python
import json
client.publish(
    "userApp_polytropic/out/Device.Command/PAC-2024-001",
    json.dumps({
        "command": "SetSetpoint",
        "setpointTemperature": 28.0,
        "requestId": "req-001"
    }),
    qos=1
)
```

Available commands: `SetSetpoint` · `SetMode` · `Restart` · `StartFiltration` · `StopFiltration`  
Operating modes: `Heating` · `Cooling` · `Auto` · `Off` · `Smart`

---

## Data Models (TypeScript)

```typescript
interface AuthenticationResult {
  token: string | null;
  state: "Success" | "BadCredentials" | "UserDisabled" | "InactiveUser"
       | "AlreadyExists" | "RestrictedAccess" | "InvalidEmailFormat" | "Unknown";
}

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
  cop?: number;                 // Coefficient of Performance
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

interface WiFiAccessPoint {
  ssid: string;
  rssi: number;
  bssid: string;
  channel: number;
  auth: "Open" | "WEP" | "WPA_PSK" | "WPA2_PSK" | "WPA_WPA2_PSK"
      | "WPA2_ENTERPRISE" | "WPA3_PSK" | "WPA2_WPA3_PSK";
  isSecured: boolean;
}
```

---

## Application Endpoint IDs

### User Apps

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

### Pro Apps

| Endpoint ID | Brand |
|-------------|-------|
| `proApp_polytropic` | Polytropic Pro |
| `proApp_ingeli` | Ingeli Pro |
| `proApp_mytechConnect` | MytechConnect Pro |
| `proApp_warmeo` | Warmeo Pro |
| `proApp_pentair` | Pentair Pro |
| `proApp_bht` | BHT Pro |
| `proApp_bluedrops` | BlueDrops Pro |

### Maintainer / Special Apps

| Endpoint ID | Description |
|-------------|-------------|
| `maintainerApp_polytropic` | Polytropic Maintainer |
| `maintainerApp_warmeo` | Warmeo Maintainer |
| `maintainerApp_bht` | BHT Maintainer |
| `maintainerApp_bwt` | BWT Maintainer |
| `proSpaceApp_polytropic` | Pro Space Dashboard |
| `heatPumpApp_default` | Generic heat pump |
| `simulatorApp_polytropic` | Device simulator |
| `extensionApp_mytechConnect` | MytechConnect extension |
| `extensionApp_bwt` | BWT extension |
| `extensionApp_solem` | Solem extension |

---

## Error Handling

| HTTP Status | Meaning |
|-------------|---------|
| `200` | Success |
| `400` | Validation error |
| `401` | Invalid / expired JWT |
| `403` | Insufficient permissions |
| `404` | Not found |
| `500` | Server error (often: NATS unreachable from outside cluster) |

| MQTT rc | Meaning |
|---------|---------|
| `0` | Connected |
| `4` | Bad credentials (JWT invalid/expired) |
| `5` | Not authorized |

---

## Embedded Credentials (APK v5.3)

> ⚠️ Hardcoded in the release APK. May be rotated at any time.

| Key | Value | Purpose |
|-----|-------|---------|
| CF Access Client ID | `zLT6DV` | Cloudflare Zero Trust bypass |
| CF Access Secret | `NEEJ9S` | Cloudflare Zero Trust bypass |
| App signing key | `ZZuo8EMfc93KtDU745gvzw8DsWY0` | JWT signing |
| Transaction key | `ptk08012018` | Device command signing |
| BLE Proof of Possession | `vcetdip48z` | BLE pairing |
| NATS seed | `SUADVFAZSBQLTYMMFSTO6ORGURKGCQ6H4FIYLJTCKLDG5FUJZYAMNZSKIY` | Internal messaging |
| 1nce SIM API client | `81003792_prod` | SIM management |
| 1nce SIM API secret | `7TdF$G$!yzR3cAnM` | SIM management |
| Custom token alphabet | `ABCDEFGHIJ_LaNOPQTSeUVWXYZzMbcxRfghijklm17pqrstuvwdy_K0n23456o89` | Token encoding |

---

## Certificate Pinning

Three SHA1 thumbprints found in `_trustedThumbprints` (IngeliStd.dll):

```
CABD2A79A1076A31F21D253635CB039D4329A5E8
B1D0F09E6A55A4A668CD0F7F58D1D09E509FB3E2
8A01F332C3C547A0DCB5D2AFD0C7FEDA34A93F7D
```

These **do not match** the current Cloudflare-issued server certificates (rotated),
so mitmproxy works without bypassing pinning.

---

*Contact: `app@polyconnect.fr` · `+33 4 78 56 93 94`*
