# Polyconnect for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange?logo=homeassistantcommunitystore&logoColor=white)](https://github.com/fradinni/ha-polyconnect)
[![HA 2024.1+](https://img.shields.io/badge/Home%20Assistant-2024.1+-blue?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)

Control your **Polyconnect / Ingeli pool heat pump** from Home Assistant.

Works with Polytropic, Warmeo, MytechConnect, Pentair, and other Ingeli-platform brands.

![Climate card preview](https://img.shields.io/badge/climate-heat%20%7C%20cool%20%7C%20auto-brightgreen) ![Presets](https://img.shields.io/badge/presets-Eco%20%7C%20Smart%20%7C%20Boost-blue) ![Temperature](https://img.shields.io/badge/range-8°C–32°C-yellow)

---

## What You Get

| Entity | What it does |
|--------|-------------|
| **Climate** | Temperature control (8–32°C) + Heat/Cool/Auto modes + Eco/Smart/Boost presets |
| **Power Switch** | Turn the heat pump on or off |
| **Water Temp** | Current pool water temperature |
| **Outside Temp** | Ambient air temperature |
| **Power Consumption** | Current draw (W) |
| **COP** | Coefficient of performance |
| **Compressor** | Running state |
| **Filtration Pump** | Running state |
| **Alarm** | Active alarm + message text |

---

## Requirements

- Home Assistant OS or Supervised (needed for the bridge add-on)
- A working Polyconnect heat pump paired to the mobile app
- Phone on the same WiFi as Home Assistant (one-time setup)

---

## Installation

### 1. Add the repository

[![Add repository to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fradinni&repository=ha-polyconnect&category=integration)

Or manually: **HACS → ⋮ → Custom Repositories** → add `https://github.com/fradinni/ha-polyconnect` (Integration)

### 2. Install the Bridge add-on

**Settings → Add-ons → Add-on Store → ⋮ → Repositories** → add `https://github.com/fradinni/ha-polyconnect`

Find **Polyconnect Bridge**, install it, and start it.

### 3. Capture credentials

Open the bridge panel from the HA sidebar:

1. Click **Start Capture**
2. On your phone, open the URL shown (e.g. `http://your-ha-ip:8080`)
3. Follow the wizard to configure the proxy
4. Open the Polyconnect app — credentials are captured automatically
5. Done when status shows **Ready**

### 4. Add the integration

**Settings → Devices & Services → Add Integration → Polyconnect**

The bridge URL is auto-detected. All entities appear immediately.

---

## Token Refresh

Tokens expire periodically. When this happens, a **Repair issue** appears in HA.

Fix: open the bridge panel → **Reset Credentials** → **Start Capture** → redo the phone step.

The integration recovers automatically once new credentials are captured.

---

## Options

| Setting | Default | Description |
|---------|---------|-------------|
| Scan interval | 60s | Polling frequency (10s – 3600s) |

Configure in **Settings → Devices & Services → Polyconnect → Configure**.

---

## How It Works

Polyconnect has no API. The mobile app renders everything through a Blazor WebSocket.

This integration runs a **headless browser** (Playwright) inside a local add-on that:
- **Reads status** by scraping the Blazor DOM every poll cycle
- **Sends commands** by navigating to edit pages and clicking buttons

Commands take 4–8 seconds due to Blazor page transitions. The UI updates optimistically.

---

## Limitations

- **Token expiry** — requires periodic recapture via the bridge panel
- **Command latency** — 4–8s per action (browser navigation)
- **DOM fragility** — app UI changes may break scraping until selectors are updated
- **Polling only** — no real-time push (MQTT is firewalled)
- **HA OS only** — the bridge add-on needs Supervisor

---

## Documentation

Detailed technical docs in [`docs/`](docs/):

- [Architecture & Integration Details](docs/README.md)
- [API Reference (Bridge + Cloud Platform)](docs/api-reference.md)
- [Security Findings](docs/security-findings.md)

---

## License

MIT
