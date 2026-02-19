"""
Leon Project Watcher — Monitor file changes in configured projects

Uses watchdog to detect file system events in project directories.
When agents complete and modify files, Leon can:
- Log what changed
- Auto-commit with descriptive messages (opt-in per project)
- Track file modification patterns
"""

import asyncio
import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.watcher")

# Ignore these patterns
IGNORE_PATTERNS = {
    "__pycache__", ".pyc", ".git", "node_modules", ".next",
    ".env", ".venv", "venv", ".cache", ".tmp", "*.log",
    "data/agent_outputs", "data/task_briefs", "logs/",
}


class ProjectWatcher:
    """
    Watches configured projects for file changes and optionally auto-commits.

    Usage:
        watcher = ProjectWatcher(projects_config)
        watcher.start()
        ...
        changes = watcher.get_recent_changes("project-name")
    """

    def __init__(self, projects: list):
        self.projects = {p["name"]: p for p in projects}
        self._observers = []
        self._changes: dict[str, list] = {}  # project_name -> [change_entries]
        self._max_changes = 200
        self._running = False
        logger.info(f"Project watcher initialized for {len(projects)} projects")

    def start(self):
        """Start watching all configured projects."""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            logger.warning("watchdog not installed — project watcher disabled. Run: pip install watchdog")
            return

        self._running = True

        for name, project in self.projects.items():
            path = Path(project.get("path", ""))
            if not path.is_dir():
                logger.warning(f"Project path not found: {path} ({name})")
                continue

            self._changes[name] = []

            handler = _ChangeHandler(name, self._changes, self._max_changes)
            observer = Observer()
            observer.schedule(handler, str(path), recursive=True)
            observer.daemon = True
            observer.start()
            self._observers.append(observer)
            logger.info(f"Watching project: {name} at {path}")

    def stop(self):
        """Stop all watchers."""
        self._running = False
        for obs in self._observers:
            obs.stop()
        for obs in self._observers:
            obs.join(timeout=2)
        self._observers = []
        logger.info("Project watcher stopped")

    def get_recent_changes(self, project_name: str, n: int = 20) -> list:
        """Get recent file changes for a project."""
        return self._changes.get(project_name, [])[-n:]

    def get_all_changes_summary(self) -> dict:
        """Get change counts for all projects."""
        return {name: len(changes) for name, changes in self._changes.items()}

    def auto_commit(self, project_name: str, message: str = "") -> Optional[str]:
        """Auto-commit changes in a project with a descriptive message."""
        project = self.projects.get(project_name)
        if not project:
            return f"Unknown project: {project_name}"

        if not project.get("auto_commit", False):
            return f"Auto-commit disabled for {project_name}. Set auto_commit: true in projects.yaml."

        path = project.get("path", "")
        if not Path(path).is_dir():
            return f"Project path not found: {path}"

        # Check if there are uncommitted changes
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=path,
        )
        if not result.stdout.strip():
            return f"No uncommitted changes in {project_name}."

        # Generate commit message from changes
        if not message:
            changes = self.get_recent_changes(project_name, 10)
            if changes:
                files = list(set(c.get("path", "") for c in changes))[:5]
                file_list = ", ".join(Path(f).name for f in files)
                message = f"Auto-commit by Leon: updated {file_list}"
            else:
                message = "Auto-commit by Leon"

        # Stage and commit
        subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True)
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=path, capture_output=True, text=True,
        )

        if result.returncode == 0:
            # Extract short hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=path, capture_output=True, text=True,
            )
            short_hash = hash_result.stdout.strip()
            logger.info(f"Auto-committed in {project_name}: {short_hash}")
            return f"Committed {short_hash} in {project_name}: {message}"
        else:
            return f"Commit failed: {result.stderr.strip()[:200]}"


class _ChangeHandler:
    """Watchdog event handler that records file changes."""

    def __init__(self, project_name: str, changes: dict, max_entries: int):
        self.project_name = project_name
        self.changes = changes
        self.max_entries = max_entries

    def dispatch(self, event):
        """Handle any file system event."""
        if event.is_directory:
            return

        src = event.src_path
        # Filter out ignored patterns
        for pattern in IGNORE_PATTERNS:
            if pattern in src:
                return

        event_type = event.event_type  # created, modified, deleted, moved
        entry = {
            "path": src,
            "type": event_type,
            "timestamp": datetime.now().isoformat(),
        }

        project_changes = self.changes.get(self.project_name, [])
        project_changes.append(entry)

        # Trim
        if len(project_changes) > self.max_entries:
            self.changes[self.project_name] = project_changes[-self.max_entries:]
