#!/bin/bash
# ═══════════════════════════════════════════════════════════
# Leon Left Brain Setup — Run on MAIN PC (192.168.68.81)
# Run with: sudo bash scripts/setup-left-brain.sh
# ═══════════════════════════════════════════════════════════
set -e

echo "═══ Leon Left Brain Setup ═══"
echo ""

# ── Barrier (shared keyboard/mouse server) ──
echo "[1/3] Installing Barrier..."
apt install -y barrier
echo "  ✓ Barrier installed — open it from app menu, set as SERVER"
echo "    Add screen 'pop-os' and position it relative to this monitor"
echo ""

# ── NFS Server ──
echo "[2/3] Setting up NFS server..."
apt install -y nfs-kernel-server

# Export project directories
EXPORTS_LINE="/home/deansabr 192.168.68.77(rw,sync,no_subtree_check,no_root_squash)"
if ! grep -qF "/home/deansabr" /etc/exports 2>/dev/null; then
    echo "$EXPORTS_LINE" >> /etc/exports
    echo "  Added /home/deansabr to /etc/exports"
else
    echo "  /home/deansabr already in /etc/exports"
fi

exportfs -ra
systemctl enable --now nfs-kernel-server
echo "  ✓ NFS server running — exporting /home/deansabr"
echo ""

# ── Firewall (allow NFS + bridge from homelab) ──
echo "[3/3] Configuring firewall..."
if command -v ufw &>/dev/null; then
    ufw allow from 192.168.68.77 to any port 2049 comment "NFS for homelab"
    ufw allow from 192.168.68.77 to any port 9100 comment "Leon bridge"
    ufw allow from 192.168.68.77 to any port 24800 comment "Barrier"
    echo "  ✓ Firewall rules added"
else
    echo "  No ufw found — skipping (ports 2049, 9100, 24800 need to be open)"
fi

echo ""
echo "═══ Left Brain setup complete ═══"
echo "Next: run setup-right-brain.sh on the homelab"
