"""
Leon Structured Logger — JSON rotating logs for all key system events.

Log files in logs_structured/:
  tasks.jsonl    — task start / complete / fail
  router.jsonl   — model routing decisions  (also written by router/model_router.py)
  search.jsonl   — search queries and latencies
  health.jsonl   — periodic health check results
  failures.jsonl — failures + alerts (easy to grep for monitoring)

Rotation: 10MB per file, 5 backups → max 50MB per channel.
Thread-safe append via line-buffered file writes.
"""

import json
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_DIR = Path("logs_structured")


class StructuredLogger:
    """Appends JSON log entries to rotating JSONL files."""

    def __init__(self, log_dir: Path = LOG_DIR):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._loggers: dict[str, logging.Logger] = {}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get(self, channel: str) -> logging.Logger:
        if channel not in self._loggers:
            log_path = self.log_dir / f"{channel}.jsonl"
            handler = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=10 * 1024 * 1024,   # 10 MB
                backupCount=5,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            lg = logging.getLogger(f"leon.structured.{channel}")
            lg.propagate = False
            lg.setLevel(logging.DEBUG)
            lg.addHandler(handler)
            self._loggers[channel] = lg
        return self._loggers[channel]

    def _write(self, channel: str, event: str, data: dict):
        entry = {"ts": datetime.now().isoformat(), "event": event, **data}
        self._get(channel).info(json.dumps(entry))

    # ── Task events ───────────────────────────────────────────────────────────

    def task_start(self, agent_id: str, description: str, project: str,
                   tier: str = "standard"):
        self._write("tasks", "task_start", {
            "agent_id":    agent_id,
            "description": description[:120],
            "project":     project,
            "tier":        tier,
        })

    def task_complete(self, agent_id: str, description: str, project: str,
                      duration_s: float, files_modified: list):
        self._write("tasks", "task_complete", {
            "agent_id":       agent_id,
            "description":    description[:120],
            "project":        project,
            "duration_s":     round(duration_s, 1),
            "files_modified": files_modified[:10],
        })

    def task_fail(self, agent_id: str, description: str, project: str, error: str):
        payload = {
            "agent_id":    agent_id,
            "description": description[:120],
            "project":     project,
            "error":       error[:300],
        }
        self._write("tasks",    "task_fail", payload)
        self._write("failures", "task_fail", payload)   # Mirror to failures

    # ── Health checks ─────────────────────────────────────────────────────────

    def health_check(self, checks: dict, source: str = "scheduler"):
        self._write("health", "health_check", {
            "source": source,
            "checks": {k: str(v)[:200] for k, v in checks.items()},
        })

    # ── Alerts ────────────────────────────────────────────────────────────────

    def alert(self, message: str, severity: str = "warning",
              context: Optional[dict] = None):
        self._write("failures", "alert", {
            "message":  message,
            "severity": severity,
            "context":  context or {},
        })
        logging.getLogger("leon.alerts").warning(f"[{severity.upper()}] {message}")

    def write_alert_file(self, message: str, task_name: str = ""):
        """Write a pending_alert file that won't block the system."""
        from pathlib import Path as _P
        alert_dir = _P("data/alerts")
        alert_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = alert_dir / f"alert_{ts}.md"
        fname.write_text(
            f"# Alert — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"**Severity:** warning\n"
            f"**Task:** {task_name or 'unknown'}\n\n"
            f"{message}\n"
        )
        self.alert(message, "warning", {"task": task_name, "file": str(fname)})

    # ── Read-back / reporting ─────────────────────────────────────────────────

    def get_recent_failures(self, limit: int = 20) -> list[dict]:
        path = self.log_dir / "failures.jsonl"
        if not path.exists():
            return []
        try:
            entries = []
            for line in reversed(path.read_text().strip().split("\n")):
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
                if len(entries) >= limit:
                    break
            return entries
        except Exception:
            return []

    def get_task_stats(self) -> dict:
        """Aggregate from tasks.jsonl. Used by leon-status."""
        path = self.log_dir / "tasks.jsonl"
        if not path.exists():
            return {"total": 0, "completed": 0, "failed": 0, "avg_duration_s": 0.0}
        try:
            total = completed = failed = 0
            durations: list[float] = []
            for line in path.read_text().strip().split("\n")[-1000:]:
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    ev = e.get("event", "")
                    if ev == "task_start":
                        total += 1
                    elif ev == "task_complete":
                        completed += 1
                        if "duration_s" in e:
                            durations.append(float(e["duration_s"]))
                    elif ev == "task_fail":
                        failed += 1
                except Exception:
                    pass
            avg = sum(durations) / len(durations) if durations else 0.0
            return {
                "total":          total,
                "completed":      completed,
                "failed":         failed,
                "avg_duration_s": round(avg, 1),
            }
        except Exception:
            return {"total": 0, "completed": 0, "failed": 0, "avg_duration_s": 0.0}

    def get_routing_stats(self) -> dict:
        """Summary of model routing from router.jsonl."""
        path = self.log_dir / "router.jsonl"
        if not path.exists():
            return {}
        stats: dict[str, int] = {}
        try:
            for line in path.read_text().strip().split("\n")[-500:]:
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    model = e.get("model", "unknown")
                    stats[model] = stats.get(model, 0) + 1
                except Exception:
                    pass
        except Exception:
            pass
        return stats


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[StructuredLogger] = None


def get_logger() -> StructuredLogger:
    global _instance
    if _instance is None:
        _instance = StructuredLogger()
    return _instance
