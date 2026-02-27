"""
RoutingMixin — extracted from core/leon.py to keep that file manageable.

Contains: _route_special_commands, _SITE_MAP, _route_to_system_skill
All self.* references resolve through Leon's MRO at runtime.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon")

# ── Keyword pre-routing table (Issue #20) ──────────────────────────────────
# Maps unambiguous natural language to system skills WITHOUT an LLM call.
# Only zero-arg or fixed-arg commands — anything needing extracted args
# falls through to the AI classifier.
# Patterns are tested against the voice-prefix-stripped, lowercased message.
# First match wins — put more specific patterns before broader ones.

_KEYWORD_ROUTES: list[tuple[re.Pattern, str, dict]] = [
    # ── System Info ──
    (re.compile(r'\bcpu\s+(?:usage|load)\b'), 'cpu_usage', {}),
    (re.compile(r'\b(?:ram|memory)\s+usage\b'), 'ram_usage', {}),
    (re.compile(r'\b(?:disk|storage)\s+usage\b'), 'disk_usage', {}),
    (re.compile(r'\b(?:disk\s+free|free\s+space)\b'), 'disk_free', {}),
    (re.compile(r'\bsystem\s+(?:info|summary)\b'), 'system_info', {}),
    (re.compile(r'\buptime\b'), 'uptime', {}),
    (re.compile(r'\b(?:ip\s+address|my\s+ip)\b'), 'ip_address', {}),
    (re.compile(r'\bbattery(?:\s+(?:level|status|life))?\b'), 'battery', {}),
    (re.compile(r'\bgpu\s+temp'), 'gpu_temp', {}),  # before generic temp
    (re.compile(r'\b(?:cpu\s+temp|temperature)\b'), 'temperature', {}),
    (re.compile(r'\bhostname\b'), 'hostname', {}),
    (re.compile(r'\bwho\s*am\s*i\b'), 'who_am_i', {}),
    (re.compile(r'\b(?:what\s+time|current\s+time|what.s\s+the\s+time)\b'), 'date_time', {}),

    # ── Media (volume/play/pause handled by earlier fast paths) ──
    (re.compile(r'\b(?:next|skip)\s+(?:track|song)\b'), 'next_track', {}),
    (re.compile(r'\bprev(?:ious)?\s+(?:track|song)\b'), 'prev_track', {}),
    (re.compile(r'\b(?:now\s+playing|what.s\s+playing|current\s+(?:track|song))\b'), 'now_playing', {}),

    # ── Desktop ──
    (re.compile(r'\bscreenshot\b|\bscreen\s*cap(?:ture)?\b'), 'screenshot', {}),
    (re.compile(r'\block\s+(?:(?:the|my)\s+)?(?:screen|computer|pc|desktop)\b'), 'lock_screen', {}),
    (re.compile(r'\bbrightness\s+up\b|\bincrease\s+brightness\b|\bbrighter\b'), 'brightness_up', {}),
    (re.compile(r'\bbrightness\s+down\b|\bdecrease\s+brightness\b|\bdimmer\b'), 'brightness_down', {}),

    # ── Clipboard ──
    (re.compile(r'\bclipboard\s+histor'), 'clipboard_history', {}),
    (re.compile(r'\b(?:get|show)\s+clipboard\b|\bwhat.s\s+(?:on\s+)?(?:my\s+)?clipboard\b|\bpaste\s+buffer\b'), 'clipboard_get', {}),

    # ── Network ──
    (re.compile(r'\bwifi\s+(?:status|info)\b'), 'wifi_status', {}),
    (re.compile(r'\bwifi\s+list\b|\bavailable\s+(?:wifi|networks)\b|\bscan\s+wifi\b'), 'wifi_list', {}),
    (re.compile(r'\bspeed\s*test\b|\binternet\s+speed\b'), 'speedtest', {}),

    # ── GPU ──
    (re.compile(r'\bgpu\s+(?:usage|info|status)\b|\bvram\s+usage\b|\bgraphics\s+card\b'), 'gpu_usage', {}),

    # ── Window Management ──
    (re.compile(r'\bminimize(?:\s+(?:this\s+)?window)?\b'), 'minimize_window', {}),
    (re.compile(r'\bmaximize(?:\s+(?:this\s+)?window)?\b'), 'maximize_window', {}),
    (re.compile(r'\btile\s+left\b|\bsnap\s+left\b'), 'tile_left', {}),
    (re.compile(r'\btile\s+right\b|\bsnap\s+right\b'), 'tile_right', {}),
    (re.compile(r'\bclose\s+(?:this\s+)?window\b'), 'close_window', {}),
    (re.compile(r'\blist\s+workspaces\b|\bshow\s+workspaces\b'), 'list_workspaces', {}),
    (re.compile(r'\b(?:list\s+)?running\s+apps?\b|\bwhat.s\s+running\b'), 'list_running', {}),

    # ── OCR ──
    (re.compile(r'\bocr\b|\bread\s+(?:the\s+)?screen\b|\bwhat.s\s+on\s+(?:my\s+)?screen\b'), 'ocr_screen', {}),

    # ── Notes (list only — add/search need args from LLM) ──
    (re.compile(r'\b(?:show|list)\s+(?:my\s+)?notes\b|\bmy\s+notes\b'), 'note_list', {}),

    # ── Downloads ──
    (re.compile(r'\b(?:list|show|recent)\s+downloads\b'), 'list_downloads', {}),

    # ── Timers ──
    (re.compile(r'\b(?:list|show|active)\s+timers?\b'), 'list_timers', {}),

    # ── Volume info ──
    (re.compile(r'\bvolume\s+level\b|\bcurrent\s+volume\b|\bwhat.s\s+(?:the\s+|my\s+)?volume\b'), 'volume_get', {}),

    # ── Process info (fixed default arg) ──
    (re.compile(r'\btop\s+processes\b|\bwhat.s\s+(?:eating|hogging)\b|\bresource\s+hog'), 'top_processes', {'n': 10}),

    # ── Weather (no location → default; "weather in X" falls through to LLM) ──
    (re.compile(r'\b(?:weather|forecast)\b(?!\s+(?:in|for|at)\b)'), 'weather', {}),
]

# Desktop apps that should open via open_app, not as browser URLs
_DESKTOP_APPS = frozenset({
    "terminal", "code", "vscode", "spotify", "files", "nautilus",
    "dolphin", "nemo", "slack", "zoom", "obs", "gimp", "inkscape",
    "blender", "steam", "settings", "calculator", "system monitor",
    "gedit", "text editor", "file manager", "task manager",
})


class RoutingMixin:
    """Special-command routing: hardware, business, vision, security, system skills."""

    async def _route_special_commands(self, message: str) -> Optional[str]:
        """Route messages to specialized modules when keywords match."""
        msg = message.lower()

        # Help command — list available modules
        if msg.strip() in ("help", "what can you do", "commands", "modules"):
            return self._build_help_text()

        # ── Night Mode / Autonomous Coding ──────────────────────────────

        nm = self.night_mode

        # Enable night mode — flexible phrasing
        _nm_on = any(p in msg for p in [
            "night mode on", "turn on night mode", "auto mode on", "turn on auto mode",
            "start auto mode", "enable auto mode", "switch to night mode", "start night mode",
            "enable night mode", "activate night mode", "autonomous mode", "go autonomous",
            "put it in night mode", "night mode:", "night mode,", "coding mode on",
            "work all night", "work through the night", "work overnight",
            "keep working", "keep going", "work continuously", "continuously work",
            "work until done", "keep coding", "dont stop working",
            "continue working", "continue improving", "continue doing", "keep improving",
            "keep at it", "continue where", "carry on", "continue until",
            "keep on working", "keep on improving", "keep going until",
        ])
        # Disable night mode — flexible phrasing
        _nm_off = any(p in msg for p in [
            "night mode off", "turn off night mode", "auto mode off", "turn off auto mode",
            "stop auto mode", "stop night mode", "disable night mode",
            "pause night mode", "end night mode", "cancel night mode", "stop the agents",
        ])

        if _nm_on:
            await nm.enable()
            # Check if task content included in the same message
            _task_triggers = [
                "your tasks are:", "here's the task:", "the task is:", "tasks are:",
                "task for tonight:", "work on this:", "here is the task:", "the tasks:",
            ]
            _inline_task = None
            for tc in _task_triggers:
                if tc in msg:
                    idx = msg.index(tc) + len(tc)
                    candidate = message[idx:].strip()
                    if len(candidate) > 30:
                        _inline_task = candidate
                        break
            if _inline_task:
                # Detect project from the full message — use the combined message + task text
                _proj = self._resolve_project("", message + " " + _inline_task)
                project_name = _proj["name"] if _proj else "Leon System"
                nm.add_task(_inline_task, project_name)
                asyncio.create_task(nm.try_dispatch())
                return f"Auto mode on. Task queued for {project_name} — spawning agent now. I'll keep you updated."

            # No colon-based trigger, but the message itself may describe the work.
            # If it mentions a known project and has substance, treat the whole message as the task.
            if not _inline_task and len(message) > 20:
                _matched_proj = self._resolve_project("", message)
                # Only use if a real match (not just the default fallback with no name in message)
                if _matched_proj and _matched_proj["name"].lower() in msg:
                    nm.add_task(message, _matched_proj["name"])
                    asyncio.create_task(nm.try_dispatch())
                    return f"Auto mode on. On it — queuing that for {_matched_proj['name']} and spawning an agent now."

            pending = nm.get_pending()
            if pending:
                asyncio.create_task(nm.try_dispatch())
                return f"Auto mode on. {len(pending)} task{'s' if len(pending) != 1 else ''} in the queue — dispatching now."
            return "Auto mode on. Queue is empty — send me the task and I'll get started."

        # Disable night mode
        if _nm_off:
            await nm.disable()
            running = nm.get_running()
            if running:
                return f"Auto mode off. {len(running)} agent{'s' if len(running) != 1 else ''} still finishing up."
            return "Auto mode off."

        # Morning briefing / overnight report
        if any(p in msg for p in ["what did you do", "overnight report", "morning briefing", "what happened last night", "night report", "what got done"]):
            briefing = nm.generate_morning_briefing()
            return briefing

        # Add task to backlog
        queue_triggers = ["queue task:", "add task:", "add to backlog:", "tonight do:", "tonight work on:", "work on tonight:", "add to queue:", "your task:", "the task:", "task is:", "go work on:", "start working on:", "go code:"]
        for trigger in queue_triggers:
            if trigger in msg:
                remainder = message[message.lower().index(trigger) + len(trigger):].strip()
                # Parse "description for project" or "description in project"
                project_name = None
                desc = remainder
                for sep in [" for ", " in ", " on "]:
                    if sep in remainder.lower():
                        parts = remainder.split(sep, 1)
                        if len(parts) == 2:
                            desc = parts[0].strip()
                            project_name = parts[1].strip()
                            break
                if not project_name:
                    # No project specified — use first project as default
                    projects = self.projects_config.get("projects", [])
                    project_name = projects[0]["name"] if projects else "unknown"
                task = nm.add_task(desc, project_name)
                status = f"Queued [{task['id']}]: {desc[:60]} ({project_name})."
                if not nm.active:
                    status += " Auto mode is off — say 'auto mode on' or 'keep working' when ready."
                else:
                    # Immediately try to dispatch if slot available
                    asyncio.create_task(nm.try_dispatch())
                    status += " Dispatching now if a slot's free."
                return status

        # List backlog
        if any(p in msg for p in ["backlog", "night queue", "task queue", "what's queued", "what's in the queue", "show queue", "list tasks"]):
            backlog_text = nm.get_backlog_text()
            status_line = nm.get_status_text()
            return f"{status_line}\n\n{backlog_text}"

        # Night mode status
        if any(p in msg for p in ["night mode status", "night mode", "autonomous status"]) and "on" not in msg and "off" not in msg:
            return nm.get_status_text()

        # Clear backlog
        if any(p in msg for p in ["clear backlog", "clear queue", "cancel all tasks", "empty the queue"]):
            cleared = nm.clear_pending()
            if cleared:
                return f"Cleared {cleared} pending task{'s' if cleared != 1 else ''} from the backlog."
            return "Nothing pending to clear."

        # ── Apply self-repair patch + restart ──────────────────────────────────
        if any(p in msg for p in ["apply self-repair", "apply the repair", "apply the fix and restart",
                                   "apply repair and restart", "apply self repair"]):
            import glob as _glob
            leon_path = str(Path(__file__).parent.parent)
            # Find most recent self-repair diff
            patches = sorted(
                _glob.glob(f"{leon_path}/data/agent_zero_jobs/AZ-SELFREPAIR-*/output/patch.diff"),
                key=os.path.getmtime, reverse=True
            )
            if not patches:
                return "No self-repair patch found. Run a self-repair first."
            patch = patches[0]
            result = subprocess.run(
                ["git", "apply", "--check", patch],
                cwd=leon_path, capture_output=True, text=True
            )
            if result.returncode != 0:
                return f"Patch check failed: {result.stderr[:200]}\nApply manually: `git apply {patch}`"
            subprocess.run(["git", "apply", patch], cwd=leon_path, check=True)
            await self._send_discord_message("✅ Patch applied. Restarting Leon in 3 seconds...", channel="chat")
            asyncio.create_task(self._delayed_restart(3))
            return "Patch applied. Restarting now."

        # ── Agent Zero — kill switch ────────────────────────────────────────────
        if any(p in msg for p in ["kill agent zero", "stop agent zero", "kill az job", "abort agent zero"]):
            try:
                from tools.agent_zero_runner import get_runner
                az = get_runner()
                jobs = az.list_jobs()
                if not jobs:
                    return "No Agent Zero jobs running."
                # Find job_id in message or kill most recent
                job_id = next(
                    (w for w in msg.split() if w.startswith("AZ-")),
                    jobs[-1]["job_id"]
                )
                ok = await az.kill_job(job_id)
                return f"{'Killed' if ok else 'Kill attempted for'} Agent Zero job `{job_id}`."
            except ImportError:
                return "Agent Zero not installed — run scripts/setup-agent-zero.sh first."

        # ── Agent Zero — job status ─────────────────────────────────────────
        if any(p in msg for p in ["agent zero status", "az jobs", "agent zero jobs", "list az"]):
            try:
                from tools.agent_zero_runner import get_runner
                az = get_runner()
                jobs = az.list_jobs()
                if not jobs:
                    return f"No Agent Zero jobs running. (Enabled: {az.is_enabled()}, Available: {az.is_available()})"
                lines = [f"**Agent Zero jobs ({len(jobs)}):**"]
                for j in jobs:
                    lines.append(f"• `{j['job_id']}` — {j['task'][:60]}... [{j['status']} {j['elapsed_min']}min]")
                return "\n".join(lines)
            except ImportError:
                return "Agent Zero not installed."

        # ── Plan Mode — cancel / status (triggering is handled by _analyze_request) ──
        # Cancel plan
        if any(p in msg for p in ["cancel plan", "stop plan", "abort plan", "stop the plan", "kill the plan"]):
            if not self.plan_mode.active:
                return "No plan is currently running."
            await self.plan_mode.cancel()
            return "Plan cancelled. Running agents will finish their current task."

        # Plan status
        if any(p in msg for p in ["plan status", "plan progress", "how's the plan", "what's the plan", "how's it going with the plan"]):
            status = self.plan_mode.get_status()
            if not status["active"] and not status["goal"]:
                return "No plan running — just describe what you want built and I'll take it from there."
            done = status["doneTasks"]
            total = status["totalTasks"]
            running = status["runningTasks"]
            failed = status["failedTasks"]
            active_str = "Running" if status["active"] else "Complete"
            phases_text = []
            for ph in status.get("phases", []):
                task_summaries = []
                for t in ph.get("tasks", []):
                    icon = {"completed": "✓", "running": "⚡", "failed": "✗", "pending": "○"}.get(t["status"], "○")
                    task_summaries.append(f"  {icon} {t['title']}")
                phases_text.append(f"Phase {ph['phase']}: {ph['name']}\n" + "\n".join(task_summaries))
            phases_block = "\n\n".join(phases_text) if phases_text else ""
            return (
                f"**Plan: {status['goal']}**\n"
                f"Status: {active_str} — {done}/{total} tasks done"
                + (f", {running} running" if running else "")
                + (f", {failed} failed" if failed else "")
                + ("\n\n" + phases_block if phases_block else "")
            )

        # ── Self-repair: detect when user says something Leon did was broken ─────
        # Catches natural language like:
        #   "your screenshot is broken fix it"
        #   "you sent me a black box"
        #   "that was wrong, fix yourself"
        #   "your voice is bad"
        #   "code yourself better"
        _is_task_brief = len(message) > 200 and any(
            w in msg for w in ["motorev", "phase 1", "phase 2", "app from railway"]
        )
        if not _is_task_brief:
            from .leon import _detect_self_repair as _dsr
            _repair_hit, _component, _issue = _dsr(msg)
            if _repair_hit:
                return await self._handle_self_repair(message, _component, _issue)

        # 3D Printing
        if any(w in msg for w in ["print", "stl", "3d print", "printer", "filament", "spaghetti", "print job", "print queue"]):
            if ("find" in msg or "search" in msg or "stl" in msg) and self.stl_searcher:
                results = await self.stl_searcher.search(message)
                if results:
                    lines = ["Found a few options:\n"]
                    for i, r in enumerate(results[:5], 1):
                        lines.append(f"{i}. **{r.get('name', 'Untitled')}** — {r.get('url', 'N/A')}")
                    return "\n".join(lines)
                return "Nothing came up. Try different keywords?"
            if "status" in msg and self.printer:
                printers = self.printer.list_printers()
                if not printers:
                    return "No printers configured yet. Add them to config/printers.yaml."
                lines = ["Here's what the printers are doing:\n"]
                for p in printers:
                    s = p.get_status()
                    lines.append(f"**{p.name}**: {s.get('state', 'unknown')} — {s.get('progress', 0)}%")
                return "\n".join(lines)

        # Vision
        if any(w in msg for w in ["what do you see", "look at", "who's here", "what's around", "describe the room", "camera"]):
            if not self.vision:
                return "Camera's not set up yet. Want me to configure it?"
            return self.vision.describe_scene()

        # Business — CRM
        if any(w in msg for w in ["crm", "pipeline", "clients", "contacts", "deals", "customer list"]):
            if not self.crm:
                return "CRM isn't set up yet. Want me to get that configured?"
            return json.dumps(self.crm.get_pipeline_summary(), indent=2, default=str)

        # Business — leads
        if any(w in msg for w in ["find clients", "find leads", "hunt leads", "prospect", "generate leads", "new leads", "lead search"]):
            if self.audit_log:
                self.audit_log.log("lead_hunt", message, "info")
            return "On it — hunting for leads now. I'll score them and have something for you shortly."

        # Business — finance
        if any(w in msg for w in ["revenue", "invoice", "how much money", "financial", "earnings", "profit", "expenses", "income", "billing"]):
            if not self.finance:
                return "Finance tracking isn't set up yet. Want me to configure it?"
            return self.finance.get_daily_summary()

        # Business — communications
        if any(w in msg for w in ["send email", "check email", "inbox", "messages", "unread", "compose"]):
            if not self.comms:
                return "Comms module isn't wired up yet. Want me to set it up?"
            # Sending email requires permission
            if "send" in msg and self.permissions:
                if not self.permissions.check_permission("send_email"):
                    return "I'll need your approval to send emails. Run `/approve send_email` to unlock that."
            return "Comms hub's live. Check inbox, send something, or review messages?"

        # Business — briefing
        if any(w in msg for w in ["briefing", "brief me", "daily brief", "morning brief", "daily briefing", "what's happening", "catch me up", "daily summary"]):
            if not self.assistant:
                return "Assistant module isn't loaded. Might need to check the business config."
            return await self.assistant.generate_daily_briefing()

        # Business — schedule/calendar
        if any(w in msg for w in ["schedule", "calendar", "appointments", "meetings today", "what's on my calendar"]):
            if not self.assistant:
                return "Assistant module isn't loaded. Can't check the calendar without it."
            return await self.assistant.generate_daily_briefing()

        # Security
        if any(w in msg for w in ["audit log", "security log", "audit trail", "recent actions"]):
            if not self.audit_log:
                return "Audit system isn't loaded. Check the security module."
            entries = self.audit_log.get_recent(10)
            if not entries:
                return "Audit log's clean — nothing to report."
            lines = ["Recent activity:\n"]
            for e in entries:
                lines.append(f"[{e.get('timestamp', '?')}] **{e.get('action')}** — {e.get('details', '')} ({e.get('severity')})")
            return "\n".join(lines)

        # System skills — AI-classified routing for PC control commands
        # Instead of 100 keyword checks, send to AI for smart classification
        system_hints = [
            "open ", "close ", "launch ", "start ", "kill ", "switch to",
            "go to ", "navigate to ", "pull up ", "show me ",
            "cpu", "ram", "memory", "disk", "storage", "processes", "uptime",
            "ip address", "battery", "temperature", "temp",
            "play", "pause", "next track", "previous track", "volume", "mute",
            "now playing", "what's playing", "music",
            "screenshot", "take a screenshot", "screen",
            "clipboard", "notify", "notification", "lock screen",
            "brightness", "find file", "recent files", "downloads", "trash",
            "wifi", "network", "speed test", "ping",
            "timer", "alarm", "set timer", "set alarm", "remind",
            "search for", "google", "define", "weather",
            "git status", "npm", "pip install", "port",
            "what's eating", "what's hogging", "what's running",
            "gpu", "graphics card", "vram", "cuda",
            "workspace", "tile", "minimize", "maximize", "snap",
            "tab", "browser", "discord", "youtube", "reddit", "twitter",
            "github", "spotify", "netflix", "twitch", "website", "site",
            "schedule", "cron", "scheduled", "remind me every", "every hour",
            "every day", "every morning", "every night", "run every", "recurring",
            # terminal & code
            "run command", "run script", "execute", "shell", "bash ", "terminal command",
            "python ", "run python", "python code", "run code",
            # OCR
            "read the screen", "what's on screen", "whats on screen", "ocr",
            "read screen", "extract text from screen",
            # search
            "search for ", "look up ", "look up", "fast search", "quick search",
            "what is ", "who is ", "define ",
            # notes
            "note ", "notes", "save a note", "write a note", "remember this",
            "my notes", "search notes", "delete note",
            # home assistant (keywords removed — lights handled by pre-router)
            # telegram
            "send telegram", "telegram message", "message on telegram",
        ]
        if any(hint in msg for hint in system_hints):
            skill_result = await self._route_to_system_skill(message)
            if skill_result:
                return skill_result

        return None

    # Common site name → URL, no LLM needed
    _SITE_MAP: dict = {
        "youtube": "https://youtube.com",
        "yt": "https://youtube.com",
        "discord": "https://discord.com",
        "spotify": "https://spotify.com",
        "github": "https://github.com",
        "google": "https://google.com",
        "reddit": "https://reddit.com",
        "twitter": "https://x.com",
        "x": "https://x.com",
        "instagram": "https://instagram.com",
        "insta": "https://instagram.com",
        "twitch": "https://twitch.tv",
        "netflix": "https://netflix.com",
        "amazon": "https://amazon.com",
        "gmail": "https://mail.google.com",
        "calendar": "https://calendar.google.com",
        "drive": "https://drive.google.com",
        "maps": "https://maps.google.com",
        "railway": "https://railway.app",
        "claude": "https://claude.ai",
        "chatgpt": "https://chatgpt.com",
        "notion": "https://notion.so",
        "figma": "https://figma.com",
        "shopify": "https://shopify.com",
        "paypal": "https://paypal.com",
        "shopspark8": "https://shopspark8.com",
        "vape shop": "https://shopspark8.com",
    }

    async def _route_to_system_skill(self, message: str) -> Optional[str]:
        """Route PC control commands — browser agent via OpenClaw, system info via skills."""
        import re as _re
        msg = message.lower().strip()

        # ── Zero-LLM fast paths for most common voice commands ─────────────────
        # Strip common voice prefixes: "hey leon", "can you", "please", etc.
        _clean = _re.sub(
            r'^(?:hey\s+\w+[\s,]+)?(?:can\s+you\s+)?(?:please\s+)?(?:could\s+you\s+)?',
            '', msg
        ).strip()

        # "open/go to/launch {site}" — no LLM needed
        _open_m = _re.match(
            r'^(?:open|go to|launch|navigate to|pull up|bring up)\s+(.+)$', _clean
        )
        if _open_m:
            site_raw = _open_m.group(1).strip().rstrip(".!?")
            # Exact table lookup
            url = self._SITE_MAP.get(site_raw)
            # Desktop apps → open_app, not browser (fixes "open terminal" → terminal.com bug)
            if not url and site_raw in _DESKTOP_APPS:
                logger.info("Keyword pre-route: open_app(%s)", site_raw)
                return await self.system_skills.execute("open_app", {"name": site_raw})
            # Fallback: try appending .com for single bare words
            if not url and " " not in site_raw and "." not in site_raw:
                url = f"https://{site_raw}.com"
            if url:
                logger.info("Fast path: browser_open %s", url)
                return self.openclaw.browser.open_url(url)

        # "volume up/down/set/mute" — no LLM needed
        if _re.search(r'\bvolume\s+up\b', _clean) or "turn it up" in _clean:
            return await self.system_skills.execute("volume_up", {"step": 10})
        if _re.search(r'\bvolume\s+down\b', _clean) or "turn it down" in _clean:
            return await self.system_skills.execute("volume_down", {"step": 10})
        if _re.search(r'\bmute\b', _clean) and "unmute" not in _clean:
            return await self.system_skills.execute("mute", {})
        _vol_pct = _re.search(r'\bvolume\s+(?:to\s+)?(\d+)\s*%?', _clean)
        if _vol_pct:
            return await self.system_skills.execute("volume_set", {"pct": int(_vol_pct.group(1))})

        # "pause/play music" — no LLM needed
        if _re.search(r'\b(pause|play|resume)\b.*\b(music|song|track|video|media)\b', _clean) \
                or _clean in ("pause", "play", "resume"):
            return await self.system_skills.execute("play_pause", {})

        # ── Voice volume (Leon's TTS gain) — handle before AI router ──────────
        _voice_vol_phrases = [
            "your voice volume", "your volume up", "your volume down",
            "turn your voice", "set your voice", "voice louder", "voice quieter",
            "speak louder", "speak quieter", "speak softer", "speak up",
            "talk louder", "talk quieter", "talk softer",
        ]
        if any(p in msg for p in _voice_vol_phrases):
            vs = self.hotkey_listener.voice_system if self.hotkey_listener else None
            if vs:
                import re as _re
                pct_match = _re.search(r'(\d+)\s*%', msg)
                if pct_match:
                    return vs.set_voice_volume(int(pct_match.group(1)))
                elif any(w in msg for w in ("up", "louder", "higher", "more")):
                    new = min(200, int(vs._voice_volume * 100) + 20)
                    return vs.set_voice_volume(new)
                elif any(w in msg for w in ("down", "quieter", "lower", "softer", "less")):
                    new = max(10, int(vs._voice_volume * 100) - 20)
                    return vs.set_voice_volume(new)

        # ── Native reminder fast path (zero-LLM, no OpenClaw) ──────────────────
        # Works on _clean (voice-prefix-stripped) AND raw msg.
        # Uses search() so "can you send me a reminder in 5 min..." still matches.
        _REMIND_RE = _re.compile(
            r'(?:remind(?:er)?|alert|alarm|timer|ping|notify)\s*(?:me\s+)?'
            r'(?:(?:in|for)\s+)?'
            r'(\d+(?:\.\d+)?)\s*(second|sec|minute|min|hour|hr)s?'
            r'(?:\s+(?:to|that|about|for))?\s+([^?!]{3,})',
            _re.IGNORECASE,
        )
        _remind_m = _REMIND_RE.search(_clean) or _REMIND_RE.search(msg)
        if _remind_m:
            import uuid as _uuid, time as _time
            amount = float(_remind_m.group(1))
            unit   = _remind_m.group(2).lower()
            task   = _remind_m.group(3).strip().rstrip(".!?")
            mult   = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600}
            delay  = int(amount * next((v for k, v in mult.items() if unit.startswith(k)), 60))
            mins, secs = divmod(delay, 60)
            time_str = f"{mins}m {secs}s" if mins else f"{secs}s"
            rid = str(_uuid.uuid4())[:8]
            fire_at = _time.time() + delay
            self._pending_reminders[rid] = {"task": task, "fire_at": fire_at}
            self._save_reminders()
            loop = asyncio.get_event_loop()
            loop.call_later(delay, lambda t=task, i=rid: asyncio.ensure_future(self._fire_reminder(t, i)))
            logger.info("Reminder set: '%s' fires in %ds (id=%s)", task, delay, rid)
            return f"Got it — I'll remind you to {task} in {time_str}."

        # Direct cron dispatches — don't burn an AI call for unambiguous cron requests
        cron_list_hints = ["list cron", "cron jobs", "scheduled tasks", "my schedules",
                           "what's scheduled", "whats scheduled", "show schedules",
                           "show cron", "cron list"]
        if any(h in msg for h in cron_list_hints):
            jobs = self.openclaw.cron.list_jobs(include_disabled=True)
            return self.openclaw.cron.format_jobs(jobs)

        # ── Keyword pre-route: skip LLM for unambiguous system commands ────
        for _pattern, _skill, _args in _KEYWORD_ROUTES:
            if _pattern.search(_clean):
                logger.info("Keyword pre-route: %s (no LLM call)", _skill)
                return await self.system_skills.execute(_skill, _args)

        skill_list = self.system_skills.get_skill_list()

        prompt = f"""You are a PC control router for {self.ai_name}. Classify the user's request.

User message: "{message}"

Respond with ONLY valid JSON (no markdown fences):
{{
  "action": "browser_open" | "browser_agent" | "browser_screenshot" | "cron_list" | "cron_add" | "cron_remove" | "cron_run" | "system_skill" | "none",
  "url": "starting URL for browser actions (e.g. https://discord.com)",
  "goal": "natural language goal for browser_agent tasks",
  "skill": "skill_name if system_skill",
  "args": {{}},
  "confidence": 0.0-1.0
}}

Rules:
- "browser_open" = ONLY open a site with no further interaction needed
- "browser_agent" = anything that requires interacting with a page (clicking, typing, searching, filling forms, sending messages, etc.)
- "browser_screenshot" = user wants to see/screenshot the current browser page
- "cron_list" = list scheduled/recurring tasks
- "cron_add" = schedule a new recurring task: args must include name, message, and ONE of: every (e.g. "1h", "30m"), cron (cron expr), at (e.g. "+10m" or ISO)
- "cron_remove" = delete a scheduled task: args must include id
- "cron_run" = run a scheduled task right now: args must include id
- "system_skill" = system info, media control, file search, timers (NOT browser tasks)
- "none" = not a PC control request

Browser examples:
- "open discord" -> {{"action": "browser_open", "url": "https://discord.com", "confidence": 0.99}}
- "open youtube" -> {{"action": "browser_open", "url": "https://youtube.com", "confidence": 0.99}}
- "play love sosa on youtube" -> {{"action": "browser_agent", "url": "https://youtube.com", "goal": "search for Love Sosa, click the first video result to play it, then mark done immediately", "confidence": 0.99}}
- "play [song] on youtube" -> {{"action": "browser_agent", "url": "https://youtube.com", "goal": "search for [song], click the first video result to start playing it, mark done after clicking", "confidence": 0.99}}
- "search youtube for lofi music" -> {{"action": "browser_agent", "url": "https://youtube.com", "goal": "search for lofi music and open the first result", "confidence": 0.99}}
- "google how to make pasta" -> {{"action": "browser_agent", "url": "https://google.com", "goal": "search for how to make pasta", "confidence": 0.99}}
- "send a message on discord" -> {{"action": "browser_agent", "url": "https://discord.com", "goal": "send a message on discord", "confidence": 0.95}}
- "click the subscribe button" -> {{"action": "browser_agent", "url": null, "goal": "click the subscribe button on the current page", "confidence": 0.95}}
- "log into github" -> {{"action": "browser_agent", "url": "https://github.com/login", "goal": "log into github", "confidence": 0.95}}
- "show me the screen" -> {{"action": "browser_screenshot", "confidence": 0.9}}
- "open a new terminal" -> {{"action": "system_skill", "skill": "open_app", "args": {{"name": "terminal"}}, "confidence": 0.95}}

Cron examples:
- "remind me every morning at 9am to check email" -> {{"action": "cron_add", "args": {{"name": "morning email reminder", "message": "remind me to check email", "cron": "0 9 * * *"}}, "confidence": 0.95}}
- "check the weather every hour" -> {{"action": "cron_add", "args": {{"name": "hourly weather", "message": "what's the weather", "every": "1h"}}, "confidence": 0.95}}
- "run a task in 30 minutes" -> {{"action": "cron_add", "args": {{"name": "30min task", "message": "do the task", "at": "+30m"}}, "confidence": 0.95}}
- "show my scheduled tasks" -> {{"action": "cron_list", "confidence": 0.95}}
- "delete cron job abc123" -> {{"action": "cron_remove", "args": {{"id": "abc123"}}, "confidence": 0.95}}

System skill examples:
{skill_list}
- "what's eating my RAM" -> {{"action": "system_skill", "skill": "top_processes", "args": {{"n": 10}}, "confidence": 0.95}}
- "pause the music" -> {{"action": "system_skill", "skill": "play_pause", "args": {{}}, "confidence": 0.95}}
- "turn my volume up" -> {{"action": "system_skill", "skill": "volume_up", "args": {{"step": 10}}, "confidence": 0.99}}
- "turn my volume down" -> {{"action": "system_skill", "skill": "volume_down", "args": {{"step": 10}}, "confidence": 0.99}}
- "set my volume to 50%" -> {{"action": "system_skill", "skill": "volume_set", "args": {{"pct": 50}}, "confidence": 0.99}}
- "mute my pc" -> {{"action": "system_skill", "skill": "mute", "args": {{}}, "confidence": 0.99}}
- "set a timer for 5 minutes" -> {{"action": "system_skill", "skill": "set_timer", "args": {{"minutes": 5, "label": "Timer"}}, "confidence": 0.95}}
- "run ls -la" -> {{"action": "system_skill", "skill": "shell_exec", "args": {{"command": "ls -la"}}, "confidence": 0.95}}
- "run this python: print(2+2)" -> {{"action": "system_skill", "skill": "python_exec", "args": {{"code": "print(2+2)"}}, "confidence": 0.95}}
- "what's on my screen" -> {{"action": "system_skill", "skill": "ocr_screen", "args": {{}}, "confidence": 0.95}}
- "search for what is a black hole" -> {{"action": "system_skill", "skill": "fast_search", "args": {{"query": "what is a black hole"}}, "confidence": 0.9}}
- "save a note: buy milk" -> {{"action": "system_skill", "skill": "note_add", "args": {{"content": "buy milk"}}, "confidence": 0.95}}
- "show my notes" -> {{"action": "system_skill", "skill": "note_list", "args": {{}}, "confidence": 0.95}}
- "search notes for grocery" -> {{"action": "system_skill", "skill": "note_search", "args": {{"query": "grocery"}}, "confidence": 0.95}}
- "turn on the bedroom light" -> {{"action": "system_skill", "skill": "ha_set", "args": {{"entity_id": "light.bedroom", "service": "turn_on"}}, "confidence": 0.9}}
- "what's the bedroom light status" -> {{"action": "system_skill", "skill": "ha_get", "args": {{"entity_id": "light.bedroom"}}, "confidence": 0.9}}
- "send telegram: hey call me" -> {{"action": "system_skill", "skill": "send_telegram", "args": {{"message": "hey call me"}}, "confidence": 0.95}}"""

        result = await self.api.analyze_json(prompt, smart=True)
        if not result:
            return None

        action = result.get("action", "none")
        confidence = result.get("confidence", 0)

        if confidence < 0.7 or action == "none":
            return None

        logger.info(f"PC control: action={action} confidence={confidence}")

        if action == "browser_open":
            url = result.get("url", "")
            if url:
                return self.openclaw.browser.open_url(url)
            return None

        if action == "browser_agent":
            goal = result.get("goal", message)
            url = result.get("url")
            # Google search goals → use real web search (no browser needed)
            _is_google_search = url and "google.com" in url and not _re.search(
                r'log.?in|sign.?in|account|gmail|drive|docs', goal, _re.IGNORECASE
            )
            if _is_google_search:
                return await self._web_search(goal)
            return await self._execute_browser_agent(goal, start_url=url)

        if action == "browser_screenshot":
            path = self.openclaw.browser.screenshot()
            return f"Screenshot saved to {path}." if path else "Couldn't take screenshot."

        if action == "cron_list":
            jobs = self.openclaw.cron.list_jobs(include_disabled=True)
            return self.openclaw.cron.format_jobs(jobs)

        if action == "cron_add":
            import uuid as _uuid, time as _time
            args = result.get("args", {})
            name = args.get("name", f"{self.ai_name} task")
            message = args.get("message", "")
            every = args.get("every")
            cron_expr = args.get("cron")
            at = args.get("at")
            tz = args.get("tz")
            if not message:
                return "Couldn't schedule — no task message specified."

            # For one-time "at" reminders, always prefer the native system (no OpenClaw gateway needed).
            # For recurring (every/cron), try OpenClaw first, fall back to native if it fails.
            if at and not every and not cron_expr:
                # Parse "+Xm" / "+Xs" / "+Xh" duration into seconds
                _dur_m = _re.match(r'^\+?(\d+(?:\.\d+)?)(m|min|minute|s|sec|second|h|hr|hour)', str(at))
                if _dur_m:
                    _amt = float(_dur_m.group(1))
                    _u = _dur_m.group(2)[0]  # first char: m/s/h
                    _delay = int(_amt * {"m": 60, "s": 1, "h": 3600}.get(_u, 60))
                    rid = str(_uuid.uuid4())[:8]
                    fire_at = _time.time() + _delay
                    self._pending_reminders[rid] = {"task": message, "fire_at": fire_at}
                    self._save_reminders()
                    loop = asyncio.get_event_loop()
                    loop.call_later(_delay, lambda t=message, i=rid: asyncio.ensure_future(self._fire_reminder(t, i)))
                    mins, secs = divmod(_delay, 60)
                    time_str = f"{mins}m {secs}s" if mins else f"{secs}s"
                    logger.info("Native reminder (cron fallback): '%s' in %ds", message, _delay)
                    return f"Got it — I'll remind you to {message} in {time_str}."

            job = self.openclaw.cron.add_job(name, message, every=every, cron=cron_expr, at=at, tz=tz)
            if job.get("ok") is False:
                err = job.get("error", "")
                logger.warning("OpenClaw cron failed: %s — dispatching AZ to fix", err[:80])
                asyncio.create_task(self._repair_openclaw_cron(err))
                return f"Couldn't schedule the recurring task right now (OpenClaw issue). I've dispatched Agent Zero to fix it. Try again in a minute."
            job_id = job.get("id", "?")
            sched = every or cron_expr or at or "?"
            return f"Scheduled '{name}' ({sched}) — ID: {job_id}"

        if action == "cron_remove":
            job_id = result.get("args", {}).get("id", "")
            if not job_id:
                return "Which cron job? Tell me the ID (use 'show my scheduled tasks' to list them)."
            return self.openclaw.cron.remove_job(job_id)

        if action == "cron_run":
            job_id = result.get("args", {}).get("id", "")
            if not job_id:
                return "Which cron job? Tell me the ID."
            return self.openclaw.cron.run_now(job_id)

        if action == "system_skill":
            skill_name = result.get("skill")
            args = result.get("args", {})
            if not skill_name:
                return None
            # Skip Home Assistant skills when HA isn't configured — fall through to conversational
            if skill_name in ("ha_set", "ha_get", "ha_list"):
                if not os.environ.get("HA_URL") or not os.environ.get("HA_TOKEN"):
                    return None
            return await self.system_skills.execute(skill_name, args)

        return None
