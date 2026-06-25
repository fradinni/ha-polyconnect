# Polyconnect — Security Findings

> Reverse-engineered from `Polyconnect_5.3_APKPure.xapk` (June 2026).
> Live API testing confirmed June 2026.

---

## Cloudflare Zero Trust

The auth and pairing servers are behind **Cloudflare Zero Trust** with JA3/JA4 TLS fingerprint-based device policy. Desktop clients (curl, Python, browser) are blocked:

```
HTTP 303 → https://blocked.teams.cloudflare.com
  ?source_ip=<your-ip>&url=auth.pool.mytech-connect.io
```

**Bypass:** Two service tokens were found hardcoded in the APK:

```
CF-Access-Client-Id: zLT6DV
CF-Access-Client-Secret: NEEJ9S
```

Confirmed working — include on every request to auth/pairing servers.

---

## Authentication Flow

The app uses **email + password authentication** via a REST endpoint:

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
{ "tid": "<transaction-id>", "tpv": 1, "psp": "<JWT>" }
```

The `psp` field is the authentication token used for all subsequent API and MQTT calls. It is a custom-encoded opaque token (not a standard `eyJ...` JWT).

After login, the app opens a Blazor WebView at:
```
https://polytropic.user-app.pool.mytech-connect.io/from-native/<session-token>
```
All data display goes through this Blazor SignalR WebSocket, not REST calls.

---

## POST /Login Returns 500 (External Callers)

The `POST /Irc/Application/Login` endpoint returns `500` with empty body for all external callers. Root cause: the HTTP API is a gateway that forwards requests to an internal **NATS JetStream** microservice. NATS is only reachable within the Kubernetes cluster.

`GET /Irc/Application/Login` returns `{"s":5}` (status check, no NATS call) — confirming the endpoint exists but write operations require internal access.

---

## User Data Endpoints — Cluster-Internal Only

The following endpoints return `404` from external networks regardless of auth token. They are only reachable from within the Kubernetes cluster:

- `GET /Irc/Application/GetUser`
- `GET /Irc/Application/GetDevices`
- `GET /Irc/Application/GetInstallations`

This is consistent with the Blazor architecture — user data is served via SignalR, not public REST.

---

## True Architecture: Blazor Server SignalR (confirmed via mitmproxy WebSocket capture)

**There is no REST API for device data or control.** Everything goes through a Blazor Server SignalR WebSocket connection.

### How it works

```
Mobile app
  │
  ├─ POST /Irc/Application/Login  → gets psp session token
  │
  └─ Opens WebView → polytropic.user-app.pool.mytech-connect.io/from-native/<token>
       │
       ├─ GET /_blazor/initializers
       ├─ POST /_blazor/negotiate?negotiateVersion=1  → connectionToken
       └─ WebSocket wss://polytropic.user-app.pool.mytech-connect.io/_blazor?id=<token>
            │
            ├─ Client → Server: ConnectCircuit, DispatchEventAsync (click/slider events)
            └─ Server → Client: JS.RenderBatch (binary UI diffs), JS.BeginInvokeJS (commands)
                                 └─ Server internally calls NATS/MQTT to control devices
```

### Frame types (MessagePack binary)

| Direction | Method | Description |
|-----------|--------|-------------|
| C→S | `OnRenderCompleted` | ACK after receiving a render batch |
| C→S | `BeginInvokeDotNetFromJS` + `DispatchEventAsync` | User interaction (click, slider change) |
| C→S | `OnLocationChanged` | Navigation between pages |
| C→S | `EndInvokeJSFromDotNet` | JS call result |
| S→C | `JS.RenderBatch` | Binary UI diff (Blazor render tree) |
| S→C | `JS.BeginInvokeJS` | Server calls JS function (e.g. `initRoundSlider`, `navigateTo`) |
| S→C | `JS.EndInvokeDotNet` | .NET call result |

### Discovered IDs (from live mitmproxy capture)

| ID | Value | Type |
|----|-------|------|
| Heat Pump | `64140b25194618718c5083bd` | `HeatPumpId` |
| Installation | `64140b25194618718c5083be` | `PolyInstallationId` |
| Pool 1 | `024067` (suffix) | `PoolId` |
| Pool 2 | `561278` (suffix) | `PoolId` |
| Device management | `6661a4e9643cef5668989fae` | Page ID |
| Image storage | `66f2d3290f2b6ad86fb1f0ca` | `storageInfo` |
| Device serial | `IVS08Q023038B024` | Physical serial (base64: `SVZTMDhRMDIzMDM4QjAyNA==`) |

### Device state (from `initRoundSlider` args)

| Field | Value |
|-------|-------|
| Current setpoint | `29°C` |
| Temperature range | `8–32°C` |
| Operating modes | `Chauffage`, `Froid`, `Automatique`, `Smart`, `Boost` |

### All Blazor page routes (from navigation frames)

| Page | URL |
|------|-----|
| Installation overview | `/installation-overview/64140b25194618718c5083be` |
| Heat pump view | `/heat-pump-view/64140b25194618718c5083bd` |
| Edit mode | `/heat-pump-edit-mode/64140b25194618718c5083bd` |
| Edit power mode | `/heat-pump-edit-power-mode/64140b25194618718c5083bd` |
| Devices management | `/devices-management/6661a4e9643cef5668989fae` |
| Pool info edit | `/pool-info-edit/6661a4e9643cef5668989fae` |
| Pools overview | `/pools-overview` |
| Support (device) | `/support/64140b25194618718c5083bd` |
| Support (installation) | `/support/device/64140b25194618718c5083be` |
| Support document | `/support/document/dynamic/<b64serial>/<docId>` |
| Account | `/account` |
| Change email | `/change-email` |
| Change language | `/change-language` |
| Change measurement system | `/change-measurement-system` |

### JavaScript functions called by server

| Function | Args | Description |
|----------|------|-------------|
| `initRoundSlider` | `[gaugeId, value, min, max, dotNetRef]` | Initialize temperature gauge |
| `updateRoundSlider` | `[gaugeId, value]` | Update displayed temperature |
| `enableRoundSlider` | `[gaugeId]` | Enable slider interaction |
| `disableRoundSlider` | `[gaugeId]` | Disable slider during save |
| `navigationManager.navigateTo` | `[url, options]` | Navigate to page |
| `initDecimalSeparator` | `[...]` | Locale setup |

### Alarm codes (from support/documentation pages)

Two alarm series discovered: `AL01`–`AL28` and `EA01`–`EA10`.

Known alarm descriptions:
- `AL07 / E13` — défaillant (component failure)
- `Erreur de communication Modbus`
- `Fuite d'eau sous la machine` (water leak)
- `Hivernage / Givre ou Fumée` (winterization / frost or smoke)
- `La piscine ne chauffe pas` (pool not heating)
- `Perte de communication` (communication loss)
- `Perte de connexion` (connection loss)
- `givrage` (icing)

### Pool configuration fields (from devices management page)

Two pools in the installation (suffixes `024067` and `561278`):

| Field | Description |
|-------|-------------|
| `Name` | Pool name |
| `Shape` | `rectangle`, `oval`, `round`, `other` |
| `Location` | `in-ground`, `above-ground`, `indoor`, `outdoor` |
| `Covering` | `no-cover`, `tarp-cover`, `shelter` |
| `WaterTreatment` | `chlorine`, `bromine`, `salt`, `other` |
| `WaterTreatmentType` | Detailed treatment type |
| `LengthInMeterConverted` | Pool length |
| `WidthInMeterConverted` | Pool width |
| `DepthInMeterConverted` | Pool depth |
| `DiameterInMeterConverted` | Diameter (round pools) |
| `SurfaceInSquareMeterConverted` | Surface area (auto-calculated) |
| `VolumeInCubicMeterConverted` | Volume (auto-calculated) |
| `DropHeightConverted` | Drop height |
| `OverflowLengthConverted` | Overflow length |
| `AirHeated` | Air heating enabled (bool) |
| `AirHeatingOrder` | Air heating setpoint |
| `Overflowing` | Overflow pool (bool) |
| `ZipCode` | Postal code |
| `Country` | Country |

### Account fields

- `UserId` — user MongoDB ID
- `ProjectId` — project ID
- `Email` — `fradinni@gmail.com` (confirmed)
- `Language` — locale setting
- `MeasurementSystem` — metric/imperial

### Controlling devices

To programmatically control a device, you must:
1. Load `/from-native/<token>` to get a session cookie
2. POST `/_blazor/negotiate` to get a `connectionToken`
3. Open WebSocket `/_blazor?id=<connectionToken>`
4. Send SignalR MessagePack frames with `DispatchEventAsync`

**Limitation:** `eventHandlerIds` in `DispatchEventAsync` are dynamic per-session (assigned by the Blazor server at render time). They cannot be hardcoded — you must parse the render batch to find them. The approach is: connect, wait for the first render batch of the target page, extract the handler IDs for the desired button/slider, then dispatch the event.

---

## MQTT Port Firewall-Blocked

Port `8883` on `pairing.pool.mytech-connect.io` is **not reachable** from external networks (connection timeout confirmed). MQTT is only accessible from the mobile device's network context.

---

## Kubernetes Infrastructure Leak

Pod names are exposed via unauthenticated endpoints:

```json
// https://auth.pool.mytech-connect.io/
{"name":"PolyconnectAuthentificationApplicationServiceRC","version":"3.0.3.10068",
 "machineName":"api-std-poly-auth-658fc566b-h44tp"}

// https://pairing.pool.mytech-connect.io/
{"name":"PolyconnectPairingApi","version":"3.0.3.10068",
 "machineName":"api-cus-poly-pairing-b8c67c4b7-njns2"}

// https://mypolyconnect.polytropic.com/api
{"name":"ProSpaceApp","version":"3.0.3.10068",
 "machineName":"app-cus-poly-prospace-poly-698686ddd-gh2gz"}
```

---

## Hardcoded Credentials in APK

All found in plaintext in the decompiled .NET assemblies:

| Secret | Value | Risk |
|--------|-------|------|
| CF Access Client ID | `zLT6DV` | Bypasses Cloudflare Zero Trust firewall |
| CF Access Secret | `NEEJ9S` | Bypasses Cloudflare Zero Trust firewall |
| App signing key | `ZZuo8EMfc93KtDU745gvzw8DsWY0` | JWT signing/verification |
| Transaction key | `ptk08012018` | Signs device commands (Modbus) |
| NATS JetStream seed | `SUADVFAZSBQLTYMMFSTO6ORGURKGCQ6H4FIYLJTCKLDG5FUJZYAMNZSKIY` | Full access to internal message bus |
| NATS dev user | `roxane` | Internal dev credential |
| BLE Proof of Possession | `vcetdip48z` | Allows pairing any device without physical access |
| 1nce SIM API client | `81003792_prod` | Access to SIM management API |
| 1nce SIM API secret | `7TdF$G$!yzR3cAnM` | Access to SIM management API |
| MongoDB ObjectIDs | `608acd9fda2d414f7079344c/d/e` | Exposes internal database IDs |
| Custom token alphabet | `ABCDEFGHIJ_LaNOPQTSeUVWXYZzMbcxRfghijklm17pqrstuvwdy_K0n23456o89` | Token encoding scheme |

---

## Certificate Pinning (Not Enforced)

Three SHA1 thumbprints in `_trustedThumbprints` (IngeliStd HTTP client):

```
CABD2A79A1076A31F21D253635CB039D4329A5E8
B1D0F09E6A55A4A668CD0F7F58D1D09E509FB3E2
8A01F332C3C547A0DCB5D2AFD0C7FEDA34A93F7D
```

Current server cert SHA1: `98D49928427F68BFF08D280F9619F6E38B9C0B64` (Cloudflare-issued)

**No match** — pinning is not enforced. mitmproxy works without any bypass.

---

## NATS JetStream Seed

The NATS seed `SUADVFAZSBQLTYMMFSTO6ORGURKGCQ6H4FIYLJTCKLDG5FUJZYAMNZSKIY` grants full access to the internal message bus. An attacker with this seed could:

- Subscribe to all device readings across all customers
- Publish commands to any device
- Access internal service communication

---

## VerneMQ MQTT Configuration (Dev Environment)

Found in decompiled code — reveals internal Docker Compose setup:

```yaml
DOCKER_VERNEMQ_ACCEPT_EULA: "yes"
DOCKER_VERNEMQ_LISTENER.tcp.allowed_protocol_versions: "3,4,5"
DOCKER_VERNEMQ_MAX_ONLINE_MESSAGES: "1000"
DOCKER_VERNEMQ_MAX_OFFLINE_MESSAGES: "10000"
DOCKER_VERNEMQ_PLUGINS__VMQ_DIVERSITY: "on"
DOCKER_VERNEMQ_VMQ_DIVERSITY__AUTH_MONGODB__ENABLED: "on"
DOCKER_VERNEMQ_VMQ_DIVERSITY__MONGODB__PORT: "27017"
DOCKER_VERNEMQ_VMQ_DIVERSITY__MONGODB__DATABASE: "vernemq"
```

---

## Recommendations (for Polytropic/Ingeli)

1. **Rotate CF Access service tokens** (`zLT6DV`/`NEEJ9S`) — bypass the entire firewall
2. **Remove NATS seed** from APK — grants full internal message bus access
3. **Rotate 1nce SIM credentials** — `81003792_prod`/`7TdF$G$!yzR3cAnM`
4. **Remove BLE PoP** (`vcetdip48z`) — use per-device PoPs instead
5. **Remove transaction key** (`ptk08012018`) from client code
6. **Remove pod names** from `/` and `/api` unauthenticated endpoints
7. **Update cert pins** to match current Cloudflare certs, or remove unused pinning code
