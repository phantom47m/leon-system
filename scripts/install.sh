#!/bin/bash
# Leon AI Orchestrator — Installer
# Supports Ubuntu / Pop!_OS / Debian-based distros
#
# Usage:
#   bash scripts/install.sh          # standard install
#   bash scripts/install.sh --no-gui # skip GTK4 packages (headless/server)

set -e

LEON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$LEON_DIR"

NO_GUI=false
for arg in "$@"; do
    [[ "$arg" == "--no-gui" ]] && NO_GUI=true
done

# ── Read version ─────────────────────────────────────────
VERSION=$(cat VERSION 2>/dev/null || echo "dev")

echo "╔══════════════════════════════════════════════════╗"
echo "║        Leon AI Orchestrator v$VERSION              "
echo "║        Installation                               "
echo "╚══════════════════════════════════════════════════╝"
echo

# ── 1. System packages ───────────────────────────────────
echo "[1/6] Installing system packages..."
sudo apt-get update -qq

PKGS=(
    python3
    python3-pip
    python3-venv
    portaudio19-dev   # PyAudio (voice I/O)
    libsndfile1       # soundfile
    ffmpeg            # audio processing
    espeak-ng         # local TTS fallback
    nodejs            # WhatsApp bridge
    npm
)

if [ "$NO_GUI" = false ]; then
    PKGS+=(
        python3-gi
        python3-gi-cairo
        gir1.2-gtk-4.0
        gir1.2-adw-1
        libgirepository1.0-dev
        gobject-introspection
        libgtk-4-dev
    )
fi

sudo apt-get install -y -qq "${PKGS[@]}" 2>&1 | tail -3
echo "  ✓ System packages installed"

# ── 2. Python venv ───────────────────────────────────────
echo "[2/6] Setting up Python environment..."
python3 -m venv venv --system-site-packages
source venv/bin/activate

pip install --upgrade pip -q

# Install without GTK binding if headless
if [ "$NO_GUI" = true ]; then
    grep -v "PyGObject" requirements.txt | pip install -r /dev/stdin -q
else
    pip install -r requirements.txt -q
fi

echo "  ✓ Python environment ready (venv/)"

# ── 3. Node.js dependencies (WhatsApp bridge) ────────────
echo "[3/6] Installing WhatsApp bridge dependencies..."
if command -v node &>/dev/null; then
    cd integrations/whatsapp
    npm install --silent
    cd "$LEON_DIR"
    echo "  ✓ WhatsApp bridge ready"
else
    echo "  ⚠ Node.js not found — WhatsApp bridge skipped"
fi

# ── Discord bot dependencies ─────────────────────────────
pip install discord.py aiohttp -q && echo "  ✓ Discord bot dependencies ready"
chmod +x integrations/discord/start.sh

# ── 4. Data directories & gitkeep placeholders ───────────
echo "[4/6] Creating runtime directories..."
mkdir -p data/task_briefs data/agent_outputs logs
touch data/task_briefs/.gitkeep data/agent_outputs/.gitkeep logs/.gitkeep
echo "  ✓ Directories created"

# ── 5. Permissions ───────────────────────────────────────
echo "[5/6] Setting file permissions..."
chmod +x main.py start.sh stop.sh scripts/*.sh
echo "  ✓ Permissions set"

# ── 6. systemd user service ──────────────────────────────
echo "[6/6] Installing systemd service..."
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"

cat > "$SYSTEMD_DIR/leon.service" << EOF
[Unit]
Description=Leon AI Orchestrator
After=graphical-session.target network-online.target

[Service]
Type=simple
WorkingDirectory=$LEON_DIR
Environment="PATH=$LEON_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$LEON_DIR/venv/bin/python3 $LEON_DIR/main.py --full
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
echo "  ✓ systemd service installed (leon.service)"

# ── Done ─────────────────────────────────────────────────
echo
echo "╔══════════════════════════════════════════════════╗"
echo "║              Installation complete!              ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║                                                  ║"
echo "║  Start Leon:                                     ║"
echo "║    bash start.sh                                 ║"
echo "║                                                  ║"
echo "║  Then open http://localhost:3000                 ║"
echo "║  The setup wizard will guide you from there.     ║"
echo "║                                                  ║"
echo "║  Auto-start on login:                            ║"
echo "║    systemctl --user enable leon.service          ║"
echo "║    systemctl --user start leon.service           ║"
echo "║                                                  ║"
echo "║  API key links:                                  ║"
echo "║    Groq (free):   https://console.groq.com       ║"
echo "║    ElevenLabs:    https://elevenlabs.io           ║"
echo "║    Anthropic:     https://console.anthropic.com  ║"
echo "║                                                  ║"
echo "╚══════════════════════════════════════════════════╝"
