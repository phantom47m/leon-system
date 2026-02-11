#!/bin/bash
# Leon AI Orchestrator - Installation Script
# Run this on Pop!_OS / Ubuntu to set up everything

set -e

echo "╔════════════════════════════════════════╗"
echo "║    🤖 Leon - Installation Script       ║"
echo "╚════════════════════════════════════════╝"
echo

LEON_DIR="$HOME/leon-system"
cd "$LEON_DIR"

# ---- System packages ----
echo "[1/5] Installing system packages..."
sudo apt update -qq
sudo apt install -y -qq \
    python3 \
    python3-pip \
    python3-venv \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    libgirepository1.0-dev \
    gobject-introspection \
    libgtk-4-dev \
    2>&1 | tail -3

echo "  ✓ System packages installed"

# ---- Python venv ----
echo "[2/5] Setting up Python environment..."
python3 -m venv venv --system-site-packages
source venv/bin/activate

pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "  ✓ Python environment ready"

# ---- Data directories ----
echo "[3/5] Creating data directories..."
mkdir -p data/{task_briefs,agent_outputs}
mkdir -p logs

echo "  ✓ Directories created"

# ---- Make executable ----
echo "[4/5] Setting permissions..."
chmod +x main.py
chmod +x scripts/*.sh

echo "  ✓ Permissions set"

# ---- systemd service ----
echo "[5/5] Setting up systemd service..."
mkdir -p "$HOME/.config/systemd/user"

cat > "$HOME/.config/systemd/user/leon.service" << EOF
[Unit]
Description=Leon AI Orchestration System
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$LEON_DIR
Environment="PATH=$LEON_DIR/venv/bin:/usr/local/bin:/usr/bin"
ExecStart=$LEON_DIR/venv/bin/python3 $LEON_DIR/main.py --cli
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
echo "  ✓ systemd service created (leon.service)"
echo "    To enable auto-start: systemctl --user enable leon.service"
echo "    To start now:         systemctl --user start leon.service"

echo
echo "╔════════════════════════════════════════╗"
echo "║    ✅ Leon installation complete!      ║"
echo "╠════════════════════════════════════════╣"
echo "║                                        ║"
echo "║  To run Leon:                          ║"
echo "║    CLI:  cd ~/leon-system              ║"
echo "║          source venv/bin/activate      ║"
echo "║          python3 main.py --cli         ║"
echo "║                                        ║"
echo "║    GUI:  python3 main.py --gui         ║"
echo "║                                        ║"
echo "║  Add projects to:                      ║"
echo "║    config/projects.yaml                ║"
echo "║                                        ║"
echo "║  Set your Anthropic API key:           ║"
echo "║    export ANTHROPIC_API_KEY=sk-...     ║"
echo "║                                        ║"
echo "╚════════════════════════════════════════╝"
