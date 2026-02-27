"""
Leon Memory System - Persistent context across all sessions
"""

import json
import os
import time
import uuid
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.memory")

_SAVE_DEBOUNCE_SECONDS = 5  # Minimum interval between disk writes


class MemorySystem:
    """Persistent memory for Leon - survives restarts, maintains project context"""

    def __init__(self, memory_file: str = "data/leon_memory.json"):
        self.memory_file = Path(memory_file)
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self._dirty = False
        self._last_save_time = 0.0
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

    def save(self, force: bool = False):
        """Persist memory state to disk with debouncing.

        Writes are debounced to at most once every _SAVE_DEBOUNCE_SECONDS
        to avoid excessive I/O on high-frequency updates. Use force=True
        to bypass debouncing (e.g., on shutdown).
        """
        now = time.monotonic()
        if not force and (now - self._last_save_time) < _SAVE_DEBOUNCE_SECONDS:
            self._dirty = True
            return
        self._flush()

    def _flush(self):
        """Immediately write memory to disk (atomic write)."""
        # Trim completed_tasks to prevent unbounded growth
        if "completed_tasks" in self.memory:
            ct = self.memory["completed_tasks"]
            if isinstance(ct, dict):
                # Migrate legacy dict → list (older Agent Zero sessions stored as dict)
                ct = list(ct.values())
            self.memory["completed_tasks"] = ct[-500:]
        tmp = self.memory_file.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self.memory, f, indent=2, default=str)
        shutil.move(str(tmp), str(self.memory_file))
        self._dirty = False
        self._last_save_time = time.monotonic()

    def flush_if_dirty(self):
        """Flush pending changes to disk. Call on shutdown."""
        if self._dirty:
            self._flush()

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
        """Return the last `limit` conversation messages."""
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
        """Return a list of all tracked projects."""
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

    def get_active_task(self, agent_id: str) -> Optional[dict]:
        """Return the active task for the given agent, or None."""
        return self.memory["active_tasks"].get(agent_id)

    def remove_active_task(self, agent_id: str):
        """Remove an active task by agent ID."""
        self.memory["active_tasks"].pop(agent_id, None)

    def update_active_task(self, agent_id: str, task: dict):
        """Merge updates into an existing active task."""
        if agent_id in self.memory["active_tasks"]:
            self.memory["active_tasks"][agent_id].update(task)

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
        """Return a copy of all active tasks."""
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
        """Store a learned context key-value pair."""
        self.memory.setdefault("learned_context", {})[key] = value
        self.save()

    # ------------------------------------------------------------------
    # Autonomy: memory compaction + daily archive
    # ------------------------------------------------------------------

    CONVERSATION_HARD_LIMIT = 200   # lines before compaction triggers
    COMPACTION_TARGET       = 40    # keep last N messages after compaction

    def memory_update(self, summary: str, source: str = "agent"):
        """
        Store a compact summary artifact in long-term memory.
        Called at end of each Plan/REFLECT step.
        Also writes memory/long_term.md for human review.
        """
        entry = {
            "ts":      datetime.now().isoformat(),
            "source":  source,
            "summary": summary[:500],
        }
        self.memory.setdefault("memory_updates", []).append(entry)
        # Keep last 100 summaries
        self.memory["memory_updates"] = self.memory["memory_updates"][-100:]
        self.save()

        # Write human-readable long_term.md
        lt_path = Path("memory/long_term.md")
        lt_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = lt_path.read_text() if lt_path.exists() else "# Long-Term Memory\n\n"
            ts_short = datetime.now().strftime("%Y-%m-%d %H:%M")
            lt_path.write_text(
                existing + f"\n## [{ts_short}] {source}\n{summary[:500]}\n"
            )
        except Exception as e:
            logger.warning(f"Could not write long_term.md: {e}")

    def compact(self) -> bool:
        """
        Compress conversation history if it exceeds CONVERSATION_HARD_LIMIT.
        Keeps the last COMPACTION_TARGET messages; older ones are summarized
        as a single archive entry and written to memory/daily/<date>.md.
        Returns True if compaction was performed.
        """
        history = self.memory.get("conversation_history", [])
        if len(history) <= self.CONVERSATION_HARD_LIMIT:
            return False

        # Archive the old messages
        archive    = history[: -self.COMPACTION_TARGET]
        kept       = history[-self.COMPACTION_TARGET :]
        today      = datetime.now().strftime("%Y-%m-%d")
        archive_dir = Path("memory/daily")
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{today}.md"

        # Build archive content
        lines = [f"# Conversation Archive — {today}\n"]
        lines.append(f"_Compacted {len(archive)} messages at {datetime.now().strftime('%H:%M')}_\n\n")
        for msg in archive[-50:]:  # Last 50 of the archive for context
            role    = msg.get("role", "?")
            content = msg.get("content", "")[:200]
            ts      = msg.get("timestamp", "")[:16]
            lines.append(f"**[{ts}] {role}:** {content}\n")

        try:
            with open(archive_path, "a") as f:
                f.write("\n".join(lines))
        except Exception as e:
            logger.warning(f"Could not write archive: {e}")

        self.memory["conversation_history"] = kept
        self.save(force=True)
        logger.info(
            f"Memory compacted: archived {len(archive)} messages, kept {len(kept)}"
        )
        return True

    def save_daily(self) -> str:
        """
        Write memory/daily/YYYY-MM-DD.md with today's stats.
        Idempotent — safe to call multiple times per day.
        Returns path written.
        """
        today     = datetime.now().strftime("%Y-%m-%d")
        daily_dir = Path("memory/daily")
        daily_dir.mkdir(parents=True, exist_ok=True)
        path      = daily_dir / f"{today}.md"

        completed = self.memory.get("completed_tasks", [])
        today_tasks = [
            t for t in completed
            if t.get("completed_at", "").startswith(today)
        ]
        updates = [
            u for u in self.memory.get("memory_updates", [])
            if u.get("ts", "").startswith(today)
        ]

        content = (
            f"# Leon Daily — {today}\n\n"
            f"## Tasks Completed Today ({len(today_tasks)})\n"
        )
        for t in today_tasks[-20:]:
            content += (
                f"- [{t.get('completed_at','?')[:16]}] "
                f"**{t.get('project','?')}** — {t.get('description','?')[:80]}\n"
            )

        if updates:
            content += f"\n## Memory Updates ({len(updates)})\n"
            for u in updates:
                content += f"- [{u.get('ts','?')[:16]}] {u.get('summary','')[:120]}\n"

        content += f"\n_Saved at {datetime.now().strftime('%H:%M')}_\n"

        try:
            path.write_text(content)
        except Exception as e:
            logger.warning(f"Could not write daily memory: {e}")

        return str(path)

    def ensure_working_context(self):
        """Create memory/working_context.md if it doesn't exist."""
        wc = Path("memory/working_context.md")
        wc.parent.mkdir(parents=True, exist_ok=True)
        if not wc.exists():
            wc.write_text(
                "# Working Context\n\n"
                "_This file is auto-managed by Leon. "
                "Add notes here that should persist across sessions._\n"
            )
