#!/bin/bash
# Start the Discord bridge for Leon AI
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$SCRIPT_DIR/../.."
cd "$BASE_DIR"

# Use venv python directly (no activate needed)
PYTHON="$BASE_DIR/venv/bin/python"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python3"
fi

# Install full requirements (discord.py, aiohttp, Pillow, pyscreenshot, psutil)
"$PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" -q

# Read Discord bot token from user_config.yaml
DISCORD_TOKEN=$("$PYTHON" -c "
import yaml, sys
try:
    c = yaml.safe_load(open('config/user_config.yaml'))
    print(c.get('discord_bot_token', ''))
except:
    print('')
" 2>/dev/null)

ALLOWED_USERS=$("$PYTHON" -c "
import yaml
try:
    c = yaml.safe_load(open('config/user_config.yaml'))
    users = c.get('discord_allowed_users', '')
    print(str(users) if users else '')
except:
    print('')
" 2>/dev/null)

if [ -z "$DISCORD_TOKEN" ]; then
    echo "[discord] No Discord bot token configured. Add it in the setup wizard."
    exit 1
fi

# Kill ALL existing discord bot processes (not just the pid file)
pkill -f "integrations/discord/bot.py" 2>/dev/null
sleep 1

LEON_TOKEN=$(cat config/api_token.txt 2>/dev/null || echo "")

# Absolute path to config root so dashboard can find projects.yaml
CONFIG_ROOT="$(cd "$BASE_DIR" && pwd)/config"

ARGS="--token $DISCORD_TOKEN --leon-url http://localhost:3000 --leon-token $LEON_TOKEN"
ARGS="$ARGS --config-root $CONFIG_ROOT"
if [ -n "$ALLOWED_USERS" ]; then
    ARGS="$ARGS --allowed-users $ALLOWED_USERS"
fi

# Optional guild ID from user_config.yaml
GUILD_ID=$("$PYTHON" -c "
import yaml
try:
    c = yaml.safe_load(open('config/user_config.yaml'))
    print(c.get('discord_guild_id', '') or '')
except:
    print('')
" 2>/dev/null)
[ -n "$GUILD_ID" ] && ARGS="$ARGS --guild-id $GUILD_ID"

echo "[discord] Starting Discord bridge..."
# Export Wayland env vars so the screenshot subprocess can reach the compositor
export DISPLAY="${DISPLAY:-:1}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
"$PYTHON" "$SCRIPT_DIR/bot.py" $ARGS >> logs/discord_bridge.log 2>&1 &
echo $! > /tmp/leon_discord.pid
echo "[discord] Bridge started (PID: $!)"
