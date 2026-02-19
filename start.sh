#!/bin/bash
# ═══════════════════════════════════════════════════════════
# LEON — Full System Launcher
# Starts Leon core + dashboard + voice + WhatsApp bridge
#
# Usage:
#   ./start.sh              # Full mode (CLI + voice + dashboard + WhatsApp)
#   ./start.sh --no-voice   # Skip voice (no mic needed)
#   ./start.sh --no-wa      # Skip WhatsApp bridge
# ═══════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")"

# Ensure PATH includes local bins
export PATH="$HOME/.local/bin:$PATH"

SKIP_VOICE=false
SKIP_WA=false
for arg in "$@"; do
    case "$arg" in
        --no-voice) SKIP_VOICE=true ;;
        --no-wa)    SKIP_WA=true ;;
    esac
done

echo ""
echo "╔═══════════════════════════════════════╗"
echo "║     LEON — AI Orchestration System    ║"
echo "╚═══════════════════════════════════════╝"
echo ""

# Kill any existing Leon/bridge processes
echo "[start] Cleaning up old processes..."
kill $(ss -tlnp sport = :3000 2>/dev/null | grep -oP 'pid=\K\d+') 2>/dev/null || true
kill $(pgrep -f "node bridge.js") 2>/dev/null || true
sleep 1

# Start Leon
echo "[start] Launching Leon..."
if [ "$SKIP_VOICE" = true ]; then
    MODE="--cli --dashboard"
else
    MODE="--full"
fi

nohup bash -c "exec 0< <(sleep infinity); python3 main.py $MODE" > logs/leon_startup.log 2>&1 &
LEON_PID=$!
echo "[start] Leon PID: $LEON_PID"

# Wait for dashboard to be ready
echo "[start] Waiting for dashboard..."
for i in $(seq 1 30); do
    if ss -tlnp sport = :3000 2>/dev/null | grep -q 3000; then
        echo "[start] Dashboard is up on http://localhost:3000"
        break
    fi
    sleep 1
done

# Extract tokens from log
SESSION_TOKEN=$(grep -a 'session token:' logs/leon_startup.log 2>/dev/null | tail -1 | awk '{print $NF}')
API_TOKEN=$(grep -a 'API token:' logs/leon_startup.log 2>/dev/null | tail -1 | awk '{print $NF}')

echo "[start] Session token: $SESSION_TOKEN"
echo "[start] API token: $API_TOKEN"

# Start WhatsApp bridge
if [ "$SKIP_WA" = false ] && [ -f "integrations/whatsapp/bridge.js" ] && command -v node &>/dev/null; then
    if [ -n "$API_TOKEN" ]; then
        echo "[start] Launching WhatsApp bridge..."
        cd integrations/whatsapp
        LEON_API_TOKEN="$API_TOKEN" \
        LEON_API_URL="http://127.0.0.1:3000" \
        LEON_WHATSAPP_ALLOWED="17275427167" \
        nohup node bridge.js > ../../logs/whatsapp_bridge.log 2>&1 &
        WA_PID=$!
        cd ../..
        echo "[start] WhatsApp bridge PID: $WA_PID"
        echo "[start] WhatsApp log: logs/whatsapp_bridge.log"
        echo "[start] If QR scan needed: tail -f logs/whatsapp_bridge.log"
    else
        echo "[start] No API token found — skipping WhatsApp bridge"
    fi
else
    echo "[start] WhatsApp bridge skipped"
fi

echo ""
echo "╔═══════════════════════════════════════╗"
echo "║  Leon is running!                     ║"
echo "║  Dashboard: http://localhost:3000      ║"
echo "║  Token: $SESSION_TOKEN  ║"
echo "╚═══════════════════════════════════════╝"
echo ""
echo "Logs:"
echo "  Leon:     tail -f logs/leon_startup.log"
echo "  WhatsApp: tail -f logs/whatsapp_bridge.log"
echo ""
echo "To stop: kill $LEON_PID ${WA_PID:-}"
