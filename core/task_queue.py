"""
Leon Task Queue - Manages multiple simultaneous agent tasks
with JSON persistence to survive restarts.
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.tasks")


class TaskQueue:
    """Priority queue for managing concurrent agent tasks with persistence."""

    def __init__(self, max_concurrent: int = 5, persist_path: str = "data/task_queue.json"):
        self.max_concurrent = max_concurrent
        self._persist_path = Path(persist_path)
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)

        # Load persisted state or start fresh
        self.queue: list[dict] = []
        self.active_tasks: dict[str, dict] = {}
        self.completed: list[dict] = []
        self._load()

        logger.info(
            f"Task queue initialized — max concurrent: {max_concurrent}, "
            f"restored {len(self.active_tasks)} active, {len(self.queue)} queued, "
            f"{len(self.completed)} completed"
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self):
        """Load task queue state from disk."""
        if not self._persist_path.exists():
            return
        try:
            with open(self._persist_path, "r") as f:
                data = json.load(f)
            self.queue = data.get("queue", [])
            self.active_tasks = data.get("active_tasks", {})
            self.completed = data.get("completed", [])

            # Tasks that were "active" at shutdown lost their processes — move back to queue
            recovered = []
            for agent_id, task in list(self.active_tasks.items()):
                task["status"] = "queued"
                task.pop("failed_at", None)
                task.pop("failure_reason", None)
                recovered.append(task)
            if recovered:
                self.queue = recovered + self.queue  # re-queue at front so they run first
                logger.info(f"Recovered {len(recovered)} interrupted task(s) → re-queued")
            self.active_tasks = {}

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Corrupt task queue file, starting fresh: {e}")
            self.queue = []
            self.active_tasks = {}
            self.completed = []

    def _save(self):
        """Atomic write: write to tmp then rename."""
        tmp = self._persist_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({
                "queue": self.queue,
                "active_tasks": self.active_tasks,
                "completed": self.completed[-200:],  # keep last 200
            }, f, indent=2, default=str)
        shutil.move(str(tmp), str(self._persist_path))

    # ------------------------------------------------------------------
    # Queue operations
    # ------------------------------------------------------------------

    def add_task(self, agent_id: str, task: dict) -> str:
        """Add a task to the queue or start it immediately if a slot is available."""
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

        self._save()
        return task_entry["id"]

    def complete_task(self, agent_id: str):
        """Mark a task as completed and promote the next queued task."""
        task = self.active_tasks.pop(agent_id, None)
        if task:
            task["status"] = "completed"
            task["completed_at"] = datetime.now().isoformat()
            self.completed.append(task)
            self.completed = self.completed[-200:]  # Cap during runtime too
            logger.info(f"Task completed: {task['description'][:50]}")

        # Promote next queued task
        if self.queue and len(self.active_tasks) < self.max_concurrent:
            next_task = self.queue.pop(0)
            next_task["status"] = "active"
            self.active_tasks[next_task["agent_id"]] = next_task
            logger.info(f"Promoted queued task: {next_task['description'][:50]}")

        self._save()

    def fail_task(self, agent_id: str, reason: str = ""):
        """Mark a task as failed and promote the next queued task."""
        task = self.active_tasks.pop(agent_id, None)
        if task:
            task["status"] = "failed"
            task["failed_at"] = datetime.now().isoformat()
            task["failure_reason"] = reason
            self.completed.append(task)
            self.completed = self.completed[-200:]  # Cap during runtime too
            logger.warning(f"Task failed: {task['description'][:50]} - {reason}")

        # Promote next queued task
        if self.queue and len(self.active_tasks) < self.max_concurrent:
            next_task = self.queue.pop(0)
            next_task["status"] = "active"
            self.active_tasks[next_task["agent_id"]] = next_task
            logger.info(f"Promoted queued task: {next_task['description'][:50]}")

        self._save()

    def get_status_summary(self) -> dict:
        """Return a summary of active, queued, and completed tasks."""
        return {
            "active": len(self.active_tasks),
            "queued": len(self.queue),
            "completed": len(self.completed),
            "max_concurrent": self.max_concurrent,
            "active_tasks": list(self.active_tasks.values()),
            "queued_tasks": list(self.queue),
        }

    def get_task(self, agent_id: str) -> Optional[dict]:
        """Return an active task by agent ID, or None."""
        return self.active_tasks.get(agent_id)
