#!/bin/bash
set -e

OPTIONS_FILE=/data/options.json
get_option() {
    python3 -c "import json; d=json.load(open('$OPTIONS_FILE')); print(d.get('$1',''))" 2>/dev/null || echo ""
}

export POLYCONNECT_TOKEN=$(get_option token)
export POLYCONNECT_HEAT_PUMP_ID=$(get_option heat_pump_id)
export POLYCONNECT_LOG_LEVEL=$(get_option log_level)

echo "Starting Polyconnect Bridge Server on port 8765..."
[ -z "$POLYCONNECT_TOKEN" ] && echo "WARNING: No token configured."

exec python3 /server.py
