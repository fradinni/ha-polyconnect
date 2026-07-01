#!/bin/bash
set -e

# Ensure persistent data directory exists
mkdir -p /data

# Read add-on options
OPTIONS_FILE=/data/options.json
get_option() {
    python3 -c "import json; d=json.load(open('$OPTIONS_FILE')); print(d.get('$1',''))" 2>/dev/null || echo ""
}

export POLYCONNECT_LOG_LEVEL=$(get_option log_level)
export POLYCONNECT_EMAIL=$(get_option email)
export POLYCONNECT_PASSWORD=$(get_option password)

ADDON_DIR="$(cd "$(dirname "$0")" && pwd)"
BRIDGE_VERSION=$(grep -oP '(?<=^version: ")[^"]+' "$ADDON_DIR/config.yaml" 2>/dev/null || echo "unknown")
echo "Starting Polyconnect Bridge v${BRIDGE_VERSION}..."
echo "  Credentials stored in: /data/"
echo "  Log level: ${POLYCONNECT_LOG_LEVEL:-info}"
echo "  Email: ${POLYCONNECT_EMAIL:-<not configured>}"

exec python3 /server.py
