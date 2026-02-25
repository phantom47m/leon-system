"""
Leon OpenClaw Interface - PC control via OpenClaw browser + agent API.

OpenClaw provides a managed browser (Chrome/Chromium) with full automation:
  openclaw browser open <url>    — open URL in new tab
  openclaw browser navigate <url> — navigate current tab
  openclaw browser click <ref>   — click element
  openclaw browser type <ref> <text> — type text
  openclaw browser snapshot      — get page accessibility tree (for AI reading)
  openclaw browser screenshot    — capture screenshot

Leon uses this instead of xdg-open / subprocess hacks for all browser tasks.
"""

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.openclaw")

OC = str(Path.home() / ".openclaw" / "bin" / "openclaw")


def _friendly_name(url: str) -> str:
    """Extract a human-readable site name from a URL."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or url
        host = host.replace("www.", "")
        name = host.split(".")[0].capitalize()
        return name
    except Exception:
        return "that"

def _open_in_brave(url: str) -> str:
    """Open a URL as a new tab in the user's existing Brave window."""
    env = dict(__import__("os").environ)
    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":1"
    name = _friendly_name(url)
    for cmd in (
        ["brave-browser", "--new-tab", url],
        ["brave", "--new-tab", url],
        ["xdg-open", url],
    ):
        try:
            subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Opening {name}."
        except FileNotFoundError:
            continue
        except Exception as e:
            return f"Couldn't open the browser."
    return f"Couldn't open the browser."


def _xdg_open(url: str) -> str:
    """Fallback: open a URL using xdg-open."""
    return _open_in_brave(url)


def _oc(*args, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run an openclaw CLI command and return the result."""
    return subprocess.run(
        [OC, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class OpenClawBrowser:
    """Leon's interface to OpenClaw's managed browser for PC control."""

    PROFILE = "openclaw"  # Persistent Brave profile — sessions saved in ~/.openclaw/browser/openclaw/user-data/

    def ensure_running(self) -> bool:
        """Start the OpenClaw browser profile if not running."""
        r = _oc("browser", "start", "--browser-profile", self.PROFILE, timeout=20)
        return r.returncode == 0

    def open_url(self, url: str) -> str:
        """Open a URL as a new tab in the user's existing Brave browser."""
        if not url.startswith("http"):
            url = "https://" + url
        return _open_in_brave(url)

    def navigate(self, url: str) -> str:
        """Navigate current tab to a URL via OpenClaw (CDP automation)."""
        if not url.startswith("http"):
            url = "https://" + url
        r = _oc("browser", "navigate", url, "--browser-profile", self.PROFILE)
        if r.returncode == 0:
            return f"Navigated to {url}."
        return _open_in_brave(url)

    def screenshot(self, path: str = "/tmp/leon_screenshot.png") -> str:
        """Take a screenshot and return the file path."""
        r = _oc("browser", "screenshot", "--browser-profile", self.PROFILE)
        if r.returncode == 0:
            return path
        return ""

    def snapshot(self) -> str:
        """Get the accessibility tree of the current page (efficient AI-readable format)."""
        r = _oc("browser", "snapshot", "--browser-profile", self.PROFILE, "--efficient", timeout=20)
        if r.returncode == 0:
            return r.stdout.strip()
        return ""

    def click(self, ref: str) -> str:
        """Click an element by ref from snapshot."""
        r = _oc("browser", "click", str(ref), "--browser-profile", self.PROFILE)
        if r.returncode == 0:
            return f"Clicked element {ref}."
        return f"Click failed: {r.stderr.strip()[:120]}"

    def type_text(self, ref: str, text: str) -> str:
        """Type text into an element."""
        r = _oc("browser", "type", str(ref), text, "--browser-profile", self.PROFILE)
        if r.returncode == 0:
            return f"Typed into element {ref}."
        return f"Type failed: {r.stderr.strip()[:120]}"

    def press(self, key: str) -> str:
        """Press a keyboard key (Enter, Tab, Escape, etc.)."""
        r = _oc("browser", "press", key, "--browser-profile", self.PROFILE)
        if r.returncode == 0:
            return f"Pressed {key}."
        return f"Press failed: {r.stderr.strip()[:120]}"

    def fill(self, fields: list) -> str:
        """Fill multiple form fields at once. fields = [{"ref": "12", "value": "text"}, ...]"""
        import json as _json
        r = _oc("browser", "fill", "--fields", _json.dumps(fields), "--browser-profile", self.PROFILE)
        if r.returncode == 0:
            return f"Filled {len(fields)} field(s)."
        return f"Fill failed: {r.stderr.strip()[:120]}"

    def select(self, ref: str, *values: str) -> str:
        """Select option(s) in a <select> element."""
        r = _oc("browser", "select", str(ref), *values, "--browser-profile", self.PROFILE)
        if r.returncode == 0:
            return f"Selected {', '.join(values)} in element {ref}."
        return f"Select failed: {r.stderr.strip()[:120]}"

    def upload(self, path: str, ref: str = None) -> str:
        """Arm file upload for the next file chooser."""
        args = ["browser", "upload", path, "--browser-profile", self.PROFILE]
        if ref:
            args += ["--ref", str(ref)]
        r = _oc(*args, timeout=30)
        if r.returncode == 0:
            return f"Upload armed: {path}."
        return f"Upload failed: {r.stderr.strip()[:120]}"

    def download(self, ref: str, path: str = "/tmp/openclaw/downloads/download") -> str:
        """Click a ref and save the resulting download to path."""
        r = _oc("browser", "download", str(ref), path, "--browser-profile", self.PROFILE, timeout=60)
        if r.returncode == 0:
            return path
        return f"Download failed: {r.stderr.strip()[:120]}"

    def evaluate(self, fn: str, ref: str = None) -> str:
        """Run JavaScript against the page or a specific element. fn = '(el) => el.textContent'"""
        args = ["browser", "evaluate", "--fn", fn, "--browser-profile", self.PROFILE]
        if ref:
            args += ["--ref", str(ref)]
        r = _oc(*args, timeout=15)
        if r.returncode == 0:
            return r.stdout.strip()
        return f"Evaluate failed: {r.stderr.strip()[:120]}"

    def wait(self, text: str = None, url: str = None, load: str = None,
             time_ms: int = None, fn: str = None, timeout: int = 20) -> str:
        """Wait for a page condition: text, url pattern, load state, time, or JS."""
        args = ["browser", "wait", "--browser-profile", self.PROFILE]
        if text:
            args += ["--text", text]
        elif url:
            args += ["--url", url]
        elif load:
            args += ["--load", load]
        elif time_ms:
            args += ["--time", str(time_ms)]
        elif fn:
            args += ["--fn", fn]
        r = _oc(*args, timeout=timeout)
        if r.returncode == 0:
            return "Wait condition met."
        return f"Wait failed: {r.stderr.strip()[:120]}"

    def dialog(self, accept: bool = True, prompt_text: str = None) -> str:
        """Arm the next browser dialog (alert/confirm/prompt)."""
        args = ["browser", "dialog", "--browser-profile", self.PROFILE]
        if accept:
            args.append("--accept")
        else:
            args.append("--dismiss")
        if prompt_text:
            args += ["--prompt", prompt_text]
        r = _oc(*args, timeout=30)
        if r.returncode == 0:
            return f"Dialog {'accepted' if accept else 'dismissed'}."
        return f"Dialog failed: {r.stderr.strip()[:120]}"

    def tabs(self) -> list:
        """List open browser tabs."""
        r = _oc("browser", "tabs", "--json")
        if r.returncode == 0:
            try:
                return json.loads(r.stdout)
            except Exception:
                pass
        return []

    def status(self) -> dict:
        """Get browser running status."""
        r = _oc("browser", "status", "--json")
        if r.returncode == 0:
            try:
                return json.loads(r.stdout)
            except Exception:
                pass
        return {"running": False}


def _oc_no_profile(*args, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run an openclaw CLI command without browser profile (for non-browser commands)."""
    return subprocess.run(
        [OC, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class OpenClawCron:
    """Manage scheduled cron jobs via OpenClaw gateway."""

    def list_jobs(self, include_disabled: bool = False) -> list:
        """Return list of cron jobs as dicts."""
        args = ["cron", "list", "--json"]
        if include_disabled:
            args.append("--all")
        r = _oc_no_profile(*args, timeout=10)
        if r.returncode == 0:
            try:
                data = json.loads(r.stdout)
                # OpenClaw returns {"jobs": [...]} or a plain list
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    return data.get("jobs", [])
            except Exception:
                pass
        return []

    def add_job(self, name: str, message: str,
                every: str = None, cron: str = None, at: str = None,
                tz: str = None) -> dict:
        """
        Add a cron job that sends `message` to Leon's agent on schedule.
        Provide ONE of: every="10m"/"1h", cron="0 9 * * *", at="+30m"/ISO
        """
        args = ["cron", "add", "--name", name, "--message", message, "--json"]
        if every:
            args += ["--every", every]
        elif cron:
            args += ["--cron", cron]
        elif at:
            args += ["--at", at]
        if tz:
            args += ["--tz", tz]
        r = _oc_no_profile(*args, timeout=15)
        if r.returncode == 0:
            try:
                return json.loads(r.stdout)
            except Exception:
                return {"ok": True, "output": r.stdout.strip()}
        return {"ok": False, "error": r.stderr.strip()[:200]}

    def remove_job(self, job_id: str) -> str:
        """Delete a cron job by ID."""
        r = _oc_no_profile("cron", "rm", str(job_id), timeout=10)
        if r.returncode == 0:
            return f"Removed cron job {job_id}."
        return f"Remove failed: {r.stderr.strip()[:120]}"

    def run_now(self, job_id: str) -> str:
        """Trigger a cron job immediately."""
        r = _oc_no_profile("cron", "run", str(job_id), timeout=15)
        if r.returncode == 0:
            return f"Cron job {job_id} triggered."
        return f"Run failed: {r.stderr.strip()[:120]}"

    def history(self, job_id: str = None) -> list:
        """Get cron run history."""
        args = ["cron", "runs"]
        if job_id:
            args.append(str(job_id))
        r = _oc_no_profile(*args, timeout=10)
        if r.returncode == 0:
            lines = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
            return lines
        return []

    def format_jobs(self, jobs: list) -> str:
        """Format job list as readable text."""
        if not jobs:
            return "No cron jobs scheduled."
        lines = []
        for j in jobs:
            name = j.get("name") or j.get("id", "?")
            sched_obj = j.get("schedule", {})
            kind = sched_obj.get("kind", "")
            if kind == "cron":
                sched = sched_obj.get("expr", "?")
            elif kind == "every":
                ms = sched_obj.get("everyMs", 0)
                mins = ms // 60000
                if mins >= 1440:
                    sched = f"every {mins // 1440}d"
                elif mins >= 60:
                    sched = f"every {mins // 60}h"
                else:
                    sched = f"every {mins}m"
            else:
                sched = kind or "?"
            status = "disabled" if not j.get("enabled", True) else "active"
            msg = j.get("payload", {}).get("message", "")
            lines.append(f"• [{j.get('id','?')[:8]}] {name} — {sched} ({status}) → \"{msg}\"")
        return "\n".join(lines)


class OpenClawInterface:
    """Leon's full OpenClaw interface — browser control + cron + system status."""

    def __init__(self, config_path: str = "~/.openclaw/openclaw.json"):
        self.config_path = Path(config_path).expanduser()
        self.browser = OpenClawBrowser()
        self.cron = OpenClawCron()
        logger.info("OpenClaw interface initialized")

    def is_openclaw_running(self) -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "openclaw"],
                capture_output=True, text=True,
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_system_status(self) -> dict:
        """Get system resource usage via /proc."""
        status = {}
        try:
            with open("/proc/loadavg") as f:
                parts = f.read().split()
                status["load_1m"] = float(parts[0])
                status["load_5m"] = float(parts[1])
        except Exception:
            status["load_1m"] = -1

        try:
            with open("/proc/meminfo") as f:
                meminfo = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        meminfo[parts[0].strip()] = int(parts[1].strip().split()[0])
                total = meminfo.get("MemTotal", 1)
                available = meminfo.get("MemAvailable", 0)
                status["mem_total_mb"] = total // 1024
                status["mem_available_mb"] = available // 1024
                status["mem_used_pct"] = round((1 - available / total) * 100, 1)
        except Exception:
            status["mem_used_pct"] = -1

        try:
            import os
            disk = os.statvfs("/")
            total = disk.f_blocks * disk.f_frsize
            free = disk.f_bavail * disk.f_frsize
            status["disk_free_gb"] = round(free / (1024 ** 3), 1)
            status["disk_used_pct"] = round((1 - free / total) * 100, 1)
        except Exception:
            status["disk_used_pct"] = -1

        return status
