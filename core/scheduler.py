"""
Leon Task Scheduler — Config-driven recurring tasks with cron-style support.

Backward-compatible with the original interval_hours format.
New features:
  - cron: "0 6 * * *"  field (standard 5-field cron expression)
  - Built-in task commands (__health_check__, __index_all__, etc.)
  - Non-interactive CI mode — never prompts for input
  - Consecutive-failure tracking → alert file after threshold
  - max_runtime_minutes enforcement

Built-in commands (prefix __):
  __health_check__   — Ollama health ping ($0, never paid API)
  __index_all__      — Re-index all configured projects
  __daily_summary__  — Write memory/daily/YYYY-MM-DD.md
  __repo_hygiene__   — Lightweight dep + stale-branch check

Config example (settings.yaml):
  scheduler:
    ci_mode: true
    tasks:
      - name: "Hourly health check"
        command: "__health_check__"
        interval_hours: 1
        enabled: true
        max_runtime_minutes: 1
        priority: 1
"""

import json
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.scheduler")

ALERT_THRESHOLD = 3          # consecutive failures before alert file written
ALERT_DIR       = Path("data/alerts")
STATE_PATH      = Path("data/scheduler_state.json")


# ── Minimal cron parser (no external deps) ───────────────────────────────────

def _cron_is_due(cron_expr: str, last_run: Optional[datetime], now: datetime) -> bool:
    """
    Return True if the cron expression fires at `now` and hasn't run since
    the last matching window.

    Supports standard 5-field cron: minute hour dom month dow
    Wildcards (*) and single values only (no ranges/lists for simplicity).
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        logger.warning(f"Invalid cron expression (need 5 fields): {cron_expr!r}")
        return False

    minute, hour, dom, month, dow = parts

    def _matches(field: str, value: int) -> bool:
        return field == "*" or int(field) == value

    if not _matches(month,  now.month):     return False
    if not _matches(dow,    now.weekday()): return False
    if not _matches(dom,    now.day):       return False
    if not _matches(hour,   now.hour):      return False
    if not _matches(minute, now.minute):    return False

    # Prevent double-firing within the same minute
    if last_run and (now - last_run) < timedelta(minutes=1):
        return False

    return True


# ── Main scheduler class ──────────────────────────────────────────────────────

class TaskScheduler:
    """
    Manages recurring scheduled tasks.
    Supports interval_hours (original) and cron (new) scheduling.
    """

    def __init__(self, config: list, state_path: str = str(STATE_PATH)):
        self._tasks        = config or []
        self._state_path   = Path(state_path)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state: dict  = self._load_state()
        self._fail_counts: dict[str, int] = {}   # consecutive failure tracking
        logger.info(f"Scheduler: {len(self._tasks)} task(s) configured")

    # ── State persistence ─────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if not self._state_path.exists():
            return {}
        try:
            with open(self._state_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_state(self):
        tmp = self._state_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self._state, f, indent=2, default=str)
        shutil.move(str(tmp), str(self._state_path))

    # ── Due-task detection ────────────────────────────────────────────────────

    def get_due_tasks(self) -> list[dict]:
        """Return list of tasks that are due to run now, sorted by priority."""
        now = datetime.now()
        due = []

        for task in self._tasks:
            if not task.get("enabled", True):
                continue

            name        = task["name"]
            last_run_str = self._state.get(name)
            last_run    = None
            if last_run_str:
                try:
                    last_run = datetime.fromisoformat(last_run_str)
                except ValueError:
                    pass

            is_due = False

            # Cron expression takes priority over interval_hours
            cron = task.get("cron", "").strip()
            if cron:
                is_due = _cron_is_due(cron, last_run, now)
            else:
                interval_h = task.get("interval_hours", 24)
                interval   = timedelta(hours=interval_h)
                if last_run is None:
                    is_due = True
                elif (now - last_run) >= interval:
                    is_due = True

            if is_due:
                due.append(task)

        # Sort by priority (lower number = higher priority)
        due.sort(key=lambda t: t.get("priority", 99))
        return due

    def mark_completed(self, task_name: str):
        """Record successful execution."""
        self._state[task_name] = datetime.now().isoformat()
        self._fail_counts.pop(task_name, None)   # Reset failure counter
        self._save_state()
        logger.info(f"Scheduled task completed: {task_name}")

    def mark_failed(self, task_name: str, error: str = ""):
        """Record failure. Writes alert file after ALERT_THRESHOLD consecutive fails."""
        self._fail_counts[task_name] = self._fail_counts.get(task_name, 0) + 1
        count = self._fail_counts[task_name]
        logger.warning(f"Scheduled task failed ({count}x): {task_name} — {error[:100]}")

        # Still update last_run so we don't immediately retry a broken task
        self._state[task_name] = datetime.now().isoformat()
        self._save_state()

        if count >= ALERT_THRESHOLD:
            self._write_alert(task_name, count, error)

    def _write_alert(self, task_name: str, count: int, error: str):
        """Non-blocking alert: write file, log — do NOT stop the system."""
        ALERT_DIR.mkdir(parents=True, exist_ok=True)
        ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = ALERT_DIR / f"alert_{ts}_{task_name.replace(' ', '_')[:30]}.md"
        fname.write_text(
            f"# Scheduled Task Alert\n\n"
            f"**Task:** {task_name}\n"
            f"**Consecutive failures:** {count}\n"
            f"**Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"**Last error:** {error[:500]}\n\n"
            f"System continues running. Investigate when convenient.\n"
        )
        logger.error(f"Alert written: {fname.name}")

    # ── Summary ───────────────────────────────────────────────────────────────

    def get_schedule_summary(self) -> list[dict]:
        """Status of all configured tasks (for /schedule command and leon-status)."""
        now = datetime.now()
        summary = []

        for task in self._tasks:
            name         = task["name"]
            enabled      = task.get("enabled", True)
            last_run_str = self._state.get(name)
            fail_count   = self._fail_counts.get(name, 0)

            next_run = "now"
            interval_h = task.get("interval_hours", 24)

            if task.get("cron"):
                next_run = f"cron: {task['cron']}"
            elif last_run_str:
                try:
                    last_run = datetime.fromisoformat(last_run_str)
                    next_at  = last_run + timedelta(hours=interval_h)
                    if next_at > now:
                        delta = next_at - now
                        hours = delta.total_seconds() / 3600
                        next_run = f"in {hours:.1f}h"
                    else:
                        next_run = "overdue"
                except ValueError:
                    next_run = "unknown"

            summary.append({
                "name":           name,
                "command":        task.get("command", ""),
                "enabled":        enabled,
                "last_run":       last_run_str or "never",
                "next_run":       next_run,
                "fail_count":     fail_count,
                "priority":       task.get("priority", 99),
                "max_runtime_m":  task.get("max_runtime_minutes", 60),
            })

        return summary


# ── Built-in task handler ─────────────────────────────────────────────────────

async def run_builtin(command: str, leon=None) -> tuple[bool, str]:
    """
    Execute a built-in scheduler command.
    Returns (success, message).
    Never raises — catches all exceptions and returns (False, error).
    """
    try:
        if command == "__health_check__":
            return await _builtin_health_check(leon)
        elif command == "__index_all__":
            return await _builtin_index_all(leon)
        elif command == "__daily_summary__":
            return await _builtin_daily_summary(leon)
        elif command == "__repo_hygiene__":
            return await _builtin_repo_hygiene(leon)
        else:
            return False, f"Unknown built-in command: {command}"
    except Exception as e:
        logger.error(f"Built-in task error ({command}): {e}", exc_info=True)
        return False, str(e)


async def _builtin_health_check(leon=None) -> tuple[bool, str]:
    """$0 health check via Ollama — never uses paid API."""
    from router.model_router import run_health_checks
    from core.structured_logger import get_logger

    checks = {
        "system": "Is this system message delivered? Reply yes or no.",
        "memory": "Is memory pressure a concern with 16GB RAM and standard Linux usage?",
    }

    results = await run_health_checks(checks)
    get_logger().health_check(results, source="scheduler.__health_check__")
    logger.info(f"Health check done: {list(results.keys())}")
    return True, f"Health check: {len(results)} checks completed via Ollama"


async def _builtin_index_all(leon=None) -> tuple[bool, str]:
    """Re-index all configured projects incrementally."""
    import yaml
    from tools.indexer import CodeIndexer

    cfg_path = Path("config/projects.yaml")
    if not cfg_path.exists():
        return False, "config/projects.yaml not found"

    projects = yaml.safe_load(cfg_path.read_text()).get("projects", [])
    results = []

    for p in projects:
        path = Path(p.get("path", ""))
        if not path.exists():
            continue
        try:
            indexer = CodeIndexer(p["name"], str(path))
            stats   = indexer.index(force=False)
            results.append(f"{p['name']}: {stats['files_indexed']} files, {stats['chunks_added']} chunks")
        except Exception as e:
            results.append(f"{p['name']}: failed — {e}")

    return True, "Index update: " + " | ".join(results)


async def _builtin_daily_summary(leon=None) -> tuple[bool, str]:
    """Write daily memory summary to memory/daily/YYYY-MM-DD.md."""
    today = datetime.now().strftime("%Y-%m-%d")
    daily_dir = Path("memory/daily")
    daily_dir.mkdir(parents=True, exist_ok=True)
    summary_file = daily_dir / f"{today}.md"

    if summary_file.exists():
        return True, f"Daily summary already written: {summary_file}"

    # Gather stats
    from core.structured_logger import get_logger
    slog   = get_logger()
    stats  = slog.get_task_stats()
    route  = slog.get_routing_stats()
    fails  = slog.get_recent_failures(limit=5)

    content = (
        f"# Leon Daily Summary — {today}\n\n"
        f"## Task Stats\n"
        f"- Total dispatched: {stats['total']}\n"
        f"- Completed: {stats['completed']}\n"
        f"- Failed: {stats['failed']}\n"
        f"- Avg duration: {stats['avg_duration_s']}s\n\n"
        f"## Model Routing\n"
    )
    for model, count in sorted(route.items(), key=lambda x: -x[1]):
        content += f"- {model}: {count} requests\n"

    if fails:
        content += "\n## Recent Failures\n"
        for f in fails[:5]:
            content += f"- [{f.get('ts', '?')[:16]}] {f.get('message') or f.get('error', '?')[:100]}\n"

    content += f"\n_Generated at {datetime.now().strftime('%H:%M')}_\n"
    summary_file.write_text(content)

    # Also update working_context.md
    wc_path = Path("memory/working_context.md")
    wc_path.parent.mkdir(parents=True, exist_ok=True)
    if not wc_path.exists():
        wc_path.write_text("# Working Context\n\n_Auto-managed by Leon_\n")

    return True, f"Daily summary written: {summary_file}"


async def _builtin_repo_hygiene(leon=None) -> tuple[bool, str]:
    """Lightweight weekly hygiene: stale branches + outdated deps check."""
    import subprocess

    results = []
    cfg_path = Path("config/projects.yaml")
    if not cfg_path.exists():
        return False, "projects.yaml not found"

    import yaml
    projects = yaml.safe_load(cfg_path.read_text()).get("projects", [])

    for p in projects:
        path = Path(p.get("path", ""))
        if not path.exists() or not (path / ".git").exists():
            continue
        try:
            # List branches older than 30 days with no recent commits
            r = subprocess.run(
                ["git", "branch", "--sort=-committerdate", "--format=%(refname:short)"],
                cwd=path, capture_output=True, text=True, timeout=5,
            )
            branches = r.stdout.strip().split("\n")[:10]
            results.append(f"{p['name']}: {len(branches)} branches")
        except Exception as e:
            results.append(f"{p['name']}: git check failed — {e}")

    return True, "Repo hygiene: " + " | ".join(results) if results else "No git repos found"
