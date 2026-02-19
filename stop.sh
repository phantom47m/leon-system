#!/bin/bash
# Stop all Leon processes
echo "[stop] Stopping Leon..."
kill $(ss -tlnp sport = :3000 2>/dev/null | grep -oP 'pid=\K\d+') 2>/dev/null && echo "[stop] Leon stopped" || echo "[stop] Leon wasn't running"
kill $(pgrep -f "node bridge.js") 2>/dev/null && echo "[stop] WhatsApp bridge stopped" || echo "[stop] Bridge wasn't running"
kill $(pgrep -f "agent-monitor") 2>/dev/null && echo "[stop] Monitor stopped" || true
echo "[stop] Done"
