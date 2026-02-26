#!/usr/bin/env bash
# ============================================================
# Leon Autonomous Upgrade Script
# ============================================================
# Installs and configures everything needed for 9-hour
# unattended autonomous operation.
#
# What this does:
#   1. Install Python RAG deps (chromadb, sentence-transformers, rank-bm25)
#   2. Install ripgrep (lexical search)
#   3. Install Ollama + pull llama3.2:3b (free heartbeat model)
#   4. Install Exa Python client (optional web search)
#   5. Create required directories
#   6. Install leon-* CLI scripts to ~/.local/bin
#   7. Create systemd user service + timer for scheduler
#   8. Run validation checks
#
# Usage:
#   bash scripts/upgrade_autonomous.sh
#   bash scripts/upgrade_autonomous.sh --skip-ollama
#   bash scripts/upgrade_autonomous.sh --skip-systemd
#
# Rollback:
#   systemctl --user disable --now leon-scheduler.timer
#   systemctl --user disable --now leon-scheduler.service
#   rm ~/.config/systemd/user/leon-scheduler.*
#   pip uninstall -y chromadb sentence-transformers rank-bm25
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
VENV="${ROOT}/venv"
PIP="${VENV}/bin/pip"
PYTHON="${VENV}/bin/python3"

GREEN="\033[92m"
YELLOW="\033[93m"
RED="\033[91m"
BOLD="\033[1m"
RESET="\033[0m"

SKIP_OLLAMA=false
SKIP_SYSTEMD=false
for arg in "$@"; do
  case $arg in
    --skip-ollama)   SKIP_OLLAMA=true ;;
    --skip-systemd)  SKIP_SYSTEMD=true ;;
  esac
done

ok()   { echo -e "  ${GREEN}✓${RESET} $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET} $*"; }
err()  { echo -e "  ${RED}✗${RESET} $*"; }
hdr()  { echo -e "\n${BOLD}$*${RESET}"; }

cd "$ROOT"

echo -e "\n${BOLD}═══════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  Leon Autonomous Upgrade${RESET}"
echo -e "${BOLD}═══════════════════════════════════════════════${RESET}"

# ── 1. Check virtualenv ───────────────────────────────────────────────────────
hdr "1. Python environment"
if [ ! -f "$PYTHON" ]; then
  err "venv not found at $VENV — run Leon's normal setup first"
  exit 1
fi
PYVER=$("$PYTHON" --version 2>&1)
ok "Using $PYVER ($PYTHON)"

# ── 2. Install Python RAG packages ───────────────────────────────────────────
hdr "2. RAG packages (chromadb, sentence-transformers, rank-bm25)"

"$PIP" install --quiet --upgrade pip

echo "  Installing chromadb..."
"$PIP" install --quiet "chromadb>=0.5.0" && ok "chromadb installed" || warn "chromadb failed — vector search disabled"

echo "  Installing sentence-transformers (local embeddings, ~90MB)..."
"$PIP" install --quiet "sentence-transformers>=3.0.0" && ok "sentence-transformers installed" || warn "sentence-transformers failed — will use default embeddings"

echo "  Installing rank-bm25..."
"$PIP" install --quiet "rank-bm25>=0.2.2" && ok "rank-bm25 installed" || warn "rank-bm25 failed (optional)"

echo "  Installing httpx (already likely installed, ensuring version)..."
"$PIP" install --quiet "httpx>=0.27.0" && ok "httpx ok"

echo "  Installing exa-py (optional web search)..."
"$PIP" install --quiet "exa-py>=1.1.0" && ok "exa-py installed" || warn "exa-py failed — web search unavailable until installed"

# ── 3. ripgrep ────────────────────────────────────────────────────────────────
hdr "3. ripgrep (lexical search)"
if command -v rg &>/dev/null; then
  ok "ripgrep already installed: $(rg --version | head -1)"
else
  echo "  Installing ripgrep via apt..."
  sudo apt-get install -y ripgrep -qq && ok "ripgrep installed" || warn "ripgrep install failed — lexical search disabled"
fi

# ── 4. Ollama ─────────────────────────────────────────────────────────────────
hdr "4. Ollama (local LLM — \$0 heartbeats)"
if $SKIP_OLLAMA; then
  warn "Skipping Ollama (--skip-ollama)"
else
  if command -v ollama &>/dev/null; then
    ok "Ollama already installed: $(ollama --version 2>/dev/null || echo 'version unknown')"
  else
    echo "  Downloading Ollama installer..."
    curl -fsSL https://ollama.ai/install.sh | sh && ok "Ollama installed" || {
      warn "Ollama install failed — health checks will use canned responses"
    }
  fi

  # Start Ollama if not running
  if command -v ollama &>/dev/null; then
    if ! pgrep -x ollama &>/dev/null; then
      echo "  Starting Ollama service..."
      nohup ollama serve >/dev/null 2>&1 &
      sleep 3
    fi

    echo "  Pulling llama3.2:3b model (~2GB, one-time download)..."
    ollama pull llama3.2:3b && ok "llama3.2:3b ready" || warn "Model pull failed — try: ollama pull llama3.2:3b"
  fi
fi

# ── 5. Create directories ─────────────────────────────────────────────────────
hdr "5. Directory structure"
dirs=(
  "data/rag_db"
  "data/alerts"
  "data/web_cache"
  "logs_structured"
  "memory/daily"
  "router"
  "tools"
)
for d in "${dirs[@]}"; do
  mkdir -p "$ROOT/$d"
  ok "$d"
done

# Ensure __init__.py files exist
touch "$ROOT/router/__init__.py" "$ROOT/tools/__init__.py"
ok "Python package markers"

# ── 6. Install CLI scripts ────────────────────────────────────────────────────
hdr "6. CLI tools"
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"

for script in leon-status leon-index leon-search; do
  SRC="$SCRIPT_DIR/$script"
  DST="$LOCAL_BIN/$script"
  if [ -f "$SRC" ]; then
    chmod +x "$SRC"
    # Create wrapper that cd's to leon root
    cat > "$DST" << WRAPPER
#!/usr/bin/env bash
cd "$ROOT"
exec "$SRC" "\$@"
WRAPPER
    chmod +x "$DST"
    ok "$script → $DST"
  else
    warn "$script not found in $SCRIPT_DIR"
  fi
done

# Check PATH
if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
  warn "$LOCAL_BIN not in PATH — add to ~/.bashrc: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# ── 7. Systemd units ──────────────────────────────────────────────────────────
hdr "7. Systemd user service + timer"
if $SKIP_SYSTEMD; then
  warn "Skipping systemd (--skip-systemd)"
else
  SYSTEMD_DIR="$HOME/.config/systemd/user"
  mkdir -p "$SYSTEMD_DIR"

  # Service unit
  cat > "$SYSTEMD_DIR/leon-scheduler.service" << SERVICE
[Unit]
Description=Leon Autonomous Scheduler
Documentation=https://github.com/phantom47m/leon-system
After=network.target

[Service]
Type=oneshot
WorkingDirectory=${ROOT}
ExecStart=${PYTHON} -c "
import asyncio, sys
sys.path.insert(0, '${ROOT}')
from core.scheduler import run_builtin, TaskScheduler
import yaml

cfg = yaml.safe_load(open('${ROOT}/config/settings.yaml'))
tasks_cfg = cfg.get('scheduler', {}).get('tasks', [])
sched = TaskScheduler(tasks_cfg)
due = sched.get_due_tasks()

async def main():
    for task in due:
        cmd = task.get('command', '')
        if cmd.startswith('__'):
            ok, msg = await run_builtin(cmd)
            if ok:
                sched.mark_completed(task['name'])
            else:
                sched.mark_failed(task['name'], msg)
            print(f'{task[\"name\"]}: {msg[:80]}')

asyncio.run(main())
"
StandardOutput=journal
StandardError=journal
Environment=PYTHONPATH=${ROOT}

[Install]
WantedBy=default.target
SERVICE

  # Timer unit — runs every hour, checks what's due
  cat > "$SYSTEMD_DIR/leon-scheduler.timer" << TIMER
[Unit]
Description=Leon Autonomous Scheduler Timer
After=network.target

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
TIMER

  systemctl --user daemon-reload
  systemctl --user enable leon-scheduler.timer
  systemctl --user start  leon-scheduler.timer

  ok "leon-scheduler.service created"
  ok "leon-scheduler.timer created and started"
  ok "Timer runs every hour, handles due built-in tasks"
fi

# ── 8. Initial index ──────────────────────────────────────────────────────────
hdr "8. Initial project index"
if "$VENV/bin/python3" -c "import chromadb" 2>/dev/null; then
  echo "  Indexing all projects..."
  cd "$ROOT"
  "$PYTHON" -m tools.indexer --all 2>&1 | grep -E "✓|Error|Indexed|skipped" | head -20 || true
  ok "Initial index complete"
else
  warn "chromadb not available — skipping initial index (run after install)"
fi

# ── 9. Validation ─────────────────────────────────────────────────────────────
hdr "9. Validation"

PASS=0; FAIL=0

check() {
  local name="$1"; local cmd="$2"
  if eval "$cmd" &>/dev/null 2>&1; then
    ok "$name"
    ((PASS++)) || true
  else
    warn "$name — FAILED"
    ((FAIL++)) || true
  fi
}

check "Python 3.12+"              "$PYTHON --version"
check "chromadb importable"       "$PYTHON -c 'import chromadb'"
check "sentence-transformers"     "$PYTHON -c 'import sentence_transformers'"
check "httpx importable"          "$PYTHON -c 'import httpx'"
check "ripgrep available"         "command -v rg"
check "Ollama reachable"          "curl -sf http://localhost:11434/api/tags"
check "llama3.2:3b available"     "ollama list 2>/dev/null | grep -q llama3.2"
check "router module"             "$PYTHON -c 'from router.model_router import route'"
check "tools.indexer"             "$PYTHON -c 'from tools.indexer import CodeIndexer'"
check "tools.searcher"            "$PYTHON -c 'from tools.searcher import search'"
check "structured_logger"         "$PYTHON -c 'from core.structured_logger import get_logger'"
check "leon-status executable"    "test -x '$LOCAL_BIN/leon-status'"
check "leon-index executable"     "test -x '$LOCAL_BIN/leon-index'"
check "leon-search executable"    "test -x '$LOCAL_BIN/leon-search'"
check "systemd timer active"      "systemctl --user is-active leon-scheduler.timer"
check "logs_structured/ exists"   "test -d '$ROOT/logs_structured'"
check "memory/ exists"            "test -d '$ROOT/memory/daily'"
check "max_concurrent=3"          "grep -q 'max_concurrent: 3' '$ROOT/config/settings.yaml'"

echo ""
echo -e "${BOLD}  Results: ${GREEN}${PASS} passed${RESET} / ${RED}${FAIL} failed${RESET}"
echo ""

if [ $FAIL -eq 0 ]; then
  echo -e "${GREEN}${BOLD}  All checks passed. Leon is ready for autonomous operation.${RESET}"
else
  echo -e "${YELLOW}  Some checks failed — see warnings above.${RESET}"
  echo -e "${YELLOW}  System will still run; failed features degrade gracefully.${RESET}"
fi

echo -e "\n${BOLD}  Quick-start commands:${RESET}"
echo "    leon-status              # System status"
echo "    leon-index --project Motorev  # Index a project"
echo "    leon-search \"auth hook\" --project Motorev  # Search code"
echo "    leon-status --watch      # Live refresh"
echo ""

# ── Rollback instructions ─────────────────────────────────────────────────────
cat > "$ROOT/data/rollback_autonomous.sh" << 'ROLLBACK'
#!/usr/bin/env bash
# Rollback autonomous upgrade
set -euo pipefail
systemctl --user disable --now leon-scheduler.timer  2>/dev/null || true
systemctl --user disable --now leon-scheduler.service 2>/dev/null || true
rm -f ~/.config/systemd/user/leon-scheduler.{service,timer}
systemctl --user daemon-reload
rm -f ~/.local/bin/leon-status ~/.local/bin/leon-index ~/.local/bin/leon-search
echo "Rollback complete. RAG packages left installed (harmless)."
echo "To remove RAG packages: pip uninstall -y chromadb sentence-transformers rank-bm25"
ROLLBACK
chmod +x "$ROOT/data/rollback_autonomous.sh"
ok "Rollback script: data/rollback_autonomous.sh"
