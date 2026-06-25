# AGENTS.md — ha-polyconnect

## Project Overview
Home Assistant custom integration + local Supervisor add-on for Polyconnect/Ingeli pool heat pumps.
Two components: the HA integration (`custom_components/polyconnect/`) and a Flask+Playwright bridge server (`polyconnect_bridge/`).

## Build / Validation Commands

```bash
# Validate HA integration metadata (requires Docker / act or a push to CI)
# CI runs hassfest and HACS validation — no local equivalents are configured

# Bridge add-on (local dev)
cd polyconnect_bridge
pip install -r requirements.txt
playwright install chromium
python server.py          # run bridge locally on :8765

# Utility scripts (JWT capture, standalone bridge)
python scripts/get-jwt.py
python scripts/polyconnect-server.py
```

There is **no test suite and no linter configured**. CI only runs `hassfest` and `hacs/action`.

## Project Structure

```
ha-polyconnect/
├── custom_components/polyconnect/   # HA integration (Python 3.12)
│   ├── manifest.json                # domain, version, iot_class
│   ├── const.py                     # DOMAIN, all constants, entity descriptions
│   ├── coordinator.py               # DataUpdateCoordinator subclass
│   ├── api.py                       # HTTP client (PolyconnectAPI)
│   ├── config_flow.py               # UI config flow
│   ├── entity.py                    # PolyconnectEntity base class
│   ├── sensor.py / binary_sensor.py / climate.py / switch.py
│   ├── strings.json / translations/en.json
│   └── __init__.py                  # async_setup_entry / async_unload_entry
├── polyconnect_bridge/              # Local HA add-on (Flask + Playwright)
│   ├── config.yaml                  # Add-on manifest (version, schema, ports)
│   ├── server.py                    # REST bridge wrapping Playwright sync API
│   ├── Dockerfile
│   ├── requirements.txt             # flask, playwright
│   └── run.sh
├── docs/                            # API reverse-engineering notes
├── scripts/                         # Dev utilities (JWT capture)
├── hacs.json
└── repository.yaml
```

## Code Style (Python)

- **Python 3.12**, `from __future__ import annotations` in every file.
- **Imports order:** stdlib → third-party → HA core → local (relative `from .module import ...`).
- **Type annotations:** always use `X | None` / `X | Y` (never `Optional`, never `Union`). All public function signatures and class attributes annotated.
- **Naming:** `PascalCase` classes, `snake_case` functions/methods, `UPPER_SNAKE_CASE` constants, `_single_underscore` private helpers. HA async callbacks prefixed `async_*`.
- **JSON data keys** from the bridge are `camelCase` (e.g. `waterTemperature`, `heatPumpActive`). Map these in `const.py`; never hardcode key strings outside `const.py`.
- **HA patterns:** Use `_attr_*` class attributes (not `@property`) where possible. Store coordinator on `entry.runtime_data`. Use `ConfigEntryNotReady` / `ConfigEntryAuthFailed` / `UpdateFailed` — never raise generic exceptions from HA callbacks.
- **Optimistic updates:** mutate `coordinator.data` → `async_write_ha_state()` → send API command → `asyncio.sleep(N)` → `async_request_refresh()`.
- **Error handling:** Custom exception hierarchy (`PolyconnectError` → `AuthExpiredError`). Re-raise as HA exceptions at integration boundaries. Use `LOGGER.exception(...)` with bare `except Exception` only in config flow.
- **Entity descriptions:** Extend `SensorEntityDescription` / `BinarySensorEntityDescription` with `@dataclass(frozen=True, kw_only=True)`.
- **Section dividers:** Use `# ── Title ──────` style comments for logical grouping in longer files.
- **Bridge (server.py):** Uses synchronous `threading.Lock` (not asyncio) because Playwright sync API requires it. `flask` runs with `threaded=False`. Long JS snippets stored as module-level string constants.

## Security Notes
- Never commit `scripts/captured_token.txt` (gitignored). Tokens are short-lived JWTs.
- `docs/` contains embedded APK credentials from reverse engineering — do not redistribute.
