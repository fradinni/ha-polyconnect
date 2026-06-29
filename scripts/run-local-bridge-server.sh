#!/usr/bin/env bash
# run-local-bridge-server.sh — Start the Polyconnect bridge locally for testing.
#
# Usage:
#   ./scripts/bridge/run-local-bridge-server.sh
#
# What it does:
#   1. Copies scripts/capture/captured_token.txt + captured_ids.json into a
#      temp data dir (/tmp/polyconnect_data) so the bridge finds its credentials.
#   2. Starts polyconnect_bridge/server.py on :8765 in the foreground (Ctrl-C to stop).
#
# Requirements:
#   pip install flask playwright && playwright install chromium
#
# Quick smoke test (in another terminal):
#   curl http://localhost:8765/status
#   curl -X POST http://localhost:8765/mode -H 'Content-Type: application/json' -d '{"mode":"Eco"}'
#   curl -X POST http://localhost:8765/setpoint -H 'Content-Type: application/json' -d '{"temperature":28}'

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CAPTURE_DIR="$REPO_ROOT/scripts/capture"
BRIDGE_DIR="$REPO_ROOT/polyconnect_bridge"
DATA_DIR="/tmp/polyconnect_data"

# Verify captured credentials exist
if [[ ! -f "$CAPTURE_DIR/captured_token.txt" || ! -f "$CAPTURE_DIR/captured_ids.json" ]]; then
  echo "ERROR: Missing captured credentials in $CAPTURE_DIR"
  echo "Run scripts/capture/capture.py first to capture token and device IDs."
  exit 1
fi

# Kill any stale server on port 8765
fuser -k 8765/tcp 2>/dev/null || true

mkdir -p "$DATA_DIR"
cp "$CAPTURE_DIR/captured_token.txt" "$DATA_DIR/token.txt"
cp "$CAPTURE_DIR/captured_ids.json"  "$DATA_DIR/ids.json"

echo "Credentials loaded from $CAPTURE_DIR"
echo "Bridge starting on http://localhost:8765 — Ctrl-C to stop"
echo ""

exec env POLYCONNECT_DATA_DIR="$DATA_DIR" python "$BRIDGE_DIR/server.py" </dev/null
