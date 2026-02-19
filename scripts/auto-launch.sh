#!/bin/bash
# Waits for current Claude Code session to release, then launches agents
# This runs in background — close your Claude Code chat and go to sleep

cd /home/deansabr/leon-system
unset CLAUDECODE
unset CLAUDE_CODE_ENTRYPOINT
export PATH="/home/deansabr/.local/bin:$PATH"
OUTPUTS="data/agent_outputs"
BRIEFS="data/task_briefs"
mkdir -p "$OUTPUTS"

echo "[auto-launch] Waiting for Claude Code session to close..."

# Poll until claude --print works (session released)
while true; do
    RESULT=$(timeout 15 claude --print -p "say ok" 2>&1)
    if [ -n "$RESULT" ] && echo "$RESULT" | grep -qi "ok"; then
        echo "[auto-launch] Claude CLI is free! Launching agents..."
        break
    fi
    echo "[auto-launch] Session still active, retrying in 30s..."
    sleep 30
done

# Spawn all 6 agents
TASKS=("01_bugs" "02_dashboard_ui" "03_system_skills" "04_voice" "05_cleanup" "06_personality")
LABELS=("Bug Fixes" "Dashboard UI" "System Skills" "Voice System" "Code Cleanup" "Personality")

for i in "${!TASKS[@]}"; do
    TASK="${TASKS[$i]}"
    LABEL="${LABELS[$i]}"
    BRIEF="$BRIEFS/self_improve_${TASK}.md"
    OUTPUT="$OUTPUTS/self_improve_${TASK}.log"
    ERROR="$OUTPUTS/self_improve_${TASK}.err"

    if [ ! -f "$BRIEF" ]; then
        echo "[auto-launch] SKIP: $LABEL"
        continue
    fi

    echo "[auto-launch] Spawning: $LABEL"
    claude --print --dangerously-skip-permissions - < "$BRIEF" > "$OUTPUT" 2> "$ERROR" &
    sleep 3
done

echo "[auto-launch] All agents spawned! They're working now."

# Send WhatsApp notification
curl -s -X POST http://127.0.0.1:3001/send \
    -H "Content-Type: application/json" \
    -d '{"number":"17275427167","message":"All 6 improvement agents are now running! I'\''ll update you every 5 min. Go to sleep."}' || true

# Wait for all
wait
echo "[auto-launch] All agents finished."
curl -s -X POST http://127.0.0.1:3001/send \
    -H "Content-Type: application/json" \
    -d '{"number":"17275427167","message":"All agents finished! Check results when you wake up."}' || true
