"""
Leon Agent Index - Searchable index of all agent runs and results.

Tracks every agent spawn, its task, project, status, timings,
output path, and files modified — persisted to JSON.
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.index")


class AgentIndex:
    """Searchable index of all agent runs."""

    def __init__(self, index_path: str = "data/agent_index.json"):
        self._path = Path(index_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[dict] = self._load()
        logger.info(f"Agent index loaded: {len(self.entries)} entries")

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            with open(self._path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            logger.warning("Corrupt agent index, starting fresh")
            return []

    def _save(self):
        tmp = self._path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self.entries[-500:], f, indent=2, default=str)  # keep last 500
        shutil.move(str(tmp), str(self._path))

    def record_spawn(self, agent_id: str, task_desc: str, project: str,
                     brief_path: str, output_path: str):
        """Record a new agent spawn."""
        self.entries.append({
            "agent_id": agent_id,
            "description": task_desc,
            "project": project,
            "status": "running",
            "spawned_at": datetime.now().isoformat(),
            "completed_at": None,
            "duration_seconds": None,
            "brief_path": brief_path,
            "output_path": output_path,
            "files_modified": [],
            "summary": "",
        })
        self._save()

    def record_completion(self, agent_id: str, summary: str,
                          files_modified: list, duration: float):
        """Update an entry when the agent completes."""
        entry = self._find(agent_id)
        if entry:
            entry["status"] = "completed"
            entry["completed_at"] = datetime.now().isoformat()
            entry["duration_seconds"] = round(duration, 1)
            entry["summary"] = summary[:500]
            entry["files_modified"] = files_modified
            self._save()

    def record_failure(self, agent_id: str, error: str, duration: float):
        """Update an entry when the agent fails."""
        entry = self._find(agent_id)
        if entry:
            entry["status"] = "failed"
            entry["completed_at"] = datetime.now().isoformat()
            entry["duration_seconds"] = round(duration, 1)
            entry["summary"] = f"FAILED: {error[:400]}"
            self._save()

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Search entries by description, project, or summary."""
        query_lower = query.lower()
        results = []
        for entry in reversed(self.entries):
            searchable = " ".join([
                entry.get("description", ""),
                entry.get("project", ""),
                entry.get("summary", ""),
                " ".join(entry.get("files_modified", [])),
            ]).lower()
            if query_lower in searchable:
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    def get_by_project(self, project: str, limit: int = 20) -> list[dict]:
        """Get recent entries for a specific project."""
        results = []
        for entry in reversed(self.entries):
            if entry.get("project", "").lower() == project.lower():
                results.append(entry)
                if len(results) >= limit:
                    break
        return results

    def get_recent(self, limit: int = 10) -> list[dict]:
        """Get most recent entries."""
        return list(reversed(self.entries[-limit:]))

    def get_stats(self) -> dict:
        """Get overall agent stats."""
        total = len(self.entries)
        completed = sum(1 for e in self.entries if e.get("status") == "completed")
        failed = sum(1 for e in self.entries if e.get("status") == "failed")
        running = sum(1 for e in self.entries if e.get("status") == "running")

        projects = {}
        for e in self.entries:
            p = e.get("project", "unknown")
            projects[p] = projects.get(p, 0) + 1

        return {
            "total_runs": total,
            "completed": completed,
            "failed": failed,
            "running": running,
            "success_rate": f"{completed / total * 100:.0f}%" if total else "N/A",
            "projects": projects,
        }

    def _find(self, agent_id: str) -> Optional[dict]:
        """Find the most recent entry for an agent_id."""
        for entry in reversed(self.entries):
            if entry.get("agent_id") == agent_id:
                return entry
        return None
