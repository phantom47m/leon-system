"""
Leon Night Mode — Autonomous overnight task execution.

Allows Leon to work through a project task backlog without user supervision.
Tasks are added via voice/text, executed while the user sleeps, and
summarized in a morning briefing.

The night mode sits between the awareness loop and the agent manager:
  User adds tasks → backlog persisted to disk
  Night loop fires → dispatches tasks up to concurrency limit
  Agents complete → awareness loop notifies night mode → next task starts
"""

import asyncio
import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .leon import Leon

logger = logging.getLogger("leon.night")


class NightMode:
    """
    Autonomous overnight task execution system.

    Maintains a persistent JSON backlog of coding tasks and processes them
    without user interaction, respecting the configured concurrency limit.
    """

    BACKLOG_PATH = Path("data/night_tasks.json")
    LOG_PATH = Path("data/night_log.json")
    FINISHED_TASK_LIMIT = 200  # Max completed/failed tasks to keep in backlog

    def __init__(self, leon: "Leon"):
        self.leon = leon
        self._active = False
        self._loop_task: Optional[asyncio.Task] = None
        self._session_log: list[dict] = []
        self._dispatch_lock = asyncio.Lock()
        self._backlog: list[dict] = self._load_backlog()

    # ─── Persistence ──────────────────────────────────────────────────────

    def _load_backlog(self) -> list[dict]:
        if not self.BACKLOG_PATH.exists():
            return []
        try:
            tasks = json.loads(self.BACKLOG_PATH.read_text())
            # On startup, any "running" tasks have lost their process — reset to pending
            recovered = 0
            for t in tasks:
                if t.get("status") == "running":
                    t["status"] = "pending"
                    t["agent_id"] = None
                    recovered += 1
            if recovered:
                logger.info(f"Night mode: recovered {recovered} interrupted task(s) → pending")
            return tasks
        except Exception:
            return []

    def _save_backlog(self):
        # Trim old completed/failed tasks to prevent unbounded growth
        active = [t for t in self._backlog if t.get("status") in ("pending", "running")]
        finished = [t for t in self._backlog if t.get("status") in ("completed", "failed")]
        if len(finished) > self.FINISHED_TASK_LIMIT:
            trimmed = len(finished) - self.FINISHED_TASK_LIMIT
            finished = finished[-self.FINISHED_TASK_LIMIT:]
            logger.debug(f"Night mode: trimmed {trimmed} old finished task(s) from backlog")
        self._backlog = active + finished
        tmp = self.BACKLOG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._backlog, indent=2, default=str))
        shutil.move(str(tmp), str(self.BACKLOG_PATH))

    def _load_log(self) -> list[dict]:
        if not self.LOG_PATH.exists():
            return []
        try:
            return json.loads(self.LOG_PATH.read_text())
        except Exception:
            return []

    def _flush_session_log(self):
        if not self._session_log:
            return
        existing = self._load_log()
        existing.extend(self._session_log)
        existing = existing[-1000:]  # keep last 1000 events
        tmp = self.LOG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=2, default=str))
        shutil.move(str(tmp), str(self.LOG_PATH))
        self._session_log = []

    # ─── Task Management ──────────────────────────────────────────────────

    def add_task(self, description: str, project_name: str, priority: int = 1) -> dict:
        """Add a task to the overnight backlog. Returns the task dict."""
        task = {
            "id": uuid.uuid4().hex[:8],
            "description": description,
            "project": project_name,
            "priority": priority,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "agent_id": None,
            "completed_at": None,
            "result": None,
        }
        # Insert maintaining priority order (higher first) among pending tasks
        insert_pos = len(self._backlog)
        for i, t in enumerate(self._backlog):
            if t["status"] == "pending" and t.get("priority", 1) < priority:
                insert_pos = i
                break
        self._backlog.insert(insert_pos, task)
        self._save_backlog()
        logger.info(f"Night task queued [{task['id']}]: {description[:60]} ({project_name})")
        return task

    def remove_task(self, task_id: str) -> bool:
        """Remove a pending task. Returns True if found and removed."""
        before = len(self._backlog)
        self._backlog = [
            t for t in self._backlog
            if not (t["id"] == task_id and t["status"] == "pending")
        ]
        changed = len(self._backlog) < before
        if changed:
            self._save_backlog()
        return changed

    def clear_pending(self) -> int:
        """Clear all pending tasks. Returns count cleared."""
        pending_count = sum(1 for t in self._backlog if t["status"] == "pending")
        self._backlog = [t for t in self._backlog if t["status"] != "pending"]
        self._save_backlog()
        return pending_count

    def get_pending(self) -> list[dict]:
        return [t for t in self._backlog if t["status"] == "pending"]

    def get_running(self) -> list[dict]:
        return [t for t in self._backlog if t["status"] == "running"]

    # ─── Mode Control ─────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    async def enable(self):
        """Start autonomous task processing."""
        if self._active:
            return
        self._active = True
        self._session_log = []
        self._loop_task = asyncio.create_task(self._run_loop())
        logger.info("Night mode enabled — autonomous processing started")

    async def disable(self):
        """Stop autonomous processing. Running agents continue until done."""
        self._active = False
        if self._loop_task:
            self._loop_task.cancel()
            self._loop_task = None
        self._flush_session_log()
        logger.info("Night mode disabled")

    # ─── Autonomous Loop ──────────────────────────────────────────────────

    async def _run_loop(self):
        """Main autonomous loop — polls every 60s and dispatches pending tasks."""
        logger.info("Night mode loop running")
        while self._active:
            try:
                await self._try_dispatch()
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Night mode loop error: {e}")
                await asyncio.sleep(30)

    async def try_dispatch(self):
        """
        Try to dispatch pending tasks into open agent slots.
        Called externally when an agent slot frees up (e.g. task completed).
        Also starts night mode automatically if tasks are pending and there's capacity.
        """
        if not self._active and not self.get_pending():
            return
        await self._try_dispatch()

    async def _try_dispatch(self):
        """Check capacity and dispatch pending tasks up to the concurrency limit.

        Uses an asyncio lock to prevent concurrent calls from over-dispatching
        (e.g. awareness loop + manual trigger racing each other).
        """
        async with self._dispatch_lock:
            await self._try_dispatch_locked()

    async def _try_dispatch_locked(self):
        pending = self.get_pending()
        if not pending:
            return

        active_count = len(self.leon.agent_manager.active_agents)
        capacity = self.leon.task_queue.max_concurrent - active_count

        if capacity <= 0:
            logger.debug(f"Night mode: {len(pending)} tasks pending, no capacity ({active_count}/{self.leon.task_queue.max_concurrent} agents running)")
            return

        # Never run more than 1 agent per project — prevents git conflicts on same codebase
        running_projects = {t.get("project") for t in self.get_running()}

        to_dispatch = []
        for task in pending:
            if len(to_dispatch) >= capacity:
                break
            proj = task.get("project")
            if proj in running_projects:
                continue  # Already have an agent on this project, skip
            to_dispatch.append(task)
            running_projects.add(proj)  # Reserve this project slot

        if not to_dispatch:
            logger.debug(f"Night mode: {len(pending)} tasks pending but all projects already have running agents")
            return

        logger.info(f"Night mode: dispatching {len(to_dispatch)} task(s), {capacity} slot(s) available")
        for task in to_dispatch:
            await self._dispatch_task(task)

    async def _dispatch_task(self, task: dict):
        """Dispatch a single backlog task to an agent."""
        task["status"] = "running"
        self._save_backlog()

        project = self.leon._resolve_project(task["project"], task["description"])
        if not project:
            task["status"] = "failed"
            task["result"] = f"No project matched '{task['project']}'"
            task["completed_at"] = datetime.now().isoformat()
            self._save_backlog()
            logger.warning(f"Night task failed — no project match: {task['description'][:60]}")
            return

        try:
            brief_path = await self.leon._create_task_brief(task["description"], project)
            agent_id = await self.leon.agent_manager.spawn_agent(
                brief_path=brief_path,
                project_path=project["path"],
            )
            task_obj = {
                "id": agent_id,
                "description": task["description"],
                "project_name": project["name"],
                "brief_path": brief_path,
                "night_task_id": task["id"],
            }
            self.leon.task_queue.add_task(agent_id, task_obj)
            self.leon.memory.add_active_task(agent_id, task_obj)
            task["agent_id"] = agent_id
            self._save_backlog()

            self._session_log.append({
                "event": "dispatched",
                "task_id": task["id"],
                "agent_id": agent_id,
                "description": task["description"],
                "project": project["name"],
                "timestamp": datetime.now().isoformat(),
            })
            logger.info(f"Night task dispatched: [{task['id']}] {task['description'][:60]} → agent {agent_id}")
            asyncio.create_task(self.leon._send_discord_message(
                f"🤖 **Agent started** — `{agent_id}`\n"
                f"**Project:** {project['name']}\n"
                f"**Task:** {task['description'][:200]}",
                channel="dev",
            ))

        except Exception as e:
            task["status"] = "failed"
            task["result"] = str(e)
            task["completed_at"] = datetime.now().isoformat()
            self._save_backlog()
            logger.error(f"Night task dispatch failed: {e}")

    # ─── Awareness Loop Hooks ─────────────────────────────────────────────

    def mark_agent_completed(self, agent_id: str, summary: str):
        """Called by the awareness loop when an agent finishes successfully."""
        for task in self._backlog:
            if task.get("agent_id") == agent_id:
                task["status"] = "completed"
                task["result"] = summary[:500] if summary else "Done"
                task["completed_at"] = datetime.now().isoformat()
                self._save_backlog()
                self._session_log.append({
                    "event": "completed",
                    "task_id": task["id"],
                    "agent_id": agent_id,
                    "description": task["description"],
                    "summary": summary[:200] if summary else "",
                    "timestamp": datetime.now().isoformat(),
                })
                logger.info(f"Night task completed: [{task['id']}] {task['description'][:60]}")
                self._flush_session_log()
                return

    def mark_agent_failed(self, agent_id: str, error: str):
        """Called by the awareness loop when an agent fails."""
        for task in self._backlog:
            if task.get("agent_id") == agent_id:
                task["status"] = "failed"
                task["result"] = error[:300] if error else "Unknown error"
                task["completed_at"] = datetime.now().isoformat()
                self._save_backlog()
                self._session_log.append({
                    "event": "failed",
                    "task_id": task["id"],
                    "agent_id": agent_id,
                    "description": task["description"],
                    "error": error[:200] if error else "",
                    "timestamp": datetime.now().isoformat(),
                })
                logger.warning(f"Night task failed: [{task['id']}] {task['description'][:60]}")
                self._flush_session_log()
                return

    # ─── Reporting ────────────────────────────────────────────────────────

    def get_status_text(self) -> str:
        """Single-line status for conversational responses."""
        pending = self.get_pending()
        running = self.get_running()
        mode_str = "ON" if self._active else "OFF"
        if not pending and not running:
            return f"Night mode {mode_str} — backlog empty."
        parts = []
        if running:
            parts.append(f"{len(running)} running")
        if pending:
            parts.append(f"{len(pending)} pending")
        return f"Night mode {mode_str} — {', '.join(parts)}."

    def get_backlog_text(self) -> str:
        """Human-readable backlog listing."""
        pending = self.get_pending()
        running = self.get_running()
        lines = []
        if running:
            lines.append(f"**Running ({len(running)}):**")
            for t in running:
                lines.append(f"  ⚙ [{t['id']}] {t['description'][:70]} ({t['project']})")
        if pending:
            lines.append(f"**Queued ({len(pending)}):**")
            for i, t in enumerate(pending[:15], 1):
                lines.append(f"  {i}. [{t['id']}] {t['description'][:70]} ({t['project']})")
            if len(pending) > 15:
                lines.append(f"  ... and {len(pending) - 15} more")
        if not lines:
            lines.append("Backlog is empty.")
        return "\n".join(lines)

    def generate_morning_briefing(self, since_hours: float = 10.0) -> str:
        """Generate a human-readable summary of overnight work."""
        cutoff = datetime.now().timestamp() - (since_hours * 3600)

        completed = [
            t for t in self._backlog
            if t.get("status") == "completed" and t.get("completed_at")
            and datetime.fromisoformat(t["completed_at"]).timestamp() > cutoff
        ]
        failed = [
            t for t in self._backlog
            if t.get("status") == "failed" and t.get("completed_at")
            and datetime.fromisoformat(t["completed_at"]).timestamp() > cutoff
        ]
        pending = self.get_pending()

        lines = []
        if not completed and not failed:
            lines.append("Nothing ran overnight — backlog was empty or night mode was off.")
        else:
            lines.append(
                f"Overnight: {len(completed)} task{'s' if len(completed) != 1 else ''} done"
                + (f", {len(failed)} failed" if failed else "") + "."
            )
            if completed:
                lines.append("\nCompleted:")
                for t in completed[:10]:
                    lines.append(f"  ✓ {t['description'][:70]} ({t['project']})")
                    result = str(t.get("result") or "")
                    if result and result != "Done":
                        lines.append(f"    → {result[:120]}")
            if failed:
                lines.append("\nFailed:")
                for t in failed[:5]:
                    lines.append(f"  ✗ {t['description'][:70]}")
                    err = str(t.get("result") or "unknown error")
                    lines.append(f"    Error: {err[:100]}")

        if pending:
            lines.append(f"\n{len(pending)} task(s) still in the backlog waiting to run.")

        return "\n".join(lines)
