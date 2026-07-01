# Polyconnect Bridge

Local HTTP bridge that lets Home Assistant control **Polyconnect / Ingeli pool heat pumps**.

The Polyconnect cloud has no public API — everything goes through a Blazor Server SignalR
WebSocket rendered inside the mobile app's WebView. This add-on runs a headless Chromium
(via Playwright) that logs into your account, scrapes the UI for status, and simulates
clicks to send commands. The companion [Polyconnect integration](https://github.com/fradinni/ha-polyconnect)
talks to this bridge over local HTTP.

## Setup

1. **Install and start the add-on.**
2. **Enter your credentials** in the add-on Configuration tab:
   - `email` — the address you use to sign in to the Polyconnect / Ingeli mobile app
   - `password` — the matching password
3. **Save and restart the add-on.**
4. Open the add-on **Web UI** (sidebar icon). The bridge:
   - Registers a virtual terminal with the Polyconnect auth service (one-time).
   - Logs in, captures a session token.
   - Auto-discovers your installation and all heat pumps on it.
5. The panel shows session status, installation name, and the list of discovered pumps.
6. Add the **Polyconnect** integration in Settings → Devices & Services. It finds
   the bridge automatically.

## Configuration

| Option | Description | Required |
|--------|-------------|----------|
| `email` | Polyconnect / Ingeli account email | Yes |
| `password` | Polyconnect / Ingeli account password | Yes |
| `log_level` | `trace` / `debug` / `info` / `warning` / `error` | Default: `info` |

Installation and heat-pump IDs are **auto-discovered** — they're no longer part of
the add-on config since v2.2.0. Discovered IDs are persisted in `/data/ids.json`
across restarts.

## Web UI

The ingress panel (sidebar icon) shows live status:

- **Installation** — name + ID (auto-discovered)
- **Heat pumps** — one row per discovered pump with its name and ID
- **Session** — token status and age
- **Terminal** — registered device fingerprint
- **Last Error** — most recent auth failure, if any

Three actions are available:

- **Refresh Browser** — force a new login round-trip (fast).
- **Restart Server** — restart the add-on process. Use if the bridge seems stuck.
- **Reset Auth & IDs** — wipe all persisted state (terminal, session, discovered IDs)
  and restart. Use when switching accounts or after a persistent auth failure.

## Data Persistence

All state lives under `/data/` (persistent across add-on updates):

| File | Contents |
|------|----------|
| `terminal.json` | Virtual terminal ID + transaction key (long-lived, one-time registration) |
| `session.json` | Session token + issue timestamp (~12h TTL, auto-refreshed) |
| `ids.json` | Installation ID + name + list of discovered heat pumps |

## Troubleshooting

**"Not acquired" session after saving credentials**
- Check the add-on log for a `Login failed (state=...)` message.
- State 1 = bad credentials. Double-check email/password in the app.
- State 2/3 = account disabled/inactive on Polyconnect's side.

**No heat pumps discovered**
- Confirm the account has at least one pump configured in the mobile app.
- Try **Reset Auth & IDs** to restart the discovery flow from scratch.

**Bridge stuck / status not updating**
- Try **Refresh Browser**. If that doesn't work, **Restart Server**.
- The bridge auto-reloads on stale DOM (10 identical reads or 30 min unchanged).

**Session expired repeatedly**
- Sessions are auto-refreshed on 401. If it keeps expiring, hit **Reset Auth & IDs**
  to force a fresh terminal registration.

## API

The bridge exposes a REST API on port 8765 (via ingress). See
[`docs/api-reference.md`](https://github.com/fradinni/ha-polyconnect/blob/main/docs/api-reference.md)
in the repository for the full endpoint list. Typical usage goes through the HA
integration, not direct API calls.
