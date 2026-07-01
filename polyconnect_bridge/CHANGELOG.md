# Changelog

## 2.2.0

- **Breaking config change:** removed `heat_pump_id` and `installation_id`
  from the add-on options. These are now fully auto-discovered from the SPA on
  first login and persisted to `/data/ids.json`. If you had them set, they're
  ignored on upgrade — no action needed.
- Add-on Configuration tab now only shows credentials (`email`, `password`)
  and `log_level`.

## 2.1.0

- Auto-discover installation **name** from `/pools-overview`.
- Ingress control panel overhaul: pool-water hero, live pump list, session /
  terminal / bridge status cards.
- Reset Auth & IDs button clears all persisted state and restarts the process.

## 2.0.0

- **Native login** replaces the mitmproxy capture flow. No more phone-side
  proxy setup — just enter your email/password.
- Multi-pump support: `/pumps` endpoint lists every heat pump on the
  installation; per-pump command routes at `/pumps/<id>/...`.
- Auto-discovery of `installation_id` and the full heat-pump list from the
  Blazor SPA on first boot.

## 1.0.4

- Extract compressor / filtration status from the info panel.
- Rename compressor → fan; add defrost binary sensor.
- Validate temperature type and mode value at the REST layer.
- Fix TOCTOU race in the capture-manager monitor loop.

## 1.0.3

- Replace the Flask dev server with `waitress` for a proper WSGI server.
- Preserve `config.yaml` in the Docker image so `run.sh` can read the version.
- Dispatch all Playwright calls through a dedicated OS thread (fixes hangs
  on some HA installs).

## 1.0.2

- Improve DOM-scraping reliability and version management.
