"""
Leon Task Scheduler - Recurring task support.

Config-driven from settings.yaml. Tracks last_run times
to avoid duplicate execution.

Example settings.yaml entry:
    scheduler:
      tasks:
        - name: "Daily lead hunt"
          command: "find leads"
          interval_hours: 24
          enabled: true
        - name: "Morning briefing"
          command: "daily briefing"
          interval_hours: 24
          enabled: true
        - name: "System health check"
          command: "/status"
          interval_hours: 6
          enabled: true
"""

import json
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("leon.scheduler")


class TaskScheduler:
    """Manages recurring scheduled tasks."""

    def __init__(self, config: list, state_path: str = "data/scheduler_state.json"):
        self._tasks = config or []
        self._state_path = Path(state_path)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state: dict = self._load_state()
        logger.info(f"Scheduler initialized: {len(self._tasks)} tasks configured")

    def _load_state(self) -> dict:
        """Load last-run times from disk."""
        if not self._state_path.exists():
            return {}
        try:
            with open(self._state_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            return {}

    def _save_state(self):
        """Atomic write of state."""
        tmp = self._state_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self._state, f, indent=2, default=str)
        shutil.move(str(tmp), str(self._state_path))

    def get_due_tasks(self) -> list[dict]:
        """Return list of tasks that are due to run now."""
        now = datetime.now()
        due = []

        for task in self._tasks:
            if not task.get("enabled", True):
                continue

            name = task["name"]
            interval = timedelta(hours=task.get("interval_hours", 24))
            last_run_str = self._state.get(name)

            if last_run_str:
                try:
                    last_run = datetime.fromisoformat(last_run_str)
                    if now - last_run < interval:
                        continue  # Not due yet
                except ValueError:
                    pass  # Corrupt timestamp, run it

            due.append(task)

        return due

    def mark_completed(self, task_name: str):
        """Record that a scheduled task has been executed."""
        self._state[task_name] = datetime.now().isoformat()
        self._save_state()
        logger.info(f"Scheduled task completed: {task_name}")

    def get_schedule_summary(self) -> list[dict]:
        """Get status of all scheduled tasks."""
        now = datetime.now()
        summary = []

        for task in self._tasks:
            name = task["name"]
            interval_hours = task.get("interval_hours", 24)
            enabled = task.get("enabled", True)
            last_run_str = self._state.get(name)

            next_run = "now"
            if last_run_str:
                try:
                    last_run = datetime.fromisoformat(last_run_str)
                    next_at = last_run + timedelta(hours=interval_hours)
                    if next_at > now:
                        delta = next_at - now
                        hours = delta.total_seconds() / 3600
                        next_run = f"in {hours:.1f}h"
                    else:
                        next_run = "overdue"
                except ValueError:
                    next_run = "unknown"

            summary.append({
                "name": name,
                "command": task.get("command", ""),
                "interval_hours": interval_hours,
                "enabled": enabled,
                "last_run": last_run_str or "never",
                "next_run": next_run,
            })

        return summary
