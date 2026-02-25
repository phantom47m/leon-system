#!/bin/bash
# Leon startup script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Kill existing Leon processes
EXISTING=$(pgrep -f "python3 main.py" 2>/dev/null)
if [ -n "$EXISTING" ]; then
    echo "Stopping existing Leon (PID: $EXISTING)..."
    kill $EXISTING 2>/dev/null
    sleep 2
fi

# Rotate startup log — keep last 5000 lines only
LOG="logs/leon_startup.log"
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 5000 ]; then
    tail -5000 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
    echo "Log rotated."
fi

MODE="${1:---left-brain}"
echo "Starting Leon $MODE..."
python3 main.py $MODE >> "$LOG" 2>&1 &
sleep 4

LEON_PID=$(pgrep -f "python3 main.py" | head -1)
if [ -n "$LEON_PID" ]; then
    echo "Leon running (PID: $LEON_PID)"
    tail -6 logs/leon_system.log
else
    echo "ERROR — check logs/leon_startup.log"
    tail -15 "$LOG"
    exit 1
fi

# Auto-start Discord bridge if token is configured
DISCORD_TOKEN=$(python3 -c "
import yaml, sys
try:
    c = yaml.safe_load(open('config/user_config.yaml'))
    print(c.get('discord_bot_token', ''))
except:
    print('')
" 2>/dev/null)
if [ -n "$DISCORD_TOKEN" ]; then
    # Kill any existing Discord bridge
    OLD_PID=$(cat /tmp/leon_discord.pid 2>/dev/null)
    [ -n "$OLD_PID" ] && kill "$OLD_PID" 2>/dev/null
    bash integrations/discord/start.sh
fi
