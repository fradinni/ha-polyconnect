<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0ea5e9,100:0284c7&height=200&section=header&text=HA-Polyconnect&fontSize=60&fontColor=ffffff&fontAlignY=38&desc=Pool%20Heat%20Pump%20%E2%80%94%20Home%20Assistant%20Integration&descAlignY=60&descSize=18&animation=fadeIn" width="100%" alt="Polyconnect banner"/>

<!-- Badges row 1 — install -->

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange?style=for-the-badge&logo=homeassistantcommunitystore&logoColor=white)](https://github.com/fradinni/ha-polyconnect) [![HA 2024.1+](https://img.shields.io/badge/Home%20Assistant-2024.1+-41bdf5?style=for-the-badge&logo=homeassistant&logoColor=white)](https://www.home-assistant.io/) [![License: MIT](https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge)](LICENSE)

<!-- Badges row 2 — status -->

[![GitHub last commit](https://img.shields.io/github/last-commit/fradinni/ha-polyconnect?style=flat-square&logo=git&logoColor=white&color=6366f1)](https://github.com/fradinni/ha-polyconnect/commits) [![GitHub issues](https://img.shields.io/github/issues/fradinni/ha-polyconnect?style=flat-square&logo=github&color=f97316)](https://github.com/fradinni/ha-polyconnect/issues) [![IoT class](https://img.shields.io/badge/IoT%20class-local%20polling-0ea5e9?style=flat-square&logo=wifi&logoColor=white)](https://developers.home-assistant.io/docs/integration_quality_scale_index/)


> Control your **Polytropic pool heat pump** from Home Assistant — for units managed via the **Polyconnect / Ingeli** app.

</div>

---

## What You Get

| Entity                   | Type          | What it does                                                          |
| ------------------------ | ------------- | --------------------------------------------------------------------- |
| **Climate**              | Control       | Temperature (8–32°C) · Heat/Cool/Auto modes · Eco/Smart/Boost presets |
| **Power**                | Switch        | Turn the heat pump on or off                                          |
| **Water Temperature**    | Sensor        | Current pool water temperature                                        |
| **Outside Temperature**  | Sensor        | Ambient air temperature                                               |
| **Setpoint Temperature** | Sensor        | Active target temperature                                             |
| **Operating Mode**       | Sensor        | Current HVAC mode (Heating/Cooling/Auto)                              |
| **Regulation Mode**      | Sensor        | Current preset (Eco/Smart/Boost)                                      |
| **Alarm Message**        | Sensor        | Alarm description text (null when no alarm)                           |
| **Fan**                  | Binary sensor | Fan running state                                                     |
| **Filtration Pump**      | Binary sensor | Filtration pump running state                                         |
| **Defrost**              | Binary sensor | Defrost cycle active state                                            |
| **Alarm**                | Binary sensor | Alarm active state                                                    |

---

## Requirements

- Home Assistant OS or Supervised (required for the bridge add-on)
- A Polytropic heat pump paired to the Polyconnect mobile app
- Phone on the same WiFi as Home Assistant (one-time setup only)

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

| Setting       | Default | Range    | Description                             |
| ------------- | ------- | -------- | --------------------------------------- |
| Scan interval | 1 min   | 1–60 min | How often to poll the bridge for status |

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

## License

MIT
