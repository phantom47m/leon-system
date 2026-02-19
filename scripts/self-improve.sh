#!/bin/bash
# Leon Self-Improvement — Spawns parallel Claude agents + WhatsApp progress monitor
# Run this AFTER closing any active Claude Code sessions
#
# Usage: bash scripts/self-improve.sh

cd "$(dirname "$0")/.."
PROJECT="/home/deansabr/leon-system"
BRIEFS="$PROJECT/data/task_briefs"
OUTPUTS="$PROJECT/data/agent_outputs"
WA_DIR="$PROJECT/integrations/whatsapp"
mkdir -p "$OUTPUTS"

echo "=========================================="
echo "  Leon Self-Improvement — Full Launch"
echo "=========================================="
echo ""

# Check claude is available
if ! command -v claude &> /dev/null; then
    echo "ERROR: claude CLI not found in PATH"
    exit 1
fi

# Unset Claude Code session vars to avoid conflicts
unset CLAUDECODE
unset CLAUDE_CODE_ENTRYPOINT

# ── Step 1: Start WhatsApp bridge for progress updates ──
echo "[1/3] Starting WhatsApp bridge..."
if [ -f "$WA_DIR/bridge.js" ] && command -v node &> /dev/null; then
    cd "$WA_DIR"
    LEON_API_URL="http://127.0.0.1:3000" \
    LEON_API_TOKEN="$(grep -a 'API token:' $PROJECT/logs/leon_system.log | tail -1 | awk '{print $NF}')" \
    LEON_WHATSAPP_ALLOWED="17275427167" \
    nohup node bridge.js > "$OUTPUTS/whatsapp_bridge.log" 2>&1 &
    WA_PID=$!
    echo "  WhatsApp bridge PID: $WA_PID"
    cd "$PROJECT"
    sleep 5  # Let it connect
else
    echo "  WhatsApp bridge not available — skipping"
    WA_PID=""
fi

# ── Step 2: Spawn all improvement agents ──
echo ""
echo "[2/3] Spawning improvement agents..."
PIDS=()
TASKS=("01_bugs" "02_dashboard_ui" "03_system_skills" "04_voice" "05_cleanup" "06_personality")
LABELS=("Bug Fixes" "Dashboard UI" "System Skills" "Voice System" "Code Cleanup" "Personality")

for i in "${!TASKS[@]}"; do
    TASK="${TASKS[$i]}"
    LABEL="${LABELS[$i]}"
    BRIEF="$BRIEFS/self_improve_${TASK}.md"
    OUTPUT="$OUTPUTS/self_improve_${TASK}.log"
    ERROR="$OUTPUTS/self_improve_${TASK}.err"

    if [ ! -f "$BRIEF" ]; then
        echo "  SKIP: $LABEL (brief not found)"
        continue
    fi

    echo "  Agent #$((i+1)): $LABEL"
    claude --print --dangerously-skip-permissions - < "$BRIEF" \
        > "$OUTPUT" 2> "$ERROR" &
    PIDS+=($!)
    echo "    PID: ${PIDS[-1]} | Output: $OUTPUT"

    # Stagger spawns by 3 seconds to avoid rate limiting
    sleep 3
done

# ── Step 3: Start progress monitor (WhatsApp updates every 5 min) ──
echo ""
echo "[3/3] Starting WhatsApp progress monitor..."
if [ -n "$WA_PID" ]; then
    nohup python3 "$PROJECT/scripts/agent-monitor.py" > "$OUTPUTS/monitor.log" 2>&1 &
    MON_PID=$!
    echo "  Monitor PID: $MON_PID"
else
    echo "  Skipped (no WhatsApp bridge)"
    MON_PID=""
fi

echo ""
echo "=========================================="
echo "  ${#PIDS[@]} agents spawned!"
echo "  WhatsApp updates every 5 min to your phone"
echo "  Outputs: $OUTPUTS/self_improve_*.log"
echo "=========================================="
echo ""
echo "You can close this terminal — everything runs in background."
echo "Go to sleep! Leon's got this. 🤖"
echo ""

# Wait for all agents and report
FAILED=0
for i in "${!PIDS[@]}"; do
    PID="${PIDS[$i]}"
    LABEL="${LABELS[$i]}"
    if wait "$PID" 2>/dev/null; then
        echo "  DONE: $LABEL (PID $PID)"
    else
        echo "  FAIL: $LABEL (PID $PID)"
        FAILED=$((FAILED + 1))
    fi
done

echo ""
echo "=========================================="
echo "  Complete: $((${#PIDS[@]} - FAILED))/${#PIDS[@]} agents succeeded"
if [ $FAILED -gt 0 ]; then
    echo "  Check .err files for failures"
fi
echo "=========================================="

# Cleanup
if [ -n "$MON_PID" ]; then
    kill "$MON_PID" 2>/dev/null
fi
if [ -n "$WA_PID" ]; then
    kill "$WA_PID" 2>/dev/null
fi
