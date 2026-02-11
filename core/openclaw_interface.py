"""
Leon OpenClaw Interface - Bridge to OpenClaw for system automation
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.openclaw")


class OpenClawInterface:
    """Interface to OpenClaw for system-level automation and agent management"""

    def __init__(self, config_path: str = "~/.openclaw/openclaw.json"):
        self.config_path = Path(os.path.expanduser(config_path))
        self.config = self._load_config()
        self.gateway_port = self.config.get("gateway", {}).get("port", 18789)
        logger.info(f"OpenClaw interface initialized (port {self.gateway_port})")

    def _load_config(self) -> dict:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load OpenClaw config: {e}")
        return {}

    async def execute_command(self, command: str, cwd: str = None) -> subprocess.Popen:
        """Execute a shell command, return the process for monitoring."""
        logger.debug(f"Executing: {command[:80]}...")
        process = subprocess.Popen(
            ["bash", "-c", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )
        return process

    async def run_and_wait(self, command: str, cwd: str = None, timeout: int = 120) -> dict:
        """Execute command and wait for completion."""
        process = await self.execute_command(command, cwd=cwd)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return {
                "returncode": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "success": process.returncode == 0,
            }
        except subprocess.TimeoutExpired:
            process.kill()
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": "Command timed out",
                "success": False,
            }

    async def check_process_running(self, process: subprocess.Popen) -> bool:
        return process.poll() is None

    async def kill_process(self, process: subprocess.Popen):
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

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
