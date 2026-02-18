#!/bin/bash
# Start the WhatsApp bridge for Leon
# Usage: ./start.sh
#
# On first run, scan the QR code with your phone.
# After that, the session is saved and no QR needed.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Get API token from Leon's logs or ask for it
if [ -z "$LEON_API_TOKEN" ]; then
    # Try to grab it from Leon's recent log output
    TOKEN=$(grep "API token" /home/deansabr/leon-system/logs/leon_system.log 2>/dev/null | tail -1 | grep -oP '(?<=API token: )\S+')
    if [ -n "$TOKEN" ]; then
        export LEON_API_TOKEN="$TOKEN"
        echo "[start] Found API token from Leon logs"
    else
        echo "[start] ERROR: No LEON_API_TOKEN found."
        echo "[start] Start Leon first, then copy the API token from the dashboard output."
        echo "[start] Run: LEON_API_TOKEN=<token> ./start.sh"
        exit 1
    fi
fi

export LEON_API_URL="${LEON_API_URL:-http://127.0.0.1:3000}"
export LEON_WHATSAPP_ALLOWED="${LEON_WHATSAPP_ALLOWED:-17275427167}"

echo "[start] Bridge → $LEON_API_URL"
echo "[start] Allowed: $LEON_WHATSAPP_ALLOWED"
echo ""

node bridge.js
