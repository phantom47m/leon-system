"""
Leon Memory System - Persistent context across all sessions
"""

import json
import os
import uuid
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.memory")


class MemorySystem:
    """Persistent memory for Leon - survives restarts, maintains project context"""

    def __init__(self, memory_file: str = "data/leon_memory.json"):
        self.memory_file = Path(memory_file)
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self.memory = self._load()
        logger.info(f"Memory loaded: {len(self.memory.get('ongoing_projects', {}))} projects tracked")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self.memory_file.exists():
            try:
                with open(self.memory_file, "r") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                logger.warning("Corrupt memory file, starting fresh")
        return self._empty()

    def save(self):
        # Atomic write: write to tmp then rename
        tmp = self.memory_file.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self.memory, f, indent=2, default=str)
        shutil.move(str(tmp), str(self.memory_file))

    # alias
    save_memory = save

    def _empty(self) -> dict:
        return {
            "identity": {
                "name": "Leon",
                "version": "1.0",
                "created": datetime.now().isoformat(),
            },
            "ongoing_projects": {},
            "completed_tasks": [],
            "active_tasks": {},
            "user_preferences": {
                "coding_style": "clean, well-commented",
                "notification_level": "important_only",
            },
            "conversation_history": [],
            "learned_context": {},
        }

    # ------------------------------------------------------------------
    # Conversation
    # ------------------------------------------------------------------

    def add_conversation(self, content: str, role: str = "user"):
        entry = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        self.memory.setdefault("conversation_history", []).append(entry)
        # Keep last 200 messages
        self.memory["conversation_history"] = self.memory["conversation_history"][-200:]
        self.save()

    def get_recent_context(self, limit: int = 20) -> list:
        history = self.memory.get("conversation_history", [])
        return history[-limit:]

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def add_project(self, name: str, path: str, tech_stack: list = None) -> str:
        project_id = uuid.uuid4().hex[:12]
        self.memory["ongoing_projects"][project_id] = {
            "name": name,
            "path": path,
            "status": "active",
            "active_agents": [],
            "context": {
                "current_task": None,
                "last_activity": datetime.now().isoformat(),
                "tech_stack": tech_stack or [],
                "recent_changes": [],
            },
        }
        self.save()
        logger.info(f"Added project: {name} ({project_id})")
        return project_id

    def get_project_context(self, project_name: str) -> Optional[dict]:
        pid = self._find_project_id(project_name)
        if pid:
            return self.memory["ongoing_projects"][pid]
        return None

    def list_projects(self) -> list:
        return [
            {"id": pid, **proj}
            for pid, proj in self.memory.get("ongoing_projects", {}).items()
        ]

    def _find_project_id(self, name: str) -> Optional[str]:
        name_lower = name.lower()
        for pid, proj in self.memory.get("ongoing_projects", {}).items():
            if proj["name"].lower() == name_lower:
                return pid
        return None

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def add_active_task(self, agent_id: str, task: dict):
        self.memory["active_tasks"][agent_id] = {
            "task_id": task.get("id", agent_id),
            "description": task["description"],
            "started_at": datetime.now().isoformat(),
            "project": task.get("project_name", "unknown"),
            "status": "running",
            "brief_path": task.get("brief_path", ""),
        }
        # Update project's active agents
        pid = self._find_project_id(task.get("project_name", ""))
        if pid:
            agents = self.memory["ongoing_projects"][pid].setdefault("active_agents", [])
            if agent_id not in agents:
                agents.append(agent_id)
        self.save()

    def complete_task(self, agent_id: str, results: dict):
        task = self.memory["active_tasks"].pop(agent_id, None)
        if not task:
            return

        self.memory["completed_tasks"].append(
            {
                "task_id": task["task_id"],
                "description": task["description"],
                "completed_at": datetime.now().isoformat(),
                "agent_id": agent_id,
                "project": task["project"],
                "result_summary": results.get("summary", "Completed"),
                "files_modified": results.get("files_modified", []),
            }
        )

        pid = self._find_project_id(task["project"])
        if pid:
            proj = self.memory["ongoing_projects"][pid]
            if agent_id in proj.get("active_agents", []):
                proj["active_agents"].remove(agent_id)
            proj["context"]["last_activity"] = datetime.now().isoformat()
            proj["context"]["recent_changes"].append(results.get("summary", "Completed"))
            # Keep recent_changes trimmed
            proj["context"]["recent_changes"] = proj["context"]["recent_changes"][-20:]

        self.save()
        logger.info(f"Task completed: {task['description'][:60]}")

    def get_all_active_tasks(self) -> dict:
        return dict(self.memory.get("active_tasks", {}))

    # ------------------------------------------------------------------
    # User preferences
    # ------------------------------------------------------------------

    def set_preference(self, key: str, value):
        self.memory.setdefault("user_preferences", {})[key] = value
        self.save()

    def get_preference(self, key: str, default=None):
        return self.memory.get("user_preferences", {}).get(key, default)

    # ------------------------------------------------------------------
    # Learned context
    # ------------------------------------------------------------------

    def learn(self, key: str, value):
        self.memory.setdefault("learned_context", {})[key] = value
        self.save()
