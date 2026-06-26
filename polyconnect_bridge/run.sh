#!/bin/bash
set -e

# Ensure persistent data directory exists
mkdir -p /data

# Read log level from add-on options
OPTIONS_FILE=/data/options.json
get_option() {
    python3 -c "import json; d=json.load(open('$OPTIONS_FILE')); print(d.get('$1',''))" 2>/dev/null || echo ""
}

export POLYCONNECT_LOG_LEVEL=$(get_option log_level)

echo "Starting Polyconnect Bridge v2.0.0..."
echo "  Credentials stored in: /data/"
echo "  Log level: ${POLYCONNECT_LOG_LEVEL:-info}"

# Check if credentials exist
if [ -f /data/token.txt ] && [ -s /data/token.txt ]; then
    echo "  Token: configured"
else
    echo "  Token: NOT configured — open add-on UI to capture"
fi

if [ -f /data/ids.json ]; then
    echo "  IDs: $(cat /data/ids.json)"
else
    echo "  IDs: NOT configured — open add-on UI to capture"
fi

exec python3 /server.py
