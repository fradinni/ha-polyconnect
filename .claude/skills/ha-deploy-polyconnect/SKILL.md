---
name: ha-deploy-polyconnect
description: Deploy or update the Polyconnect integration and add-on to a running Home Assistant instance using the home-assistant MCP. Use this skill whenever the user wants to deploy, install, update, push, or set up the Polyconnect/Ingeli pool heat pump integration in Home Assistant. Triggers on: "deploy polyconnect", "install to HA", "update the add-on", "push to Home Assistant", "set up polyconnect on HA", "install polyconnect", "is the latest version deployed", or any variant asking to get the code running on a live HA instance. This skill never manually copies files — it works exclusively through HACS and the Supervisor add-on store.
---

# Deploy Polyconnect to Home Assistant

## Overview

This skill deploys two components from `https://github.com/fradinni/ha-polyconnect`:

| Component | Type | Identifier |
|---|---|---|
| Polyconnect Bridge | Supervisor add-on | slug `polyconnect_bridge` (prefix varies) |
| Polyconnect | HACS custom integration | domain `polyconnect` |

The workflow has two branches depending on whether it's a **first install** or an **update**.

---

## Step 1 — Detect current state

Run these checks in parallel to know which branch to take:

```
ha_get_addon()                                          → find polyconnect_bridge in installed add-ons
ha_get_addon(source="available", query="polyconnect")   → find it in the store (tells you if repo is registered)
ha_get_hacs_info(action="search", query="polyconnect")  → find the HACS repo entry
ha_get_integration(query="polyconnect")                 → find the config entry (if integration is set up)
```

From these four results, decide:

- **Add-on repo registered?** — any result from `source="available"` with polyconnect
- **Add-on installed?** — any result from `ha_get_addon()` with slug containing `polyconnect_bridge`
- **HACS repo known?** — any result from the HACS search (installed or not)
- **Integration configured?** — non-empty result from `ha_get_integration`

---

## Step 2 — Version check and bump (if needed)

Read the local versions from the workspace:

```
custom_components/polyconnect/manifest.json   → "version" field  (integration)
polyconnect_bridge/config.yaml                → "version:" field  (add-on)
```

Compare each against what HA has installed (from Step 1 results):

| Source | Local version | Installed version | Action |
|---|---|---|---|
| Integration | `manifest.json` | HACS `installed_version` | bump if equal |
| Add-on | `config.yaml` | `ha_get_addon()` version | bump if equal |

**If either local version == installed version:**

1. Suggest the next patch version (e.g. `1.0.1` → `1.0.2`, `1.1.8` → `1.1.9`) for each affected component.
2. Ask the user to confirm the proposed version(s) before making any change.
3. On confirmation, update the file(s):
   - `custom_components/polyconnect/manifest.json` — edit the `"version"` value
   - `polyconnect_bridge/config.yaml` — edit the `version:` value
4. Commit and push to GitHub:

```bash
git add custom_components/polyconnect/manifest.json polyconnect_bridge/config.yaml
git commit -m "chore: bump version to <new_version>"
git push
```

> Both components share the same GitHub repo, so one commit covers both if both were bumped.

Only after the push is confirmed should you proceed to install/update steps — HA must pull the new version from the repo.

---

## Step 3 — Register repositories (first install only)

### 3a. Add-on store repository

If the add-on repo is NOT registered:

```
ha_manage_addon(
    action="add_repository",
    repository="https://github.com/fradinni/ha-polyconnect"
)
```

Then re-run `ha_get_addon(source="available", query="polyconnect")` to get the actual slug (it will have a hash prefix like `abc123_polyconnect_bridge`). You need this slug for all subsequent add-on calls.

### 3b. HACS integration repository

If the HACS repo is NOT known:

```
ha_manage_hacs(
    action="add_repository",
    repository="fradinni/ha-polyconnect",
    category="integration"
)
```

---

## Step 4 — Add-on: install, configure, start

**Process the add-on completely before touching the integration.**

### 4a. Install or update

**First install:**
```
ha_manage_addon(action="install", slug="<resolved_slug>")
```

**Update (already installed):**
```
ha_manage_addon(action="update", slug="<resolved_slug>")
```

### 4b. Configure the add-on

Always set options right after install/update (before starting/restarting):

```
ha_manage_addon(
    slug="<resolved_slug>",
    options={
        "token": "<jwt_token>",
        "heat_pump_id": "<heat_pump_id>",
        "log_level": "info"
    }
)
```

**Token resolution order** — try each in order, stop on first hit:

1. Check `scripts/captured_token.txt` in the workspace root (gitignored, created by `scripts/get-jwt.py`). Read its contents and use as the token value.
2. If the file doesn't exist, ask the user to run `scripts/get-jwt.py` to capture a fresh token, or to paste it manually.

**Heat pump ID:** default is `64140b25194618718c5083bd`. Offer this as the default and ask the user to confirm or provide a different one.

### 4c. Start or restart the add-on

**First install:**
```
ha_manage_addon(action="start", slug="<resolved_slug>")
```

**Update:**
```
ha_manage_addon(action="restart", slug="<resolved_slug>")
```

Confirm the add-on state is `started` before continuing.

---

## Step 5 — Integration: install or update

Only proceed here once the add-on is fully up (Step 4 complete).

Both first install and update use the same call:
```
ha_manage_hacs(
    action="download",
    repository_id="fradinni/ha-polyconnect"
)
```

After HACS downloads the integration, a **full HA restart is required** for the integration code to load:
```
ha_restart(confirm=True)
```

Wait for HA to come back up before continuing (poll `ha_get_overview()` or `ha_get_system_health()`).

> Skip the restart if the HACS download was a no-op (same version already installed). Use `ha_get_hacs_info(action="search", installed_only=True, query="polyconnect")` to compare installed vs latest version before deciding.

### After restart — reload the Polyconnect config entry

Once HA is back up, **always** reload the Polyconnect config entry so the integration picks up the freshly installed code:

```
# 1. Get the entry_id
ha_get_integration(query="polyconnect")   → grab entry_id from the result

# 2. Reload the config entry
ha_call_service(
    domain="homeassistant",
    service="reload_config_entry",
    data={"entry_id": "<entry_id>"}
)
```

Confirm the entry state is `loaded` before moving on.

---

## Step 6 — Set up the HA integration config entry

Check if a config entry exists:
```
ha_get_integration(query="polyconnect")
```

**If no config entry exists**, the user must complete the setup through the HA UI. Inform them:
> "The Polyconnect integration is now installed. Go to **Settings → Devices & Services → Add Integration**, search for **Polyconnect**, and follow the setup wizard. You'll need the bridge URL (the add-on's ingress address or `http://homeassistant.local:8765`) and your credentials."

**If a config entry exists**, check it's loaded correctly (state should be `loaded`). If it's in `setup_error` or `setup_retry`, check logs:
```
ha_get_logs(source="error_log", search="polyconnect")
ha_get_logs(source="supervisor", slug="<resolved_slug>", limit=50)
```

---

## Step 7 — Verify deployment

Run all checks in parallel:

```
ha_search(query="polyconnect", domain_filter="sensor")
ha_search(query="polyconnect", domain_filter="climate")
ha_search(query="polyconnect", domain_filter="binary_sensor")
ha_search(query="polyconnect", domain_filter="switch")
ha_get_addon(slug="<resolved_slug>")
```

**Expected results:**
- Add-on state: `started`
- Integration config entry: state `loaded`
- Entities visible (sensors for water temperature, heat pump status, etc.)

If entities are missing or the integration is in error state, fetch logs and surface the error to the user.

---

## Outcome summary

Report back to the user with:

1. Add-on version installed and its current state (`started` / `stopped`)
2. HACS integration version installed
3. Config entry state (`loaded` / `setup_error` / not yet configured)
4. Entity count found (sensors, climate, binary sensors, switches)
5. Any errors found in logs

---

## Key identifiers

| Item | Value |
|---|---|
| GitHub repo | `https://github.com/fradinni/ha-polyconnect` |
| HACS repo ID | `fradinni/ha-polyconnect` |
| Add-on name | `Polyconnect Bridge` |
| Add-on slug (base) | `polyconnect_bridge` (actual slug has hash prefix — discover at runtime) |
| Integration domain | `polyconnect` |
| HACS category | `integration` |
| Add-on ingress port | `8765` |
