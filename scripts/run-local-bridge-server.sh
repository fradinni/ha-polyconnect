#!/usr/bin/env bash
# run-local-bridge-server.sh — Start the Polyconnect bridge locally for testing.
#
# v2: uses native login with credentials from .env (POLYCONNECT_EMAIL/PASSWORD).
# Heat pump / installation IDs can come from .env (POLYCONNECT_HEAT_PUMP_ID,
# POLYCONNECT_INSTALLATION_ID) or from a pre-existing /tmp/polyconnect_data/ids.json.
#
# Usage:
#   ./scripts/run-local-bridge-server.sh
#
# Requirements:
#   pip install -r polyconnect_bridge/requirements.txt
#   playwright install chromium
#
# Quick smoke test (in another terminal):
#   curl http://localhost:8765/health
#   curl http://localhost:8765/status
#   curl -X POST http://localhost:8765/auth/refresh
#   curl -X POST http://localhost:8765/mode -H 'Content-Type: application/json' -d '{"mode":"Eco"}'

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRIDGE_DIR="$REPO_ROOT/polyconnect_bridge"
DATA_DIR="/tmp/polyconnect_data"
ENV_FILE="$REPO_ROOT/.env"

# Load .env if present (POLYCONNECT_EMAIL, POLYCONNECT_PASSWORD, etc.)
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [[ -z "${POLYCONNECT_EMAIL:-}" || -z "${POLYCONNECT_PASSWORD:-}" ]]; then
  echo "ERROR: POLYCONNECT_EMAIL / POLYCONNECT_PASSWORD not set"
  echo "Add them to $ENV_FILE or export them in your shell."
  exit 1
fi

# Kill any stale server on port 8765
fuser -k 8765/tcp 2>/dev/null || true

mkdir -p "$DATA_DIR"

echo "Bridge starting on http://localhost:8765 — Ctrl-C to stop"
echo "  Data dir : $DATA_DIR"
echo "  Email    : $POLYCONNECT_EMAIL"
echo "  Heat pump: ${POLYCONNECT_HEAT_PUMP_ID:-<from ids.json>}"
echo ""

exec env POLYCONNECT_DATA_DIR="$DATA_DIR" \
         POLYCONNECT_EMAIL="$POLYCONNECT_EMAIL" \
         POLYCONNECT_PASSWORD="$POLYCONNECT_PASSWORD" \
         POLYCONNECT_HEAT_PUMP_ID="${POLYCONNECT_HEAT_PUMP_ID:-}" \
         POLYCONNECT_INSTALLATION_ID="${POLYCONNECT_INSTALLATION_ID:-}" \
         python3 "$BRIDGE_DIR/server.py" </dev/null
