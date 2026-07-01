---
name: ha-deploy-polyconnect
description: Deploy or update the Polyconnect bridge add-on and/or custom_component integration to the running Home Assistant instance for testing. Use this skill whenever the user wants to deploy, install, update, push, or test the Polyconnect/Ingeli pool heat pump components in HA. Triggers on "deploy polyconnect", "install to HA", "push to HA", "update the bridge", "test on HA", "redeploy", or any variant. The skill is interactive: it asks which components to deploy, picks GitHub-pull or SSH direct copy based on git state, and confirms destructive steps.
---

# Deploy Polyconnect to Home Assistant

Interactive, two-path deployer for the bridge add-on (`polyconnect_bridge/`) and the integration (`custom_components/polyconnect/`).

## Components & identifiers

| Component | Local source | GitHub-installed slug | SSH target path |
|---|---|---|---|
| Bridge add-on | `polyconnect_bridge/` | `ecbbef75_polyconnect_bridge` | `/addons/polyconnect_bridge/` → `local_polyconnect_bridge` |
| Integration | `custom_components/polyconnect/` | HACS `fradinni/ha-polyconnect`, domain `polyconnect` | `/config/custom_components/polyconnect/` |

| Constant | Value |
|---|---|
| HA host | `nicolas@192.168.1.11` |
| SSH key | `~/.ssh/ed25519_fradinni_gmail_com` |
| GitHub repo | `https://github.com/fradinni/ha-polyconnect` |

## Step 1 — Ask what to deploy

Always ask first (use the `question` tool):
- Bridge add-on only
- Integration only
- Both

Default suggestion: **Both**.

## Step 2 — Detect state (in parallel)

```bash
git rev-parse --abbrev-ref HEAD            # current branch
git status --porcelain                      # dirty?
git log --oneline @{u}..HEAD 2>/dev/null    # unpushed commits?
```

```
ha_get_addon()                              # find ecbbef75_polyconnect_bridge
ha_get_hacs_info(action="search", installed_only=True, query="polyconnect")
ha_get_integration(query="polyconnect")     # config entry + entry_id
```

Read local versions:
```bash
grep '"version"' custom_components/polyconnect/manifest.json
grep '^version:' polyconnect_bridge/config.yaml
```

## Step 3 — Auto-pick deployment path

| Git state | Default path | Why |
|---|---|---|
| clean + on `main` + nothing unpushed | **A: GitHub-pull** | Tests the real install flow |
| clean + on feature branch | **B: SSH direct copy** | HACS pulls default branch only |
| dirty working tree | **B: SSH direct copy** | Can't push WIP |
| clean + ahead of origin | Ask: "push first then pull, or direct copy?" | Either works |

After auto-picking, **show the user the picked path and offer to switch**. Use the `question` tool with two options ("Use {picked}" / "Use the other").

---

## Path A — GitHub-pull

### A.1 Version bump (only if needed)

For each selected component, compare local version (file) to installed version (HA):

- Integration: `manifest.json` "version" vs HACS `installed_version`
- Add-on: `config.yaml` `version:` vs `ha_get_addon` `version`

If local == installed: suggest next patch (e.g. `2.1.0` → `2.1.1`), ask to confirm, then edit the file(s). If local > installed: skip bump.

### A.2 Commit + push

```bash
git add -A   # stage what changed (bump files + anything else relevant)
git status   # show user what will be committed
git commit -m "chore: deploy bump"   # or descriptive
git push
```

If on a feature branch and the user wants GitHub-pull: warn that HACS pulls the default branch (`main`). Either merge first or fall back to path B.

### A.3 Pull on HA

```
# Bridge
ha_manage_addon(action="update", slug="ecbbef75_polyconnect_bridge")
ha_manage_addon(action="restart", slug="ecbbef75_polyconnect_bridge")

# Integration
ha_manage_hacs(action="download", repository_id="fradinni/ha-polyconnect")
ha_restart(confirm=True)   # required to load new integration code
# wait for HA to come back
ha_call_service(domain="homeassistant", service="reload_config_entry",
                data={"entry_id": "<entry_id>"})
```

Skip the HA restart if HACS reports the same version (no-op).

---

## Path B — SSH direct copy

### B.0 Ensure write permissions (one-time setup, idempotent)

The `nicolas` user (uid 1000) inside the SSH addon **cannot write** to `/addons/` or `/homeassistant/` by default — both are root:root 755. Fix this via the SSH addon's `init_commands` (run as root on each addon start):

```
ha_manage_addon(
    slug="a0d7b954_ssh",
    options={"init_commands": [
        "mkdir -p /addons/polyconnect_bridge && chown -R nicolas:nicolas /addons/polyconnect_bridge",
        "chown -R nicolas:nicolas /homeassistant/custom_components/polyconnect"
    ]}
)
ha_manage_addon(action="restart", slug="a0d7b954_ssh")
```

Check whether these init_commands are already configured before re-applying. If they're already there, skip this step (the chown is idempotent anyway). Verify with:

```bash
ssh -i ~/.ssh/ed25519_fradinni_gmail_com nicolas@192.168.1.11 \
  "touch /addons/polyconnect_bridge/.test && rm /addons/polyconnect_bridge/.test && echo OK"
```

### B.1 Handle existing GitHub-installed copies (ASK)

Detect what's installed (Step 2 results) and ask the user. Default action = **non-destructive**:

| Component | Default non-destructive action | Destructive alternative |
|---|---|---|
| Bridge add-on | Stop `ecbbef75_polyconnect_bridge` (keep installed) — frees port 8765 | Uninstall it |
| Integration | rsync over `/config/custom_components/polyconnect/` (HACS still tracks the old SHA but files are local) | Remove HACS entry first |

Use the `question` tool only if the GitHub-installed copies exist. Skip the question if they don't.

If user picks non-destructive (default):
```
# Bridge only: stop the GitHub addon, port 8765 conflict avoidance
ha_manage_addon(action="stop", slug="ecbbef75_polyconnect_bridge")
```

### B.2 rsync the bridge (if selected)

The directory `/addons/polyconnect_bridge/` is created with correct ownership by Step B.0's init_commands, so the rsync just works:

```bash
rsync -av --delete \
  -e "ssh -i ~/.ssh/ed25519_fradinni_gmail_com" \
  --exclude '__pycache__' --exclude '*.pyc' \
  polyconnect_bridge/ \
  nicolas@192.168.1.11:/addons/polyconnect_bridge/
```

Then reload the Supervisor add-on store so it discovers `local_polyconnect_bridge`. The `hassio.addon_reload` service requires an `entity_id` we don't have, so use the CLI via SSH:

```bash
ssh -i ~/.ssh/ed25519_fradinni_gmail_com nicolas@192.168.1.11 \
  "TOKEN=\$(grep SUPERVISOR_TOKEN /etc/profile.d/*.sh | head -1 | sed -E 's/.*\"(.*)\".*/\\1/'); \
   ha store reload --api-token \"\$TOKEN\""
```

Verify with `ha_get_addon(slug="local_polyconnect_bridge")` — should return `installed: false, available: true, version_latest: <local version>`.

First time only: `ha_manage_addon(action="install", slug="local_polyconnect_bridge")`. This builds the docker image (Playwright + Chromium = slow, can take 3-5 minutes; the MCP call may time out before the build finishes — that's OK, just poll `ha_get_addon` afterwards until `installed: true`).

**Subsequent times — always REBUILD, never just restart**: `ha_manage_addon(action="rebuild", slug="local_polyconnect_bridge")`.

Why: the bridge's Dockerfile uses `COPY . /app/` at build time. `restart` reuses the existing image → old code keeps running. `rebuild` re-bakes the image with the rsynced files. `rebuild` does its own start at the end, no separate `start` call needed.

Configure options (email, password, heat_pump_id, installation_id) before starting on a fresh install — see the bridge's `config.yaml` for the schema. Credentials live in `.env` at the repo root:

```
POLYCONNECT_EMAIL="..."
POLYCONNECT_PASSWORD="..."
POLYCONNECT_INSTALLATION_ID="..."
POLYCONNECT_HEAT_PUMP_ID="..."
```

Map to addon options:
```
ha_manage_addon(
    slug="local_polyconnect_bridge",
    options={
        "email": "<from .env>",
        "password": "<from .env>",
        "heat_pump_id": "<from .env>",
        "installation_id": "<from .env>",
        "log_level": "info"
    }
)
ha_manage_addon(action="start", slug="local_polyconnect_bridge")
```

### B.3 rsync the integration (if selected)

The HA host's `/config` is a symlink to `/homeassistant`. Use `/homeassistant/custom_components/polyconnect/` as the rsync target (matches what B.0's init_commands chown):

```bash
rsync -av --delete \
  -e "ssh -i ~/.ssh/ed25519_fradinni_gmail_com" \
  --exclude '__pycache__' --exclude '*.pyc' \
  custom_components/polyconnect/ \
  nicolas@192.168.1.11:/homeassistant/custom_components/polyconnect/
```

Integration Python modules are cached in `sys.modules`. After rsync, **`reload_config_entry` is NOT enough** — it reloads entry config, not the Python code. A full HA restart is required:

```
ha_restart(confirm=True)
# wait for HA to come back, then optionally reload the entry to be safe:
ha_call_service(domain="homeassistant", service="reload_config_entry",
                data={"entry_id": "<entry_id>"})
```

Skip the restart only if the rsync output shows no transfers (no-op).

---

## Step 4 — Verify

Run in parallel:
```
ha_get_addon(slug="<bridge_slug>")                    # state == "started"
ha_get_integration(query="polyconnect")               # state == "loaded"
ha_search(query="heat pump")                          # see entity_id naming notes below
ha_get_logs(source="supervisor", slug="<bridge_slug>", limit=30)
```

**Entity_id naming changed between versions** — don't trust the old `sensor.polyconnect_heat_pump_*` names. v2 (multi-pump, one device per pump) names entities `sensor.heat_pump_*` (device-name based) instead of `sensor.polyconnect_*`. After deploying v2 over a v1 install, the v1 entity_ids will be stale and stuck on `unavailable`; the new v2 entities live under the new names. Search broadly (`query="heat pump"` without domain filter) to find the actually-active set.

If addon or integration is in error: fetch error_log and surface to user.

Quick bridge sanity check via SSH:
```bash
ssh -i ~/.ssh/ed25519_fradinni_gmail_com nicolas@192.168.1.11 \
  "curl -s http://172.30.33.2:8765/health; echo; \
   curl -s http://172.30.33.2:8765/pumps"
```

(The bridge container IP is consistent across rebuilds — 172.30.33.2 in this deployment. Use the stored `bridge_url` from the config entry: `python3 -c 'import json; print(json.load(open(\"/homeassistant/.storage/core.config_entries\"))[\"data\"][\"entries\"])'` via SSH for the canonical value.)

## Step 5 — Report

Single short message:
- Path used (A or B)
- Components deployed and their versions
- Add-on state, integration state, entity count
- Anything notable from logs

---

## Notes & gotchas

- **Port 8765 conflict**: only one of `ecbbef75_polyconnect_bridge` and `local_polyconnect_bridge` can run at a time. Path B always stops the GitHub one first.
- **Bridge needs `rebuild`, not `restart`**: the addon Dockerfile uses `COPY . /app/` at build time. `restart` just restarts the container from the cached image with the OLD code. Always `rebuild` after rsync.
- **Integration needs full HA restart**: `reload_config_entry` does NOT reimport Python modules (sys.modules is cached). Without restart, the new files are on disk but the old code keeps running.
- **HACS doesn't notice direct overwrites** of `/config/custom_components/polyconnect/`. To revert: `ha_manage_hacs(action="download", repository_id="fradinni/ha-polyconnect")` redownloads the tracked version.
- **SSH addon runs as `nicolas` (uid 1000), not root** — Step B.0 fixes this once via `init_commands` on the SSH addon. After that, deploys are fast.
- **Bridge install timeout**: `ha_manage_addon(action="install", slug="local_polyconnect_bridge")` builds Chromium and can take 3-5 minutes. The MCP call may report timeout while the build continues — poll `ha_get_addon` until `installed: true` rather than failing the deploy.
- **Branch `v2-native-login` is NOT `main`**: path A from this branch needs either `git push origin v2-native-login:main` (force) or a merge first. Surface this to the user before doing it.
- **SSH key**: the addon's `authorized_keys` must contain `~/.ssh/ed25519_fradinni_gmail_com.pub`. If `ssh nicolas@192.168.1.11 echo OK` fails, fix the addon options before proceeding.
