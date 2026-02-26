#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
#  Agent Zero — Docker setup for Leon integration
#  Run ONCE with:  bash scripts/setup-agent-zero.sh
#  Requires: sudo (for Docker install) + ANTHROPIC_API_KEY in env
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

BOLD=$(tput bold 2>/dev/null || true)
RESET=$(tput sgr0 2>/dev/null || true)
GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
err()  { echo -e "  ${RED}✗${NC} $1"; }

WORKSPACE="/home/deansabr/agent-zero-workspace"
COMPOSE_DIR="/home/deansabr/agent-zero-docker"
LEON_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AZ_PORT=50080

echo "${BOLD}═══════════════════════════════════════════════${RESET}"
echo "${BOLD}  Agent Zero — Docker Setup${RESET}"
echo "${BOLD}═══════════════════════════════════════════════${RESET}"
echo ""

# ── 1. Install Docker ────────────────────────────────────────────
echo "${BOLD}1. Docker${RESET}"
if command -v docker &>/dev/null; then
    pass "Docker already installed ($(docker --version | cut -d' ' -f3 | tr -d ','))"
else
    echo "  Installing Docker via official script..."
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker "$USER"
    pass "Docker installed — you may need to log out/in for group membership"
fi

if ! docker info &>/dev/null 2>&1; then
    warn "Docker daemon not running — starting..."
    sudo systemctl enable --now docker
fi

# ── 2. Workspace directory (safe, not home) ───────────────────────
echo ""
echo "${BOLD}2. Workspace directory${RESET}"
mkdir -p "$WORKSPACE/jobs"
chmod 755 "$WORKSPACE"
pass "Workspace: $WORKSPACE"

# ── 3. Docker Compose file ────────────────────────────────────────
echo ""
echo "${BOLD}3. Docker Compose${RESET}"
mkdir -p "$COMPOSE_DIR"

# Load API key from Leon's vault or env
AZ_ADMIN_PASS=$(python3 -c "
import secrets, string
chars = string.ascii_letters + string.digits
print(''.join(secrets.choice(chars) for _ in range(20)))
")

cat > "$COMPOSE_DIR/docker-compose.yml" << COMPOSE
version: "3.8"

services:
  agent-zero:
    image: frdel/agent-zero-run:latest
    container_name: agent-zero
    ports:
      - "127.0.0.1:${AZ_PORT}:80"   # localhost only — never exposed externally
    volumes:
      - ${WORKSPACE}:/a0/work_dir    # workspace ONLY — no home dir access
    environment:
      - ANTHROPIC_API_KEY=\${ANTHROPIC_API_KEY}
      - A0_ADMIN_PASSWORD=${AZ_ADMIN_PASS}
      - A0_ALLOW_NONE=false
    restart: unless-stopped
    mem_limit: 4g          # 4 GB cap — leaves 12 GB for Leon + system
    cpus: "2.0"            # 2 cores — leaves 6 cores for host
    security_opt:
      - no-new-privileges:true
    read_only: false        # Agent needs write access to /a0/work_dir
    tmpfs:
      - /tmp:size=512m      # Temp storage in RAM
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "3"
COMPOSE

pass "docker-compose.yml written → $COMPOSE_DIR/"
echo "  Admin password saved → $COMPOSE_DIR/.az_admin_pass"
echo "$AZ_ADMIN_PASS" > "$COMPOSE_DIR/.az_admin_pass"
chmod 600 "$COMPOSE_DIR/.az_admin_pass"

# ── 4. Pull image ─────────────────────────────────────────────────
echo ""
echo "${BOLD}4. Pull Agent Zero image${RESET}"
echo "  Pulling frdel/agent-zero-run:latest (may take a few minutes)..."
if docker pull frdel/agent-zero-run:latest 2>&1 | tail -3; then
    pass "Image pulled"
else
    warn "Pull failed — will retry when 'docker compose up' is run"
fi

# ── 5. Enable agent_zero in Leon settings ─────────────────────────
echo ""
echo "${BOLD}5. Leon settings${RESET}"
SETTINGS="$LEON_ROOT/config/settings.yaml"
if grep -q "^agent_zero:" "$SETTINGS" 2>/dev/null; then
    # Flip enabled to true
    sed -i '/^agent_zero:/,/^[a-z]/ s/enabled: false/enabled: true/' "$SETTINGS"
    pass "agent_zero.enabled set to true in settings.yaml"
else
    warn "agent_zero section not found in settings.yaml — was Leon upgraded correctly?"
fi

# ── 6. Create .env for docker-compose ────────────────────────────
echo ""
echo "${BOLD}6. Environment file${RESET}"
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}" > "$COMPOSE_DIR/.env"
    pass ".env written (key from environment)"
else
    # Try to read from Leon vault
    AK=$(python3 -c "
import sys, os
sys.path.insert(0, '${LEON_ROOT}')
try:
    from security.vault import SecureVault
    v = SecureVault()
    print(v.get('ANTHROPIC_API_KEY') or '')
except Exception:
    print('')
" 2>/dev/null)
    if [ -n "$AK" ]; then
        echo "ANTHROPIC_API_KEY=${AK}" > "$COMPOSE_DIR/.env"
        pass ".env written (key from Leon vault)"
    else
        cat > "$COMPOSE_DIR/.env" << 'ENV'
# Add your Anthropic API key here:
ANTHROPIC_API_KEY=sk-ant-...
ENV
        warn ".env needs ANTHROPIC_API_KEY — edit $COMPOSE_DIR/.env"
    fi
fi
chmod 600 "$COMPOSE_DIR/.env"

# ── 7. Start container ────────────────────────────────────────────
echo ""
echo "${BOLD}7. Start Agent Zero${RESET}"
cd "$COMPOSE_DIR"
if docker compose up -d 2>&1 | tail -5; then
    sleep 5
    if curl -sf "http://localhost:${AZ_PORT}/" -o /dev/null 2>/dev/null; then
        pass "Agent Zero running → http://localhost:${AZ_PORT}/"
    else
        warn "Container started but HTTP not responding yet (give it ~30s)"
    fi
else
    err "Container failed to start — check: docker compose logs agent-zero"
fi

# ── 8. CLI helper ─────────────────────────────────────────────────
echo ""
echo "${BOLD}8. CLI helpers${RESET}"
cat > "$HOME/.local/bin/agent-zero-start" << SCRIPT
#!/bin/bash
cd "${COMPOSE_DIR}" && docker compose up -d
echo "Agent Zero → http://localhost:${AZ_PORT}/"
SCRIPT
cat > "$HOME/.local/bin/agent-zero-stop" << SCRIPT
#!/bin/bash
cd "${COMPOSE_DIR}" && docker compose down
echo "Agent Zero stopped."
SCRIPT
cat > "$HOME/.local/bin/agent-zero-kill" << SCRIPT
#!/bin/bash
JOB_ID="${1:-}"
if [ -n "\$JOB_ID" ]; then
    echo "Killing job \$JOB_ID..."
    docker stop agent-zero 2>/dev/null && docker start agent-zero 2>/dev/null
    echo "Done."
else
    docker kill agent-zero 2>/dev/null || true
    echo "Agent Zero hard-killed."
fi
SCRIPT
chmod +x "$HOME/.local/bin/agent-zero-start" "$HOME/.local/bin/agent-zero-stop" "$HOME/.local/bin/agent-zero-kill"
pass "agent-zero-start / agent-zero-stop / agent-zero-kill → ~/.local/bin/"

# ── 9. Validation ─────────────────────────────────────────────────
echo ""
echo "${BOLD}9. Validation${RESET}"
PASS=0; FAIL=0
chk() { local n="$1"; local c="$2"
    if eval "$c" &>/dev/null 2>&1; then ((PASS++)) || true; echo -e "  ${GREEN}✓${NC} $n"
    else ((FAIL++)) || true; echo -e "  ${RED}✗${NC} $n"; fi
}
chk "Docker running"          "docker info"
chk "agent-zero container"    "docker ps | grep -q agent-zero"
chk "Workspace exists"        "test -d '$WORKSPACE/jobs'"
chk ".env has API key"        "grep -q 'sk-ant' '$COMPOSE_DIR/.env'"
chk "CLI tools installed"     "test -x '$HOME/.local/bin/agent-zero-start'"
chk "Leon settings updated"   "grep -A5 '^agent_zero:' '$SETTINGS' | grep -q 'enabled: true'"

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}All checks passed!${RESET}"
else
    echo -e "${YELLOW}${BOLD}${PASS} passed / ${FAIL} failed — see warnings above${RESET}"
fi

echo ""
echo "════════════════════════════════════════════"
echo " Agent Zero UI:   http://localhost:${AZ_PORT}/"
echo " Workspace:       $WORKSPACE"
echo " Compose dir:     $COMPOSE_DIR"
echo " Kill switch:     agent-zero-kill [job_id]"
echo " Restart Leon:    bash ${LEON_ROOT}/start.sh"
echo "════════════════════════════════════════════"
echo ""
echo "  Next: restart Leon to activate routing →"
echo "  bash ${LEON_ROOT}/start.sh"
