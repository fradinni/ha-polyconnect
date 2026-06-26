# Polyconnect — Security Findings

> Reverse-engineered from `Polyconnect_5.3_APKPure.xapk` (.NET MAUI, June 2026).
> Live API testing confirmed June 2026.

---

## Summary

The Polyconnect mobile app (by Ingeli) contains multiple hardcoded secrets that bypass security controls. The platform's MQTT and REST endpoints are mostly firewalled, but the embedded Cloudflare credentials grant full API access to anyone who decompiles the APK.

---

## Cloudflare Zero Trust Bypass

The auth and pairing servers are behind **Cloudflare Zero Trust** with JA3/JA4 TLS fingerprint-based device policy. Desktop clients (curl, Python, browser) are blocked:

```
HTTP 303 → https://blocked.teams.cloudflare.com
  ?source_ip=<your-ip>&url=auth.pool.mytech-connect.io
```

**Bypass:** Two service tokens found hardcoded in the APK:

```
CF-Access-Client-Id: zLT6DV
CF-Access-Client-Secret: NEEJ9S
```

These bypass the entire Cloudflare firewall for any caller.

---

## Authentication Architecture

The app uses email + password auth via REST:

```http
POST https://auth.pool.mytech-connect.io/Irc/Application/Login
```

**Response format** (observed via mitmproxy):
```json
{ "tid": "<transaction-id>", "tpv": 1, "psp": "<session-token>" }
```

The `psp` field is a custom-encoded opaque token (not a standard JWT). After login, the app opens a Blazor WebView at:
```
https://polytropic.user-app.pool.mytech-connect.io/from-native/<token>
```

All data flows through this Blazor SignalR WebSocket — not REST.

---

## Login Endpoint Returns 500 (External Callers)

`POST /Irc/Application/Login` returns `500` with empty body for all external callers. The HTTP API is a gateway that forwards requests to an internal **NATS JetStream** microservice. NATS is only reachable within the Kubernetes cluster.

`GET /Irc/Application/Login` returns `{"s":5}` (status check, no NATS call) — confirming the endpoint exists but write operations require cluster access.

---

## User Data Endpoints — Cluster-Internal Only

These return `404` from external networks regardless of auth token:

- `GET /Irc/Application/GetUser`
- `GET /Irc/Application/GetDevices`
- `GET /Irc/Application/GetInstallations`

Consistent with the Blazor architecture — user data is served via SignalR, not public REST.

---

## MQTT Port Firewall-Blocked

Port `8883` on `pairing.pool.mytech-connect.io` is **not reachable** from external networks (connection timeout confirmed). MQTT is only accessible from the mobile device's network context (possibly bound to specific IP ranges or requires the CF bypass at the TCP level).

---

## Certificate Pinning (Not Enforced)

Three SHA1 thumbprints found in `_trustedThumbprints` (IngeliStd.dll):

```
CABD2A79A1076A31F21D253635CB039D4329A5E8
B1D0F09E6A55A4A668CD0F7F58D1D09E509FB3E2
8A01F332C3C547A0DCB5D2AFD0C7FEDA34A93F7D
```

Current server cert SHA1: `98D49928427F68BFF08D280F9619F6E38B9C0B64` (Cloudflare-issued).

**No match** — pinning is not enforced. mitmproxy works without any bypass needed.

---

## Kubernetes Infrastructure Leak

Pod names exposed via unauthenticated endpoints:

```json
// GET https://auth.pool.mytech-connect.io/
{"name":"PolyconnectAuthentificationApplicationServiceRC","version":"3.0.3.10068",
 "machineName":"api-std-poly-auth-658fc566b-h44tp"}

// GET https://pairing.pool.mytech-connect.io/
{"name":"PolyconnectPairingApi","version":"3.0.3.10068",
 "machineName":"api-cus-poly-pairing-b8c67c4b7-njns2"}

// GET https://mypolyconnect.polytropic.com/api
{"name":"ProSpaceApp","version":"3.0.3.10068",
 "machineName":"app-cus-poly-prospace-poly-698686ddd-gh2gz"}
```

Reveals internal naming conventions, Kubernetes deployment structure, and exact version numbers.

---

## Hardcoded Credentials in APK

All found in plaintext in the decompiled .NET assemblies:

| Secret | Value | Risk |
|--------|-------|------|
| CF Access Client ID | `zLT6DV` | Bypasses Cloudflare Zero Trust firewall |
| CF Access Secret | `NEEJ9S` | Bypasses Cloudflare Zero Trust firewall |
| App signing key | `ZZuo8EMfc93KtDU745gvzw8DsWY0` | Token signing/verification |
| Transaction key | `ptk08012018` | Signs device commands (Modbus) |
| NATS JetStream seed | `SUADVFAZSBQLTYMMFSTO6ORGURKGCQ6H4FIYLJTCKLDG5FUJZYAMNZSKIY` | Full access to internal message bus |
| NATS dev user | `roxane` | Internal dev credential |
| BLE Proof of Possession | `vcetdip48z` | Pair any device without physical access |
| 1nce SIM API client | `81003792_prod` | SIM management API access |
| 1nce SIM API secret | `7TdF$G$!yzR3cAnM` | SIM management API access |
| Custom token alphabet | `ABCDEFGHIJ_LaNOPQTSeUVWXYZzMbcxRfghijklm17pqrstuvwdy_K0n23456o89` | Token encoding scheme |

---

## NATS JetStream Exposure

The NATS seed grants **full access to the internal message bus**. An attacker with this seed could:

- Subscribe to all device readings across all customers
- Publish commands to any device
- Access internal service communication

The seed is an Ed25519 signing key in NATS nkey format — it can be used to derive the public key and authenticate as a valid service.

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

## Blazor SignalR Details

### Frame Types (MessagePack binary)

| Direction | Method | Description |
|-----------|--------|-------------|
| C→S | `OnRenderCompleted` | ACK after receiving a render batch |
| C→S | `BeginInvokeDotNetFromJS` + `DispatchEventAsync` | User interaction |
| C→S | `OnLocationChanged` | Page navigation |
| S→C | `JS.RenderBatch` | Binary UI diff (Blazor render tree) |
| S→C | `JS.BeginInvokeJS` | Server calls JS function |

### JavaScript Functions Called by Server

| Function | Args | Description |
|----------|------|-------------|
| `initRoundSlider` | `[gaugeId, value, min, max, dotNetRef]` | Initialize temperature gauge |
| `updateRoundSlider` | `[gaugeId, value]` | Update displayed temperature |
| `enableRoundSlider` | `[gaugeId]` | Enable slider interaction |
| `disableRoundSlider` | `[gaugeId]` | Disable during save |
| `navigationManager.navigateTo` | `[url, options]` | Page navigation |

### Dynamic Event Handler IDs

`DispatchEventAsync` handler IDs are assigned per-session by the Blazor server at render time. They **cannot be hardcoded** — you must parse the binary RenderBatch to find them. This is the fundamental reason why the integration uses DOM scraping (Playwright) instead of raw WebSocket interaction.

---

## Alarm Codes

Two alarm series discovered from support/documentation pages:

- Series 1: `AL01`–`AL28`
- Series 2: `EA01`–`EA10`

Known descriptions (French):
- `AL07 / E13` — Component failure
- Modbus communication error
- Water leak under the machine
- Winterization / frost or smoke
- Pool not heating
- Communication loss
- Connection loss
- Icing

---

## Recommendations (for Polytropic/Ingeli)

1. **Rotate CF Access service tokens** — bypass the entire firewall
2. **Remove NATS seed from APK** — grants full internal message bus access
3. **Rotate 1nce SIM credentials** — access to production SIM management
4. **Use per-device BLE PoP** — `vcetdip48z` is shared across all devices
5. **Remove transaction key from client code** — enables command forgery
6. **Remove pod names from unauthenticated endpoints**
7. **Update or remove cert pins** — currently non-functional
