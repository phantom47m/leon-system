"""
Right Brain — Worker process that runs on the homelab PC.

Connects to the Left Brain via BridgeClient, receives task dispatches,
spawns agents locally, and reports results back over the bridge.
Agents keep running even if the bridge disconnects.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from .neural_bridge import (
    BridgeClient, BridgeMessage,
    MSG_TASK_DISPATCH, MSG_TASK_STATUS, MSG_TASK_RESULT,
    MSG_STATUS_REQUEST, MSG_STATUS_RESPONSE, MSG_MEMORY_SYNC,
)
from .agent_manager import AgentManager
from .task_queue import TaskQueue
from .openclaw_interface import OpenClawInterface

logger = logging.getLogger("leon.right_brain")

REMOTE_TASKS_FILE = Path("data/remote_tasks.json")
MAX_QUEUED_TASKS = 10


class RightBrain:
    """
    Homelab worker process. Receives tasks from Left Brain,
    spawns agents, monitors them, and sends results back.
    """

    def __init__(self, config_path: str = "config/settings.yaml"):
        logger.info("Initializing Right Brain...")

        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        # Override brain role
        self.config["leon"]["brain_role"] = "right"

        # Core agent subsystems
        self.openclaw = OpenClawInterface(self.config["openclaw"]["config_path"])
        self.agent_manager = AgentManager(self.openclaw, self.config["agents"])
        self.task_queue = TaskQueue(self.config["agents"]["max_concurrent"])

        # Bridge client
        bridge_config = self.config.get("bridge", {})
        self.bridge = BridgeClient(bridge_config)

        # Register message handlers
        self.bridge.on(MSG_TASK_DISPATCH, self._handle_task_dispatch)
        self.bridge.on(MSG_STATUS_REQUEST, self._handle_status_request)
        self.bridge.on(MSG_MEMORY_SYNC, self._handle_memory_sync)

        # Local tracking for tasks dispatched from Left Brain
        self._remote_tasks: dict[str, dict] = {}
        self._load_remote_tasks()

        # Cached memory context pushed from Left Brain (read-only)
        self._memory_cache: dict = {}

        self.running = False
        self._monitor_task: Optional[asyncio.Task] = None

        logger.info("Right Brain initialized")

    # ── Persistence ───────────────────────────────────────

    def _load_remote_tasks(self):
        """Load persisted remote tasks from disk, prune old completed ones."""
        try:
            if REMOTE_TASKS_FILE.exists():
                data = json.loads(REMOTE_TASKS_FILE.read_text())
                now = datetime.now()
                for task_id, task in data.items():
                    # Prune completed tasks older than 24h
                    completed_at = task.get("completed_at")
                    if completed_at:
                        age = (now - datetime.fromisoformat(completed_at)).total_seconds()
                        if age > 86400:
                            continue
                    self._remote_tasks[task_id] = task
                logger.info(f"Loaded {len(self._remote_tasks)} remote tasks from disk")
        except Exception as e:
            logger.warning(f"Could not load remote tasks: {e}")

    def _save_remote_tasks(self):
        """Persist remote tasks dict to disk."""
        try:
            REMOTE_TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
            REMOTE_TASKS_FILE.write_text(json.dumps(self._remote_tasks, default=str, indent=2))
        except Exception as e:
            logger.warning(f"Could not save remote tasks: {e}")

    # ── Lifecycle ─────────────────────────────────────────

    async def start(self):
        """Start the Right Brain: connect to Left Brain + monitor agents."""
        self.running = True
        await self.bridge.start()
        self._monitor_task = asyncio.create_task(self._agent_monitor_loop())
        logger.info("Right Brain running — waiting for tasks from Left Brain")

    async def stop(self):
        """Graceful shutdown."""
        logger.info("Stopping Right Brain...")
        self.running = False
        if self._monitor_task:
            self._monitor_task.cancel()
        await self.bridge.stop()

        # Terminate any running agents
        for agent_id in list(self.agent_manager.active_agents.keys()):
            await self.agent_manager.terminate_agent(agent_id)

        self._save_remote_tasks()
        logger.info("Right Brain stopped")

    # ── Message Handlers ─────────────────────────────────

    async def _handle_task_dispatch(self, msg: BridgeMessage):
        """Receive a task from Left Brain, spawn an agent locally."""
        payload = msg.payload
        brief_path = payload.get("brief_path", "")
        project_path = payload.get("project_path", "")
        task_desc = payload.get("description", "unknown task")
        remote_task_id = payload.get("task_id", msg.id)

        logger.info(f"Received task from Left Brain: {task_desc[:60]}")

        # Backpressure: reject if queue is full
        active_count = len(self.agent_manager.active_agents)
        queued_count = len(self.task_queue.queue)
        if active_count >= self.task_queue.max_concurrent and queued_count >= MAX_QUEUED_TASKS:
            logger.warning(f"Rejecting task — queue full ({active_count} active, {queued_count} queued)")
            await self.bridge.send(BridgeMessage(
                type=MSG_TASK_STATUS,
                id=msg.id,
                payload={
                    "task_id": remote_task_id,
                    "status": "rejected",
                    "reason": "queue_full",
                },
            ))
            return

        # Verify paths exist with NFS retry (mount may be delayed)
        if not await self._wait_for_path(brief_path):
            logger.error(f"Brief not found after retries (NFS issue?): {brief_path}")
            await self.bridge.send(BridgeMessage(
                type=MSG_TASK_STATUS,
                id=msg.id,
                payload={
                    "task_id": remote_task_id,
                    "status": "failed",
                    "error": f"Brief path not accessible after retries: {brief_path}",
                },
            ))
            return

        if not await self._wait_for_path(project_path):
            logger.error(f"Project path not found after retries (NFS issue?): {project_path}")
            await self.bridge.send(BridgeMessage(
                type=MSG_TASK_STATUS,
                id=msg.id,
                payload={
                    "task_id": remote_task_id,
                    "status": "failed",
                    "error": f"Project path not accessible after retries: {project_path}",
                },
            ))
            return

        # Spawn agent
        agent_id = await self.agent_manager.spawn_agent(
            brief_path=brief_path,
            project_path=project_path,
        )

        task_obj = {
            "id": agent_id,
            "description": task_desc,
            "project_name": payload.get("project_name", "unknown"),
            "brief_path": brief_path,
            "remote_task_id": remote_task_id,
        }
        self.task_queue.add_task(agent_id, task_obj)
        self._remote_tasks[agent_id] = task_obj
        self._save_remote_tasks()

        # Acknowledge
        await self.bridge.send(BridgeMessage(
            type=MSG_TASK_STATUS,
            id=msg.id,
            payload={
                "task_id": remote_task_id,
                "agent_id": agent_id,
                "status": "spawned",
            },
        ))
        logger.info(f"Agent {agent_id} spawned for remote task {remote_task_id}")

    async def _wait_for_path(self, path: str, retries: int = 3, delay: float = 2.0) -> bool:
        """Wait for a path to become available (NFS mount delay)."""
        for attempt in range(retries):
            if Path(path).exists():
                return True
            if attempt < retries - 1:
                logger.debug(f"Path not found, retrying in {delay}s: {path}")
                await asyncio.sleep(delay)
        return False

    async def _handle_status_request(self, msg: BridgeMessage):
        """Return current agent + task status to Left Brain."""
        summary = self.task_queue.get_status_summary()
        await self.bridge.send(BridgeMessage(
            type=MSG_STATUS_RESPONSE,
            id=msg.id,
            payload={
                "active_agents": summary["active"],
                "queued": summary["queued"],
                "completed": summary["completed"],
                "active_tasks": [
                    {
                        "agent_id": t.get("agent_id", ""),
                        "description": t.get("description", ""),
                        "project": t.get("project", ""),
                        "started_at": t.get("created_at", ""),
                    }
                    for t in summary.get("active_tasks", [])
                ],
            },
        ))

    async def _handle_memory_sync(self, msg: BridgeMessage):
        """Cache memory context pushed from Left Brain (read-only)."""
        self._memory_cache.update(msg.payload)
        logger.debug(f"Memory sync received — {len(msg.payload)} keys")

    # ── Agent Monitor Loop ───────────────────────────────

    async def _agent_monitor_loop(self):
        """Every 5s, check local agents and report completions/failures to Left Brain."""
        logger.info("Agent monitor loop started")
        while self.running:
            try:
                agent_ids = list(self.agent_manager.active_agents.keys())
                for agent_id in agent_ids:
                    status = await self.agent_manager.check_status(agent_id)

                    # If agent is retrying, update our tracking
                    if status.get("retrying"):
                        new_id = status.get("new_agent_id")
                        if new_id and agent_id in self._remote_tasks:
                            task_info = self._remote_tasks.pop(agent_id)
                            task_info["id"] = new_id
                            self._remote_tasks[new_id] = task_info
                            self._save_remote_tasks()
                        continue

                    if status.get("completed"):
                        results = await self.agent_manager.get_agent_results(agent_id)
                        self.task_queue.complete_task(agent_id)
                        self.agent_manager.cleanup_agent(agent_id)

                        # Report back to Left Brain
                        remote_info = self._remote_tasks.pop(agent_id, {})
                        remote_info["completed_at"] = datetime.now().isoformat()
                        self._save_remote_tasks()

                        await self.bridge.send(BridgeMessage(
                            type=MSG_TASK_RESULT,
                            payload={
                                "task_id": remote_info.get("remote_task_id", agent_id),
                                "agent_id": agent_id,
                                "status": "completed",
                                "results": {
                                    "summary": results.get("summary", ""),
                                    "files_modified": results.get("files_modified", []),
                                    "success": results.get("success", True),
                                    "duration_seconds": results.get("duration_seconds", 0),
                                },
                            },
                        ))
                        logger.info(f"Agent {agent_id} completed — reported to Left Brain")

                    elif status.get("failed"):
                        results = await self.agent_manager.get_agent_results(agent_id)
                        self.task_queue.fail_task(agent_id, results.get("errors", ""))
                        self.agent_manager.cleanup_agent(agent_id)

                        remote_info = self._remote_tasks.pop(agent_id, {})
                        remote_info["completed_at"] = datetime.now().isoformat()
                        self._save_remote_tasks()

                        await self.bridge.send(BridgeMessage(
                            type=MSG_TASK_RESULT,
                            payload={
                                "task_id": remote_info.get("remote_task_id", agent_id),
                                "agent_id": agent_id,
                                "status": "failed",
                                "results": {
                                    "summary": f"FAILED: {results.get('errors', 'unknown')[:200]}",
                                    "files_modified": [],
                                    "success": False,
                                    "duration_seconds": results.get("duration_seconds", 0),
                                },
                            },
                        ))
                        logger.warning(f"Agent {agent_id} failed — reported to Left Brain")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Agent monitor error: {e}")

            await asyncio.sleep(5)

    def get_status(self) -> dict:
        """Local status summary."""
        return {
            "brain_role": "right",
            "bridge_connected": self.bridge.connected,
            "tasks": self.task_queue.get_status_summary(),
            "active_agents": len(self.agent_manager.active_agents),
            "remote_tasks": len(self._remote_tasks),
        }
