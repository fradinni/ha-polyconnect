---
name: ha-uninstall-polyconnect
description: Completely uninstall and clean up all traces of the Polyconnect integration and add-on from Home Assistant. Use this skill whenever the user wants to remove, uninstall, clean up, wipe, or get rid of Polyconnect from their Home Assistant instance. Triggers on: "uninstall polyconnect", "remove polyconnect from HA", "clean up polyconnect", "delete polyconnect", "wipe polyconnect", "remove the add-on", "clean HA of polyconnect", or any variant asking to remove the integration, add-on, entities, devices, or repositories related to Polyconnect. Covers the full teardown: entities (cascade) → config entry → add-on (stop + uninstall) → optionally repositories. Repository removal is opt-in only — always confirm separately before removing repos.
---

# Uninstall Polyconnect from Home Assistant

## Overview

This skill performs a complete, ordered teardown of both Polyconnect components:

| Component | Type | Identifier |
|---|---|---|
| Polyconnect Bridge | Supervisor add-on | slug contains `polyconnect_bridge` |
| Polyconnect | HACS custom integration | domain `polyconnect` |

**Key facts discovered from live runs:**
- Deleting the config entry automatically cascades to remove all 13 Polyconnect entities — no manual entity removal needed.
- `ha_remove_helpers_integrations` (with just `target=entry_id`) handles graceful unloading internally — no need to call `reload_config_entry` first.
- The HACS MCP API (`ha_manage_hacs`) has **no remove action** — HACS repo removal is always manual via the UI.
- Repository removal (add-on store + HACS) should default to **keep** — only remove if the user explicitly requests it.

---

## Step 1 — Detect what is installed

Run all checks in parallel:

```
ha_get_addon()                                           → find installed add-on (slug contains "polyconnect_bridge")
ha_get_addon(source="available", query="polyconnect")    → check if add-on store repo is registered; slug is in the `repositories[]` array
ha_get_hacs_info(action="search", query="polyconnect", installed_only=True)  → check if HACS repo is installed
ha_get_integration(query="polyconnect")                  → find config entry (entry_id, state)
ha_search(query="polyconnect", domain_filter="sensor")
ha_search(query="polyconnect", domain_filter="climate")
ha_search(query="polyconnect", domain_filter="binary_sensor")
ha_search(query="polyconnect", domain_filter="switch")
```

Record:
- **add_on_slug** — e.g. `ecbbef75_polyconnect_bridge`, or `null`
- **add_on_repo_slug** — the repo slug from `repositories[].slug` in the `source="available"` response (e.g. `ecbbef75`), or `null`
- **hacs_installed** — bool
- **config_entry_id** — string or `null`
- **entity_count** — total across all four domain searches

If nothing is installed at all, tell the user "Polyconnect is not installed in this Home Assistant instance" and exit.

Summarise findings to the user before proceeding.

---

## Step 2 — Confirm with the user (two-part)

### Part A — Core removal (default: yes)

Show the user exactly what will be deleted and ask for confirmation:

```
The following Polyconnect items will be removed:

• <N> entities (<breakdown by domain>)
• Config entry: Polyconnect Heat Pump (<entry_id>)
• Add-on: Polyconnect Bridge (<slug>) — currently <state>

Proceed?
```

### Part B — Repository removal (default: keep)

Ask separately and independently:

```
Should the repositories also be removed?
Keeping them makes re-installing easier later.

• Add-on store repository: <repo_slug> (https://github.com/fradinni/ha-polyconnect)
• HACS repository: fradinni/ha-polyconnect
  ⚠ HACS repo removal requires manual UI action (no API available)

Remove repositories? (yes / no — default no)
```

Only proceed with each section if confirmed. The user may choose to remove the integration but keep repositories (common when planning to reinstall).

---

## Step 3 — Remove the config entry

All Polyconnect entities are owned by the config entry. Deleting the entry cascades entity removal automatically — **no need to call `ha_remove_entity` individually**.

```
ha_remove_helpers_integrations(
    target="<config_entry_id>",
    confirm=True
)
```

Note: the tool response shows `entity_ids: []` even when entities existed — they are already gone by the time the response is returned. This is expected; verify cleanup via entity search (Step 6), not via this response field.

Verify the entry is gone:
```
ha_get_integration(query="polyconnect")   → should return 0 entries
```

---

## Step 4 — Stop and uninstall the add-on

Only if the add-on is installed.

### 4a. Stop

```
ha_manage_addon(action="stop", slug="<add_on_slug>")
```

### 4b. Uninstall

```
ha_manage_addon(action="uninstall", slug="<add_on_slug>")
```

---

## Step 5 — Remove add-on store repository (if confirmed)

Only if the user confirmed repository removal in Step 2 Part B, and `add_on_repo_slug` is not null.

The repo slug comes from the `repositories[]` array in the `ha_get_addon(source="available")` response — **not** from the `addons[]` array. Example: `{"slug": "ecbbef75", "source": "https://github.com/fradinni/ha-polyconnect", ...}`.

```
ha_manage_addon(
    action="remove_repository",
    repository="<add_on_repo_slug>"   # e.g. "ecbbef75"
)
```

Confirm by re-running `ha_get_addon(source="available", query="polyconnect")` — the Polyconnect repo should no longer appear in `repositories[]`.

---

## Step 6 — HACS repository (manual step, if confirmed)

The `ha_manage_hacs` tool only supports `download` and `add_repository` actions — **there is no API for removing a HACS repository**. If the user confirmed repository removal:

> "To remove the HACS repository, go to **HACS → Integrations → ⋮ menu next to Polyconnect → Remove** in the Home Assistant UI."

---

## Step 7 — Verify clean state

Run in parallel:

```
ha_get_addon()                                         → no polyconnect_bridge
ha_get_integration(query="polyconnect")                → 0 entries
ha_search(query="polyconnect", domain_filter="sensor")
ha_search(query="polyconnect", domain_filter="climate")
ha_search(query="polyconnect", domain_filter="binary_sensor")
ha_search(query="polyconnect", domain_filter="switch")
```

Also check repos if their removal was requested:
```
ha_get_addon(source="available", query="polyconnect")  → repositories[] should be empty
ha_get_hacs_info(action="search", query="polyconnect") → installed: false (or absent)
```

---

## Step 8 — Outcome summary

```
Polyconnect uninstall complete.

✓ <N> entities removed (via config entry cascade)
✓ Config entry deleted
✓ Add-on stopped and uninstalled
✓ Add-on store repository removed   (or — skipped, kept for reinstall)
⚠ HACS repository: manual removal required in HA UI   (or — skipped, kept for reinstall)
```

---

## Key identifiers

| Item | Value |
|---|---|
| GitHub repo | `https://github.com/fradinni/ha-polyconnect` |
| HACS repo ID | `fradinni/ha-polyconnect` |
| Add-on name | `Polyconnect Bridge` |
| Add-on slug (base) | `polyconnect_bridge` (actual slug has hash prefix — discovered at runtime) |
| Add-on store repo slug | `ecbbef75` (discovered at runtime from `ha_get_addon(source="available").repositories[].slug`) |
| Integration domain | `polyconnect` |
| HACS category | `integration` |

---

## Partial-state handling

| Scenario | Action |
|---|---|
| Nothing installed | Tell user, exit |
| Add-on missing, integration present | Skip Steps 4–5 |
| Config entry missing, entities orphaned | Remove entities individually with `ha_remove_entity` |
| Repos not registered | Skip Steps 5–6 |
