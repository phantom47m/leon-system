#!/bin/bash
# Start the Discord bridge for Leon AI
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$SCRIPT_DIR/../.."
cd "$BASE_DIR"

# Install dependencies if needed
if ! python3 -c "import discord" 2>/dev/null; then
    echo "[discord] Installing discord.py..."
    pip install -r "$SCRIPT_DIR/requirements.txt" -q
fi

# Read Discord bot token from user_config.yaml
DISCORD_TOKEN=$(python3 -c "
import yaml, sys
try:
    c = yaml.safe_load(open('config/user_config.yaml'))
    print(c.get('discord_bot_token', ''))
except:
    print('')
" 2>/dev/null)

ALLOWED_USERS=$(python3 -c "
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

LEON_TOKEN=$(cat config/api_token.txt 2>/dev/null || echo "")

ARGS="--token $DISCORD_TOKEN --leon-url http://localhost:3000 --leon-token $LEON_TOKEN"
if [ -n "$ALLOWED_USERS" ]; then
    ARGS="$ARGS --allowed-users $ALLOWED_USERS"
fi

echo "[discord] Starting Discord bridge..."
python3 "$SCRIPT_DIR/bot.py" $ARGS >> logs/discord_bridge.log 2>&1 &
echo $! > /tmp/leon_discord.pid
echo "[discord] Bridge started (PID: $!)"
