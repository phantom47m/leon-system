"""
Leon Task Queue - Manages multiple simultaneous agent tasks
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("leon.tasks")


class TaskQueue:
    """Priority queue for managing concurrent agent tasks"""

    def __init__(self, max_concurrent: int = 5):
        self.max_concurrent = max_concurrent
        self.queue: list[dict] = []
        self.active_tasks: dict[str, dict] = {}
        self.completed: list[dict] = []
        logger.info(f"Task queue initialized - max concurrent: {max_concurrent}")

    def add_task(self, agent_id: str, task: dict) -> str:
        task_entry = {
            "id": task.get("id", agent_id),
            "agent_id": agent_id,
            "description": task["description"],
            "project": task.get("project_name", "unknown"),
            "priority": task.get("priority", 1),
            "status": "queued",
            "created_at": datetime.now().isoformat(),
            "dependencies": task.get("dependencies", []),
        }

        if len(self.active_tasks) < self.max_concurrent:
            self.active_tasks[agent_id] = task_entry
            task_entry["status"] = "active"
            logger.info(f"Task started immediately: {task_entry['description'][:50]}")
        else:
            self.queue.append(task_entry)
            logger.info(f"Task queued (slot full): {task_entry['description'][:50]}")

        return task_entry["id"]

    def complete_task(self, agent_id: str):
        task = self.active_tasks.pop(agent_id, None)
        if task:
            task["status"] = "completed"
            task["completed_at"] = datetime.now().isoformat()
            self.completed.append(task)
            logger.info(f"Task completed: {task['description'][:50]}")

        # Promote next queued task
        if self.queue and len(self.active_tasks) < self.max_concurrent:
            next_task = self.queue.pop(0)
            next_task["status"] = "active"
            self.active_tasks[next_task["agent_id"]] = next_task
            logger.info(f"Promoted queued task: {next_task['description'][:50]}")

    def fail_task(self, agent_id: str, reason: str = ""):
        task = self.active_tasks.pop(agent_id, None)
        if task:
            task["status"] = "failed"
            task["failed_at"] = datetime.now().isoformat()
            task["failure_reason"] = reason
            self.completed.append(task)
            logger.warning(f"Task failed: {task['description'][:50]} - {reason}")

    def get_status_summary(self) -> dict:
        return {
            "active": len(self.active_tasks),
            "queued": len(self.queue),
            "completed": len(self.completed),
            "active_tasks": list(self.active_tasks.values()),
            "queued_tasks": list(self.queue),
        }

    def get_task(self, agent_id: str) -> Optional[dict]:
        return self.active_tasks.get(agent_id)
