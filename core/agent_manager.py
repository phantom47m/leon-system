"""
Leon Agent Manager - Spawns and monitors Claude Code agents via OpenClaw
"""

import asyncio
import logging
import os
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.agents")


class AgentManager:
    """Spawns, monitors, and manages Claude Code agent processes"""

    def __init__(self, openclaw, config: dict):
        self.openclaw = openclaw
        self.config = config
        self.active_agents: dict[str, dict] = {}
        self.output_dir = Path(config.get("output_directory", "data/agent_outputs"))
        self.brief_dir = Path(config.get("brief_directory", "data/task_briefs"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.brief_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = config.get("timeout_minutes", 60) * 60
        self.auto_retry = config.get("auto_retry", True)
        self.retry_attempts = config.get("retry_attempts", 2)
        logger.info("Agent manager initialized")

    async def spawn_agent(self, brief_path: str, project_path: str) -> str:
        """
        Spawn a Claude Code agent to work on a task.

        Args:
            brief_path: Path to the task brief markdown file
            project_path: Working directory for the agent

        Returns:
            agent_id: Unique identifier for tracking this agent
        """
        agent_id = f"agent_{uuid.uuid4().hex[:8]}"
        output_file = self.output_dir / f"{agent_id}.log"
        error_file = self.output_dir / f"{agent_id}.err"

        # Read the full brief content (no truncation)
        brief_content = Path(brief_path).read_text()

        logger.info(f"Spawning agent {agent_id} in {project_path}")

        # Open files properly so we can track and close them
        stdout_fh = open(output_file, "w")
        stderr_fh = open(error_file, "w")

        # Pipe brief via stdin to avoid argument length limits and shell injection
        # Unset CLAUDECODE to allow spawning from within a Claude Code session
        # Point at Leon's own Claude credentials (backup account)
        spawn_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        leon_auth_dir = Path(__file__).parent.parent / "config" / "claude-auth"
        if (leon_auth_dir / ".claude" / ".credentials.json").exists():
            spawn_env["HOME"] = str(leon_auth_dir)
        process = subprocess.Popen(
            ["claude", "--print", "-"],
            stdin=subprocess.PIPE,
            stdout=stdout_fh,
            stderr=stderr_fh,
            cwd=project_path,
            env=spawn_env,
        )

        # Write the full brief to stdin and close it so the process can run
        process.stdin.write(brief_content.encode())
        process.stdin.close()

        self.active_agents[agent_id] = {
            "process": process,
            "pid": process.pid,
            "brief_path": brief_path,
            "project_path": project_path,
            "output_file": str(output_file),
            "error_file": str(error_file),
            "started_at": datetime.now().isoformat(),
            "status": "running",
            "last_check": datetime.now().isoformat(),
            "retries": 0,
            "_file_handles": [stdout_fh, stderr_fh],
        }

        logger.info(f"Agent {agent_id} spawned (PID {process.pid})")
        return agent_id

    async def check_status(self, agent_id: str) -> dict:
        """Check whether an agent is still running, completed, or failed."""
        if agent_id not in self.active_agents:
            return {"error": "Agent not found"}

        agent = self.active_agents[agent_id]
        process: subprocess.Popen = agent["process"]

        is_running = process.poll() is None
        output = self._read_output(agent["output_file"])
        errors = self._read_output(agent["error_file"])

        # Proper completion detection: check return code + output
        completed = (
            (not is_running)
            and (process.returncode == 0)
            and bool(output.strip())
        )
        failed = (
            (not is_running)
            and (process.returncode != 0 or not output.strip())
        )

        # Timeout check
        started = datetime.fromisoformat(agent["started_at"])
        elapsed = (datetime.now() - started).total_seconds()
        if is_running and elapsed > self.timeout:
            logger.warning(f"Agent {agent_id} timed out after {elapsed:.0f}s")
            await self.terminate_agent(agent_id)
            failed = True
            completed = False

        # Auto-retry on failure
        if failed and self.auto_retry and agent["retries"] < self.retry_attempts:
            logger.info(
                f"Agent {agent_id} failed (attempt {agent['retries'] + 1}/{self.retry_attempts}), retrying..."
            )
            new_id = await self._retry_agent(agent_id)
            agent["status"] = "retrying"
            agent["last_check"] = datetime.now().isoformat()
            return {
                "running": False,
                "completed": False,
                "failed": False,
                "retrying": True,
                "new_agent_id": new_id,
                "output_preview": output[-500:] if output else "",
                "errors": errors[-500:] if errors else "",
                "duration_seconds": elapsed,
            }

        agent["status"] = "running" if is_running else ("completed" if completed else "failed")
        agent["last_check"] = datetime.now().isoformat()

        return {
            "running": is_running,
            "completed": completed,
            "failed": failed,
            "retrying": False,
            "output_preview": output[-500:] if output else "",
            "errors": errors[-500:] if errors else "",
            "duration_seconds": elapsed,
        }

    async def _retry_agent(self, agent_id: str) -> str:
        """Re-spawn a failed agent with the same brief and project."""
        agent = self.active_agents[agent_id]
        retry_count = agent["retries"] + 1

        # Close old file handles
        self._close_file_handles(agent_id)

        # Spawn a new agent with the same parameters
        new_id = await self.spawn_agent(
            brief_path=agent["brief_path"],
            project_path=agent["project_path"],
        )

        # Carry over retry count
        self.active_agents[new_id]["retries"] = retry_count

        # Remove old agent from tracking
        self.active_agents.pop(agent_id, None)

        logger.info(f"Retried agent {agent_id} -> {new_id} (attempt {retry_count + 1})")
        return new_id

    async def get_agent_results(self, agent_id: str) -> dict:
        """Extract structured results from a completed agent."""
        if agent_id not in self.active_agents:
            return {"error": "Agent not found"}

        agent = self.active_agents[agent_id]
        output = self._read_output(agent["output_file"])
        errors = self._read_output(agent["error_file"])

        return {
            "summary": self._extract_summary(output),
            "files_modified": self._extract_files(output),
            "success": agent["status"] == "completed",
            "output": output,
            "errors": errors,
            "duration_seconds": (datetime.now() - datetime.fromisoformat(agent["started_at"])).total_seconds(),
        }

    async def terminate_agent(self, agent_id: str):
        """Kill a running agent."""
        if agent_id not in self.active_agents:
            return
        agent = self.active_agents[agent_id]
        process: subprocess.Popen = agent["process"]
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        agent["status"] = "terminated"
        self._close_file_handles(agent_id)
        logger.info(f"Agent {agent_id} terminated")

    def cleanup_agent(self, agent_id: str):
        """Remove agent from active tracking (call after results are collected)."""
        self._close_file_handles(agent_id)
        self.active_agents.pop(agent_id, None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _close_file_handles(self, agent_id: str):
        """Close any open file handles for an agent."""
        agent = self.active_agents.get(agent_id)
        if not agent:
            return
        for fh in agent.pop("_file_handles", []):
            try:
                if not fh.closed:
                    fh.close()
            except Exception:
                pass

    def _read_output(self, filepath: str) -> str:
        try:
            return Path(filepath).read_text()
        except FileNotFoundError:
            return ""

    def _extract_summary(self, output: str) -> str:
        lines = output.strip().split("\n")
        if not lines:
            return "No output"

        # Look for structured markers first
        marker_prefixes = ("summary:", "result:", "done:", "completed:", "output:")
        for line in reversed(lines):
            stripped = line.strip().lower()
            for prefix in marker_prefixes:
                if stripped.startswith(prefix):
                    return line.strip()

        # Fall back to last 5 non-empty lines
        tail = [l for l in lines[-15:] if l.strip()]
        return "\n".join(tail[-5:]) if tail else "Task completed"

    def _extract_files(self, output: str) -> list:
        patterns = [
            r"(?:Modified|Created|Updated|Wrote|Edited):\s*(.+)",
            r"✓\s+(.+\.\w{1,5})",
        ]
        files = []
        for pattern in patterns:
            files.extend(re.findall(pattern, output))
        return list(set(files))
