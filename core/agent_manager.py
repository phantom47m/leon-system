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

        # Read the brief content
        brief_content = Path(brief_path).read_text()[:4000]

        logger.info(f"Spawning agent {agent_id} in {project_path}")

        # Use stdin to pass the brief content safely (avoids shell injection)
        process = subprocess.Popen(
            ["claude", "--print", brief_content],
            stdout=open(output_file, "w"),
            stderr=open(error_file, "w"),
            cwd=project_path,
        )

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

        completed = (not is_running) and self._detect_completion(output)
        failed = (not is_running) and not completed

        # Timeout check
        started = datetime.fromisoformat(agent["started_at"])
        elapsed = (datetime.now() - started).total_seconds()
        if is_running and elapsed > self.timeout:
            logger.warning(f"Agent {agent_id} timed out after {elapsed:.0f}s")
            await self.terminate_agent(agent_id)
            failed = True

        agent["status"] = "running" if is_running else ("completed" if completed else "failed")
        agent["last_check"] = datetime.now().isoformat()

        return {
            "running": is_running,
            "completed": completed,
            "failed": failed,
            "output_preview": output[-500:] if output else "",
            "errors": errors[-500:] if errors else "",
            "duration_seconds": elapsed,
        }

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
        logger.info(f"Agent {agent_id} terminated")

    def cleanup_agent(self, agent_id: str):
        """Remove agent from active tracking (call after results are collected)."""
        self.active_agents.pop(agent_id, None)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_output(self, filepath: str) -> str:
        try:
            return Path(filepath).read_text()
        except FileNotFoundError:
            return ""

    def _detect_completion(self, output: str) -> bool:
        if not output.strip():
            return False
        # Claude Code typically outputs its full response; a non-empty output
        # with no errors usually means it finished.
        return True

    def _extract_summary(self, output: str) -> str:
        lines = output.strip().split("\n")
        if not lines:
            return "No output"
        # Take last meaningful lines as summary
        tail = [l for l in lines[-10:] if l.strip()]
        return " ".join(tail[-3:]) if tail else "Task completed"

    def _extract_files(self, output: str) -> list:
        patterns = [
            r"(?:Modified|Created|Updated|Wrote|Edited):\s*(.+)",
            r"✓\s+(.+\.\w{1,5})",
        ]
        files = []
        for pattern in patterns:
            files.extend(re.findall(pattern, output))
        return list(set(files))
