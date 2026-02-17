#!/bin/bash
# Leon AI Orchestrator - Installation Script
# Run this on Pop!_OS / Ubuntu to set up everything

set -e

echo "╔════════════════════════════════════════════╗"
echo "║    🤖 Leon v2.0 - Installation Script      ║"
echo "║    Voice + Brain Dashboard + Orchestrator   ║"
echo "╚════════════════════════════════════════════╝"
echo

LEON_DIR="$HOME/leon-system"
cd "$LEON_DIR"

# ── 1. System packages ──────────────────────────────────
echo "[1/6] Installing system packages..."
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

# ── 2. Audio packages (for voice) ───────────────────────
echo "[2/6] Installing audio packages for voice system..."
sudo apt install -y -qq \
    portaudio19-dev \
    python3-pyaudio \
    libsndfile1 \
    ffmpeg \
    espeak-ng \
    2>&1 | tail -3

echo "  ✓ Audio packages installed"

# ── 3. Python venv ──────────────────────────────────────
echo "[3/6] Setting up Python environment..."
python3 -m venv venv --system-site-packages
source venv/bin/activate

pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "  ✓ Python environment ready"

# ── 4. Data directories ─────────────────────────────────
echo "[4/6] Creating data directories..."
mkdir -p data/{task_briefs,agent_outputs}
mkdir -p logs

echo "  ✓ Directories created"

# ── 5. Permissions ───────────────────────────────────────
echo "[5/6] Setting permissions..."
chmod +x main.py
chmod +x scripts/*.sh

echo "  ✓ Permissions set"

# ── 6. systemd service ──────────────────────────────────
echo "[6/6] Setting up systemd service..."
mkdir -p "$HOME/.config/systemd/user"

cat > "$HOME/.config/systemd/user/leon.service" << EOF
[Unit]
Description=Leon AI Orchestration System
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$LEON_DIR
Environment="PATH=$LEON_DIR/venv/bin:/usr/local/bin:/usr/bin"
ExecStart=$LEON_DIR/venv/bin/python3 $LEON_DIR/main.py --full
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
echo "  ✓ systemd service created (leon.service)"

echo
echo "╔═══════════════════════════════════════════════════╗"
echo "║           ✅ Leon installation complete!          ║"
echo "╠═══════════════════════════════════════════════════╣"
echo "║                                                   ║"
echo "║  STEP 1 — Set your API keys:                     ║"
echo "║                                                   ║"
echo "║  # Required:                                      ║"
echo "║  echo 'export ANTHROPIC_API_KEY=sk-...'           ║"
echo "║       >> ~/.bashrc                                ║"
echo "║                                                   ║"
echo "║  # For voice (Deepgram STT):                      ║"
echo "║  echo 'export DEEPGRAM_API_KEY=...'               ║"
echo "║       >> ~/.bashrc                                ║"
echo "║                                                   ║"
echo "║  # For voice (ElevenLabs TTS):                    ║"
echo "║  echo 'export ELEVENLABS_API_KEY=...'             ║"
echo "║       >> ~/.bashrc                                ║"
echo "║                                                   ║"
echo "║  source ~/.bashrc                                 ║"
echo "║                                                   ║"
echo "║  STEP 2 — Add your projects:                     ║"
echo "║  nano config/projects.yaml                        ║"
echo "║                                                   ║"
echo "║  STEP 3 — Run Leon:                              ║"
echo "║                                                   ║"
echo "║  # Terminal only:                                  ║"
echo "║  python3 main.py --cli                            ║"
echo "║                                                   ║"
echo "║  # Terminal + brain dashboard:                     ║"
echo "║  python3 main.py --cli --dashboard                ║"
echo "║  # Then open http://localhost:3000                 ║"
echo "║                                                   ║"
echo "║  # Terminal + voice + brain:                       ║"
echo "║  python3 main.py --full                           ║"
echo "║                                                   ║"
echo "║  # GTK4 overlay + voice + brain:                   ║"
echo "║  python3 main.py --gui                            ║"
echo "║                                                   ║"
echo "║  # Auto-start on boot:                            ║"
echo "║  systemctl --user enable leon.service             ║"
echo "║  systemctl --user start leon.service              ║"
echo "║                                                   ║"
echo "║  API Key Signup Links:                            ║"
echo "║  Anthropic: https://console.anthropic.com         ║"
echo "║  Deepgram:  https://console.deepgram.com          ║"
echo "║  ElevenLabs: https://elevenlabs.io                ║"
echo "║                                                   ║"
echo "╚═══════════════════════════════════════════════════╝"
