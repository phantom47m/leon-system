"""
Leon OpenClaw Interface - Safe system status and OpenClaw process management.

Only exposes structured, non-injectable operations.
Agent spawning is handled separately by agent_manager.py via subprocess.Popen.
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.openclaw")


class OpenClawInterface:
    """Safe interface to OpenClaw — no arbitrary shell execution."""

    def __init__(self, config_path: str = "~/.openclaw/openclaw.json"):
        self.config_path = Path(config_path).expanduser()
        logger.info("OpenClaw interface initialized")

    def is_openclaw_running(self) -> bool:
        """Check if OpenClaw gateway is running."""
        try:
            result = subprocess.run(
                ["pgrep", "-f", "openclaw"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except Exception:
            return False

    def start_openclaw(self) -> bool:
        """Attempt to start OpenClaw."""
        try:
            subprocess.Popen(
                ["openclaw", "start"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("OpenClaw started")
            return True
        except Exception as e:
            logger.error(f"Failed to start OpenClaw: {e}")
            return False

    def get_system_status(self) -> dict:
        """Get system resource usage via /proc (no shell execution)."""
        status = {}

        # CPU load average
        try:
            with open("/proc/loadavg", "r") as f:
                parts = f.read().split()
                status["load_1m"] = float(parts[0])
                status["load_5m"] = float(parts[1])
                status["load_15m"] = float(parts[2])
        except (OSError, IndexError, ValueError):
            status["load_1m"] = -1

        # Memory info
        try:
            with open("/proc/meminfo", "r") as f:
                meminfo = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]  # value in kB
                        meminfo[key] = int(val)
                total = meminfo.get("MemTotal", 1)
                available = meminfo.get("MemAvailable", 0)
                status["mem_total_mb"] = total // 1024
                status["mem_available_mb"] = available // 1024
                status["mem_used_pct"] = round((1 - available / total) * 100, 1) if total > 0 else 0
        except (OSError, ValueError):
            status["mem_used_pct"] = -1

        # Disk usage for root partition
        try:
            st = Path("/").stat()
            import os
            disk = os.statvfs("/")
            total = disk.f_blocks * disk.f_frsize
            free = disk.f_bavail * disk.f_frsize
            status["disk_total_gb"] = round(total / (1024 ** 3), 1)
            status["disk_free_gb"] = round(free / (1024 ** 3), 1)
            status["disk_used_pct"] = round((1 - free / total) * 100, 1) if total > 0 else 0
        except OSError:
            status["disk_used_pct"] = -1

        return status
