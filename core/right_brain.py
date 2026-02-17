"""
Right Brain — Worker process that runs on the homelab PC.

Connects to the Left Brain via BridgeClient, receives task dispatches,
spawns agents locally, and reports results back over the bridge.
Agents keep running even if the bridge disconnects.
"""

import asyncio
import logging
import os
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

        # Cached memory context pushed from Left Brain (read-only)
        self._memory_cache: dict = {}

        self.running = False
        self._monitor_task: Optional[asyncio.Task] = None

        logger.info("Right Brain initialized")

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

        # Verify paths exist (they should be on NFS)
        if not Path(brief_path).exists():
            logger.error(f"Brief not found (NFS issue?): {brief_path}")
            await self.bridge.send(BridgeMessage(
                type=MSG_TASK_STATUS,
                id=msg.id,
                payload={
                    "task_id": remote_task_id,
                    "status": "failed",
                    "error": f"Brief path not accessible: {brief_path}",
                },
            ))
            return

        if not Path(project_path).exists():
            logger.error(f"Project path not found (NFS issue?): {project_path}")
            await self.bridge.send(BridgeMessage(
                type=MSG_TASK_STATUS,
                id=msg.id,
                payload={
                    "task_id": remote_task_id,
                    "status": "failed",
                    "error": f"Project path not accessible: {project_path}",
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

                    if status.get("completed"):
                        results = await self.agent_manager.get_agent_results(agent_id)
                        self.task_queue.complete_task(agent_id)
                        self.agent_manager.cleanup_agent(agent_id)

                        # Report back to Left Brain
                        remote_info = self._remote_tasks.pop(agent_id, {})
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
