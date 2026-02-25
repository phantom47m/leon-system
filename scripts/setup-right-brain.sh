#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Leon Right Brain Setup — Run on HOMELAB (192.168.68.77)
# Run with: sudo bash /home/homelab/leon-system-local/scripts/setup-right-brain.sh
# ═══════════════════════════════════════════════════════════
set -e

LEFT_BRAIN_IP="192.168.68.81"
LOCAL_USER="homelab"

echo "═══ Leon Right Brain Setup ═══"
echo ""

# ── Python deps ──
echo "[1/5] Installing Python dependencies..."
apt install -y python3-pip python3-aiohttp python3-yaml
echo "  ✓ Python deps installed"
echo ""

# ── Barrier (shared keyboard/mouse client) ──
echo "[2/5] Installing Barrier..."
apt install -y barrier
echo "  ✓ Barrier installed — open it from app menu, set as CLIENT"
echo "    Enter server IP: $LEFT_BRAIN_IP"
echo ""

# ── NFS Client ──
echo "[3/5] Setting up NFS mount..."
apt install -y nfs-common

# Create mount point matching Left Brain paths
mkdir -p /home/deansabr

# Add fstab entry
FSTAB_LINE="$LEFT_BRAIN_IP:/home/deansabr /home/deansabr nfs soft,intr,timeo=30,retry=0 0 0"
if ! grep -qF "/home/deansabr" /etc/fstab 2>/dev/null; then
    echo "$FSTAB_LINE" >> /etc/fstab
    echo "  Added NFS mount to /etc/fstab"
else
    echo "  NFS mount already in /etc/fstab"
fi

mount -a 2>/dev/null || echo "  ⚠ Mount failed — Left Brain NFS server may not be running yet"

# Verify
if mountpoint -q /home/deansabr 2>/dev/null; then
    echo "  ✓ NFS mounted — /home/deansabr accessible"
else
    echo "  ⚠ NFS not mounted yet — will work after Left Brain setup runs"
fi
echo ""

# ── Claude Code check ──
echo "[4/5] Checking for Claude Code..."
if command -v claude &>/dev/null; then
    echo "  ✓ Claude Code found: $(which claude)"
else
    echo "  ⚠ Claude Code not found — install it:"
    echo "    npm install -g @anthropic-ai/claude-code"
fi
echo ""

# ── Systemd Service ──
echo "[5/5] Creating Right Brain systemd service..."
cat > /etc/systemd/system/leon-right-brain.service << 'EOF'
[Unit]
Description=Leon Right Brain — Agent Worker
After=network-online.target remote-fs.target
Wants=network-online.target
RequiresMountsFor=/home/deansabr

[Service]
Type=simple
User=homelab
WorkingDirectory=/home/homelab/leon-system-local
Environment=LEON_BRAIN_ROLE=right
Environment=LEON_BRIDGE_TOKEN=01d12989921d1baa07e91567d71c6175e005b122638c98ce41038be78b19b702
ExecStart=/usr/bin/python3 main.py --right-brain
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable leon-right-brain.service
echo "  ✓ Service created and enabled"
echo "    Start it with: sudo systemctl start leon-right-brain"
echo "    View logs with: journalctl -u leon-right-brain -f"

echo ""
echo "═══ Right Brain setup complete ═══"
echo ""
echo "Start order:"
echo "  1. Main PC:  python3 main.py --left-brain"
echo "  2. Homelab:  sudo systemctl start leon-right-brain"
echo "  (or just reboot the homelab — it starts automatically)"
