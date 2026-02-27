"""
Leon Core - The main AI orchestration brain

Leon analyzes user requests, decides whether to respond directly or spawn
Claude Code agents, monitors tasks, and maintains persistent memory.
"""

import asyncio
import json
import logging
import os
import random
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp
import yaml

from .memory import MemorySystem
from .agent_manager import AgentManager
from .task_queue import TaskQueue
from .agent_index import AgentIndex
from .scheduler import TaskScheduler
from .openclaw_interface import OpenClawInterface
from .api_client import AnthropicAPI
from .neural_bridge import (
    BridgeServer, BridgeMessage,
    MSG_TASK_DISPATCH, MSG_TASK_STATUS, MSG_TASK_RESULT,
    MSG_STATUS_REQUEST, MSG_STATUS_RESPONSE, MSG_MEMORY_SYNC,
)
from .system_skills import SystemSkills
from .hotkey_listener import HotkeyListener
from .screen_awareness import ScreenAwareness
from .notifications import NotificationManager, Priority
from .project_watcher import ProjectWatcher
from .night_mode import NightMode
from .plan_mode import PlanMode
from .routing_mixin import RoutingMixin
from .browser_mixin import BrowserMixin
from .task_mixin import TaskMixin
from .awareness_mixin import AwarenessMixin
from .reminder_mixin import ReminderMixin

logger = logging.getLogger("leon")


# ── Self-repair detection ─────────────────────────────────────────────────────

# Things that map to specific Leon subsystems / files
_COMPONENT_MAP = {
    "screenshot": ("screenshot system", "integrations/discord/bot.py — _take_screenshot()"),
    "screen shot": ("screenshot system", "integrations/discord/bot.py — _take_screenshot()"),
    "screen capture": ("screenshot system", "integrations/discord/bot.py — _take_screenshot()"),
    "black box": ("screenshot system", "integrations/discord/bot.py — capturing black image on Wayland"),
    "black image": ("screenshot system", "integrations/discord/bot.py — capturing black image on Wayland"),
    "black screen": ("screenshot system", "integrations/discord/bot.py — capturing black image on Wayland"),
    "voice": ("voice system", "core/voice.py"),
    "speak": ("voice system", "core/voice.py — TTS"),
    "tts": ("voice system", "core/voice.py — TTS output"),
    "hear": ("voice system", "core/voice.py — STT input"),
    "listen": ("voice system", "core/voice.py — STT input"),
    "memory": ("memory system", "core/memory.py"),
    "remember": ("memory system", "core/memory.py"),
    "forget": ("memory system", "core/memory.py"),
    "discord": ("Discord integration", "integrations/discord/bot.py"),
    "message": ("Discord integration", "integrations/discord/bot.py"),
    "agent": ("agent system", "core/agent_manager.py"),
    "task": ("task system", "core/leon.py — task routing"),
    "schedule": ("scheduler", "core/scheduler.py"),
    "reminder": ("reminder system", "core/leon.py — _fire_reminder / OpenClaw cron"),
    "remind": ("reminder system", "core/leon.py — _fire_reminder / OpenClaw cron"),
    "cron": ("cron/scheduler", "~/.openclaw/openclaw.json and OpenClaw gateway"),
    "openclaw": ("OpenClaw", "~/.openclaw/openclaw.json"),
    "dashboard": ("dashboard", "dashboard/server.py"),
    "response": ("response quality", "core/leon.py — _analyze_request / _respond_conversationally"),
    "routing": ("task routing", "core/leon.py — _route_special_commands"),
    "plan": ("plan mode", "core/plan_mode.py"),
}

_NEGATIVE_WORDS = {
    "broken", "wrong", "bad", "stupid", "not working", "doesn't work",
    "didnt work", "didn't work", "failed", "messed up", "useless",
    "terrible", "awful", "trash", "garbage", "black", "blank",
    "not right", "incorrect", "buggy", "error", "crash", "weird",
    "dumb", "useless", "fix", "repair",
}

_SELF_REPAIR_PATTERNS = [
    # Direct commands
    "fix your", "fix yourself", "fix leon", "code yourself",
    "fix your own", "improve yourself", "optimize yourself",
    "review your code", "audit your code", "check your code",
    # Criticism patterns
    "you're broken", "you are broken", "you're bad", "you are bad",
    "you're stupid", "you are stupid", "you are dumb", "you're dumb",
    "you sent a black", "you sent me a black", "sent me a black", "sent a black",
    "that was wrong", "that was bad", "that was stupid",
    "that didn't work", "that didnt work", "that's not right",
    "thats not right", "that's wrong", "thats wrong",
    "you failed", "you messed up", "you couldn't even",
    # "your X is broken" variants
    "your screenshot", "your screen", "your voice", "your memory",
    "your response", "your agent", "your code", "your system",
]


def _detect_self_repair(msg: str) -> tuple[bool, str, str]:
    """
    Detect whether the user is asking Leon to fix something about itself.
    Returns: (is_self_repair, component_label, file_hint)

    Catches natural variants:
      "your screenshot is broken fix it"   → screenshot system
      "you sent me a black box"            → screenshot system
      "that was wrong"                     → last action (from context)
      "you're stupid"                      → general behavior
      "code yourself better"               → general self-improve
    """
    m = msg.lower()

    # Check direct patterns first
    if any(p in m for p in _SELF_REPAIR_PATTERNS):
        component, file_hint = _extract_component(m)
        issue = _extract_issue(m)
        return True, component, file_hint

    # "your [X] [is/was] [negative]"
    if "your" in m and any(neg in m for neg in _NEGATIVE_WORDS):
        component, file_hint = _extract_component(m)
        issue = _extract_issue(m)
        return True, component, file_hint

    return False, "", ""


def _extract_component(msg: str) -> tuple[str, str]:
    """Return (component_label, file_hint) for the most relevant component."""
    for keyword, (label, hint) in _COMPONENT_MAP.items():
        if keyword in msg:
            return label, hint
    return "general behavior", "core/leon.py"


def _extract_issue(msg: str) -> str:
    """Pull a short description of what the issue is from the user's message."""
    # Keep it simple — just return the raw message trimmed
    return msg[:200].strip()


class Leon(RoutingMixin, BrowserMixin, TaskMixin, AwarenessMixin, ReminderMixin):
    """Main orchestration system - the brain that coordinates everything.

    Routing, browser automation, task orchestration, awareness loop, and reminders
    are implemented in their respective mixin files (core/*_mixin.py).
    """

    def __init__(self, config_path: str = "config/settings.yaml"):
        logger.info("Initializing Leon...")

        try:
            with open(config_path, "r") as f:
                self.config = yaml.safe_load(f)
        except FileNotFoundError:
            logger.critical(f"Config file not found: {config_path}")
            raise SystemExit(f"Missing config: {config_path}")
        except yaml.YAMLError as e:
            logger.critical(f"Invalid YAML in {config_path}: {e}")
            raise SystemExit(f"Bad config: {e}")

        # Security system — load early so vault is available for API key
        try:
            from security.vault import SecureVault, OwnerAuth, AuditLog, PermissionSystem
            self.audit_log = AuditLog("data/audit.log")
            self.vault = SecureVault("data/.vault.enc")
            self.owner_auth = OwnerAuth("data/.auth.json")
            self.permissions = PermissionSystem(self.audit_log)
        except Exception as e:
            logger.error(f"Security module failed to load: {e}")
            self.audit_log = self.vault = self.owner_auth = self.permissions = None

        # Core components
        self.memory = MemorySystem(self.config["leon"]["memory_file"])
        self.openclaw = OpenClawInterface(self.config["openclaw"]["config_path"])
        self.agent_manager = AgentManager(self.openclaw, self.config["agents"])
        self.task_queue = TaskQueue(self.config["agents"]["max_concurrent"])
        self.agent_index = AgentIndex("data/agent_index.json")
        scheduler_tasks = self.config.get("scheduler", {}).get("tasks", [])
        self.scheduler = TaskScheduler(scheduler_tasks)

        # API client — try vault for API key if env var is empty
        self.api = AnthropicAPI(self.config["api"], vault=self.vault)

        # If vault has API key, also set env so agent spawning works
        if self.vault and self.vault._unlocked:
            vault_api_key = self.vault.retrieve("ANTHROPIC_API_KEY")
            if vault_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
                os.environ["ANTHROPIC_API_KEY"] = vault_api_key
                logger.info("ANTHROPIC_API_KEY loaded from vault into environment")

        if not os.environ.get("ANTHROPIC_API_KEY"):
            logger.warning("ANTHROPIC_API_KEY not configured — use /setkey in dashboard or set env var")

        # Load user config (written by setup wizard, git-ignored)
        BASE_DIR = Path(config_path).parent.parent
        user_cfg_path = BASE_DIR / "config" / "user_config.yaml"
        if user_cfg_path.exists():
            try:
                ucfg = yaml.safe_load(user_cfg_path.read_text()) or {}
                self.ai_name = ucfg.get("ai_name") or self.config.get("leon", {}).get("name") or "AI"
                self.owner_name = ucfg.get("owner_name", "User")
                # Use direct assignment so user_config.yaml always wins over .env
                if ucfg.get("groq_api_key"):
                    os.environ["GROQ_API_KEY"] = ucfg["groq_api_key"]
                if ucfg.get("elevenlabs_api_key"):
                    os.environ["ELEVENLABS_API_KEY"] = ucfg["elevenlabs_api_key"]
                if ucfg.get("elevenlabs_voice_id"):
                    os.environ["LEON_VOICE_ID"] = ucfg["elevenlabs_voice_id"]
                if ucfg.get("claude_api_key") and ucfg.get("claude_auth") == "api":
                    os.environ["ANTHROPIC_API_KEY"] = ucfg["claude_api_key"]
            except Exception as e:
                logger.warning(f"Could not load user_config.yaml: {e}")
                self.ai_name = self.config.get("leon", {}).get("name") or "AI"
                self.owner_name = "User"
        else:
            self.ai_name = self.config.get("leon", {}).get("name") or "AI"
            self.owner_name = "User"

        # Load personality
        personality_file = self.config["leon"]["personality_file"]
        try:
            with open(personality_file, "r") as f:
                personality_text = f.read()
            # Apply user-defined name substitutions
            personality_text = personality_text.replace("{AI_NAME}", self.ai_name)
            personality_text = personality_text.replace("{OWNER_NAME}", self.owner_name)
            personality = yaml.safe_load(personality_text)
            self.system_prompt = personality["system_prompt"]
            self._wake_responses = personality.get("wake_responses", ["Yeah?"])
            self._task_complete_phrases = personality.get("task_complete", ["Done."])
            self._task_failed_phrases = personality.get("task_failed", ["That didn't work — {error}."])
            self._error_translations = personality.get("error_translations", {})
        except FileNotFoundError:
            logger.warning(f"Personality file not found: {personality_file}, using default")
            self.system_prompt = f"You are {self.ai_name}, a helpful AI assistant and orchestrator."
            self._wake_responses = ["Yeah?"]
            self._task_complete_phrases = ["Done."]
            self._task_failed_phrases = ["That didn't work — {error}."]
            self._error_translations = {}

        # Load projects
        projects_file = self.config.get("leon", {}).get("projects_file", "config/projects.yaml")
        if Path(projects_file).exists():
            try:
                with open(projects_file, "r") as f:
                    self.projects_config = yaml.safe_load(f) or {}
            except (yaml.YAMLError, json.JSONDecodeError):
                self.projects_config = {"projects": []}
        else:
            self.projects_config = {"projects": []}

        # Hardware — 3D printing
        try:
            from hardware.printing import PrinterManager, STLSearcher
            printer_config = "config/printers.yaml"
            self.printer = PrinterManager(printer_config) if Path(printer_config).exists() else None
            self.stl_searcher = STLSearcher()
        except Exception as e:
            logger.warning(f"3D printing module not available: {e}")
            self.printer = None
            self.stl_searcher = None

        # Vision system — disabled by default (CPU intensive, opt-in via settings.yaml)
        _vision_enabled = self.config.get("vision", {}).get("enabled", False)
        if _vision_enabled:
            try:
                from vision.vision import VisionSystem
                self.vision = VisionSystem(api_client=self.api, analysis_interval=3.0)
            except Exception as e:
                logger.warning(f"Vision module not available: {e}")
                self.vision = None
        else:
            self.vision = None

        # Business modules
        try:
            from business.crm import CRM
            from business.finance import FinanceTracker
            from business.comms import CommsHub
            from business.leads import LeadGenerator
            from business.assistant import PersonalAssistant
            self.crm = CRM()
            self.finance = FinanceTracker()
            self.comms = CommsHub()
            self.leads = LeadGenerator(crm=self.crm, api_client=self.api)
            self.assistant = PersonalAssistant(
                crm=self.crm, finance=self.finance, comms=self.comms,
                memory=self.memory, api_client=self.api,
            )
        except Exception as e:
            logger.error(f"Business modules failed to load: {e}")
            self.crm = self.finance = self.comms = self.leads = self.assistant = None

        # System skills — PC control (apps, media, desktop, files, etc.)
        self.system_skills = SystemSkills()

        # Hotkey listener — push-to-talk and voice toggle
        voice_cfg = self.config.get("voice", {})
        ptt_key = voice_cfg.get("push_to_talk_key", "scroll_lock")
        self.hotkey_listener = HotkeyListener(ptt_key=ptt_key)

        # Notification manager — unified desktop alerts
        self.notifications = NotificationManager()

        # Project watcher — monitor file changes in configured projects
        self.project_watcher = ProjectWatcher(
            self.projects_config.get("projects", [])
        )

        # Screen awareness — monitor what user is doing
        self.screen_awareness = ScreenAwareness(
            api_client=self.api,
            on_insight=self._handle_screen_insight,
            interval=self.config.get("system", {}).get("screen_interval", 30),
        )

        # Brain role: unified (single PC), left (main PC), right (homelab)
        self.brain_role = (
            os.environ.get("LEON_BRAIN_ROLE")
            or self.config.get("leon", {}).get("brain_role", "unified")
        )

        # Neural bridge (Left Brain only)
        self.bridge: Optional[BridgeServer] = None
        self._bridge_connected = False
        self._right_brain_status: dict = {}
        if self.brain_role == "left":
            bridge_config = self.config.get("bridge", {})
            # Load bridge token from env var or vault
            bridge_token = os.environ.get("LEON_BRIDGE_TOKEN", bridge_config.get("token", ""))
            if not bridge_token and self.vault and self.vault._unlocked:
                bridge_token = self.vault.retrieve("LEON_BRIDGE_TOKEN")
                if not bridge_token:
                    # Generate and store a new bridge token
                    import secrets as _secrets
                    bridge_token = _secrets.token_hex(32)
                    self.vault.store("LEON_BRIDGE_TOKEN", bridge_token)
                    logger.info("Generated new bridge token and stored in vault")
            if bridge_token:
                bridge_config["token"] = bridge_token
            self.bridge = BridgeServer(bridge_config)
            self.bridge.on(MSG_TASK_STATUS, self._handle_remote_task_status)
            self.bridge.on(MSG_TASK_RESULT, self._handle_remote_task_result)

        # Feature detection — set flags for optional components
        self._openclaw_available = (Path.home() / ".openclaw" / "bin" / "openclaw").exists()
        self._claude_cli_available = bool(__import__("shutil").which("claude"))
        if not self._openclaw_available:
            logger.info("OpenClaw not installed — browser automation and skills unavailable")
        if not self._claude_cli_available:
            logger.warning("claude CLI not found — agent spawning will fail. Install from https://claude.ai/download")

        self.running = False
        self._awareness_task = None
        self._ram_watchdog_task = None
        self._vision_task = None
        self._last_discord_update = 0.0   # timestamp of last proactive Discord update
        # Overnight autonomous coding mode
        self.night_mode = NightMode(self)
        # Structured multi-phase plan execution
        self.plan_mode = PlanMode(self)

        # Update checker
        from .update_checker import UpdateChecker
        _update_cfg = self.config.get("update_check", {})
        _repo = _update_cfg.get("repo", "")
        _version_file = Path(config_path).parent.parent / "VERSION"
        _current_version = _version_file.read_text().strip() if _version_file.exists() else "0.0.0"
        _placeholder = "GITHUB_USERNAME/REPO_NAME"
        if _repo and _repo != _placeholder and _update_cfg.get("enabled", True):
            self.update_checker = UpdateChecker(_repo, _current_version)
        else:
            self.update_checker = None
        self._update_available = False
        self._update_mentioned = False
        self._last_update_check: float = 0.0
        self._update_interval = _update_cfg.get("check_interval_hours", 12) * 3600

        # Persistent reminders — keyed by id: {"task": str, "fire_at": float}
        self._pending_reminders: dict = {}

        logger.info(f"Leon initialized — brain_role={self.brain_role}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        self.running = True

        # Run health checks before anything else
        self._run_health_checks()

        # Start bridge server if Left Brain
        if self.bridge:
            await self.bridge.start()
            logger.info("Neural bridge server started (Left Brain mode)")

        self._awareness_task = asyncio.create_task(self._awareness_loop())
        self._ram_watchdog_task = asyncio.create_task(self._ram_watchdog())

        # Start vision if camera available
        if self.vision:
            try:
                self.vision.start()
                self._vision_task = asyncio.create_task(self.vision.run_analysis_loop())
                logger.info("Vision system active")
            except Exception as e:
                logger.warning(f"Vision system not started: {e}")

        if self.audit_log:
            self.audit_log.log("system_start", "Leon started", "info")

        # Lock down sensitive file permissions
        sensitive_files = [
            "data/.vault.enc",
            "data/.vault.salt",
            "data/.auth.json",
            "config/settings.yaml",
            "data/leon_memory.json",
        ]
        for fpath in sensitive_files:
            p = Path(fpath)
            if p.exists():
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass

        # Start hotkey listener (push-to-talk, voice toggle)
        try:
            loop = asyncio.get_event_loop()
            self.hotkey_listener.start(loop)
            logger.info("Hotkey listener active — push-to-talk ready")
        except Exception as e:
            logger.warning(f"Hotkey listener failed to start: {e}")

        # Start project watcher
        try:
            self.project_watcher.start()
            logger.info("Project watcher active")
        except Exception as e:
            logger.warning(f"Project watcher failed: {e}")

        # Start notification manager
        await self.notifications.start()

        # Start screen awareness
        try:
            await self.screen_awareness.start()
            logger.info("Screen awareness active")
        except Exception as e:
            logger.warning(f"Screen awareness failed to start: {e}")

        # Auto-trigger daily briefing on startup
        asyncio.create_task(self._auto_daily_briefing())

        # Resume night mode — always enable if there are pending tasks (including recovered ones)
        pending = self.night_mode.get_pending()
        if pending:
            async def _resume_night():
                await asyncio.sleep(15)  # wait for bridge to connect first
                await self.night_mode.enable()
                logger.info(f"Night mode auto-resumed: {len(pending)} pending task(s) dispatching")
            asyncio.create_task(_resume_night())

        # Clear stale active_tasks — any tasks from previous Leon process are dead
        self.memory.memory["active_tasks"] = {}
        self.memory.save(force=True)
        logger.info("Cleared stale active_tasks from memory on startup")

        # Restore pending reminders (reschedule any that survived a restart)
        self._load_reminders()

        # Resume plan mode — if a plan was mid-execution when Leon last stopped, pick it up
        saved_plan = self.plan_mode.load_saved_plan()
        if saved_plan and saved_plan.get("status") == "executing":
            async def _resume_plan():
                await asyncio.sleep(10)
                # Reset any "running" tasks back to "pending" — their agents died with the restart
                for phase in saved_plan.get("phases", []):
                    for task in phase.get("tasks", []):
                        if task.get("status") == "running":
                            task["status"] = "pending"
                            task["agent_id"] = None
                self.plan_mode._save_plan(saved_plan)
                # Find the project from the plan
                proj_name = saved_plan.get("project", "")
                project = self._resolve_project(proj_name, proj_name)
                if not project:
                    projects = self.projects_config.get("projects", [])
                    project = projects[0] if projects else None
                if project:
                    logger.info(f"Resuming plan '{saved_plan.get('goal','')[:60]}' from restart")
                    await self.plan_mode._execute_plan(saved_plan, project)
                    saved_plan["status"] = "complete"
                    self.plan_mode._save_plan(saved_plan)
                    self.plan_mode._active = False
            self.plan_mode._active = True
            self.plan_mode.current_plan = saved_plan
            asyncio.create_task(_resume_plan())

        logger.info("Leon is now running — all systems active")

    def _run_health_checks(self):
        """Validate system state on startup. Warns but doesn't block."""
        import socket
        checks_passed = 0
        checks_total = 0

        def check(name: str, ok: bool, warn_msg: str = ""):
            nonlocal checks_passed, checks_total
            checks_total += 1
            if ok:
                checks_passed += 1
                logger.info(f"  [OK] {name}")
            else:
                logger.warning(f"  [!!] {name}: {warn_msg}")

        logger.info("Running startup health checks...")

        # 1. Config file exists and is valid
        check("Config loaded", bool(self.config), "settings.yaml failed to load")

        # 2. Required data directories
        for d in ["data", "data/task_briefs", "data/agent_outputs", "logs"]:
            p = Path(d)
            check(f"Directory {d}", p.is_dir(), f"missing — creating")
            if not p.is_dir():
                p.mkdir(parents=True, exist_ok=True)

        # 3. Check ports aren't already in use
        for port, name in [(3000, "Dashboard"), (9100, "Bridge")]:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.settimeout(0.5)
                result = sock.connect_ex(("127.0.0.1", port))
                in_use = result == 0
            except Exception:
                in_use = False
            finally:
                sock.close()
            check(f"Port {port} ({name}) available", not in_use,
                  f"port {port} already in use — another Leon instance?")

        # 4. Security modules
        check("Vault loaded", self.vault is not None, "security/vault.py failed to import")
        check("Audit log loaded", self.audit_log is not None, "audit log unavailable")
        check("Permissions loaded", self.permissions is not None, "permission system unavailable")

        # 5. API key configured
        has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        check("API key configured", has_api_key,
              "ANTHROPIC_API_KEY not set — use /setkey in dashboard")

        # 6. Sensitive file permissions
        for fpath in ["data/.vault.enc", "data/.auth.json", "config/settings.yaml"]:
            p = Path(fpath)
            if p.exists():
                mode = oct(p.stat().st_mode)[-3:]
                check(f"{fpath} permissions ({mode})", mode == "600",
                      f"expected 600, got {mode}")

        # 7. Memory file
        mem_file = Path(self.config.get("leon", {}).get("memory_file", "data/leon_memory.json"))
        check("Memory file accessible", mem_file.parent.is_dir(), f"parent dir missing")

        logger.info(f"Health checks: {checks_passed}/{checks_total} passed")

    async def stop(self):
        logger.info("Stopping Leon...")
        self.running = False
        if self._awareness_task:
            self._awareness_task.cancel()
        if self._ram_watchdog_task:
            self._ram_watchdog_task.cancel()
        if self._vision_task:
            self._vision_task.cancel()
        if self.vision:
            self.vision.stop()
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        if self.project_watcher:
            self.project_watcher.stop()
        if self.screen_awareness:
            await self.screen_awareness.stop()
        if self.notifications:
            await self.notifications.stop()
        if self.night_mode and self.night_mode.active:
            await self.night_mode.disable()
        if self.bridge:
            await self.bridge.stop()
        if self.vault:
            self.vault.lock()
        if self.audit_log:
            self.audit_log.log("system_stop", "Leon stopped", "info")
        # Force-flush memory (bypasses debounce) to ensure no data is lost
        self.memory.save(force=True)
        logger.info("Leon stopped")

    async def _auto_daily_briefing(self):
        """Run daily briefing on startup and push to dashboard feed."""
        try:
            if not self.assistant:
                return
            briefing = await self.assistant.generate_daily_briefing()
            if briefing:
                logger.info("Auto daily briefing generated")
                await self._broadcast_to_dashboard({
                    "type": "input_response",
                    "message": f"Daily Briefing:\n{briefing}",
                    "timestamp": datetime.now().strftime("%H:%M"),
                })
        except Exception as e:
            logger.warning(f"Auto daily briefing failed: {e}")

    # ------------------------------------------------------------------
    # Main input handler
    # ------------------------------------------------------------------

    async def process_voice_input(self, message: str) -> str:
        """
        Fast path for voice messages — skips the classify→respond double-LLM round trip.

        For voice, commands are almost always simple PC control or quick questions.
        We go directly to _respond_conversationally (one LLM call instead of two),
        cutting latency roughly in half vs. process_user_input.

        Complex dev commands ("build the whole app", "spin up an agent") still work
        because the system prompt tells Leon how to respond; the user can always type
        those for the full agent-dispatch pipeline.
        """
        logger.info(f"Voice: {message[:80]}...")
        self.memory.add_conversation(message, role="user")

        # Hard permission check (same as full pipeline)
        if self.permissions:
            denied = self._check_sensitive_permissions(message)
            if denied:
                self.memory.add_conversation(denied, role="assistant")
                return denied

        # Special command routing first — PC control, night mode, etc. (zero LLM calls)
        routed = await self._route_special_commands(message)
        if routed:
            response = self._strip_sir(routed)
            self.memory.add_conversation(response, role="assistant")
            asyncio.create_task(self._extract_memory(message, response))
            return response

        # Everything else: single LLM call (no classify→respond double-tap)
        response = await self._respond_conversationally(message)
        response = self._strip_sir(response)
        self.memory.add_conversation(response, role="assistant")
        asyncio.create_task(self._extract_memory(message, response))
        return response

    async def process_user_input(self, message: str) -> str:
        """
        Main entry point for user messages.
        Decides whether to respond directly or spawn agents.
        """
        logger.info(f"User: {message[:80]}...")
        self.memory.add_conversation(message, role="user")

        # Self-update command
        _msg_lower = message.lower().strip()
        _update_triggers = ["update yourself", "update yourself.", "git pull", "check for updates",
                            f"update {self.ai_name.lower()}", "pull latest", "pull updates"]
        if any(_msg_lower == t or _msg_lower.startswith(t) for t in _update_triggers):
            return await self._self_update()

        # Permission check for sensitive actions
        if self.permissions:
            denied = self._check_sensitive_permissions(message)
            if denied:
                self.memory.add_conversation(denied, role="assistant")
                return denied

        # Pre-router: lights — fast path, no LLM needed
        try:
            from tools.lights import parse_and_execute as _lights_parse
            _light_resp = _lights_parse(message)
            if _light_resp is not None:
                self.memory.add_conversation(_light_resp, role="assistant")
                return _light_resp
        except Exception as _le:
            logger.warning("Lights pre-router error: %s", _le)

        # Pre-router: explicit web search intent — bypass LLM router entirely
        import re as _re
        # Catches "look it up", "google X", "search for X", "find out about X"
        # Must have an explicit search verb so we don't intercept trivia questions
        _SEARCH_PRE = _re.compile(
            r'\b(look\s+it\s+up|look\s+up|google\s+(?:it|that|\w)|search\s+(?:for|the\s+web|online)|'
            r'find\s+(?:out|info|information)\s+(?:on|about)|research\s+\w|browse\s+for|'
            r'web\s+search|search\s+online)\b',
            _re.IGNORECASE,
        )
        if _SEARCH_PRE.search(message):
            # Strip the search verb to get the actual query
            _query = _re.sub(
                r'\b(look\s+it\s+up|look\s+up|google\s+(?:it|that)?|search\s+(?:for|the\s+web|online)?|'
                r'find\s+(?:out|info|information)?\s*(?:on|about)?|research|browse\s+for|'
                r'web\s+search|search\s+online|can\s+you|please|for\s+me)\b',
                ' ', message, flags=_re.IGNORECASE,
            ).strip().strip('.,?!')
            _query = _query if len(_query) > 3 else message
            response = await self._web_search(_query)
            self.memory.add_conversation(response, role="assistant")
            return response

        # Analyze what the user wants
        analysis = await self._analyze_request(message)

        # Plan mode — LLM detected a large autonomous build goal
        if analysis and analysis.get("type") == "plan":
            routed = await self._route_special_commands(message)
            response = routed if routed else await self._handle_plan_request(message, analysis)
        else:
            # Check for special command routing (night mode, plan status/cancel, etc.)
            routed = await self._route_special_commands(message)
            if routed:
                response = routed
            elif analysis is None or analysis.get("type") == "simple":
                response = await self._respond_conversationally(message)
            elif analysis.get("type") == "device_control":
                # Retry lights pre-router — fuzzy matching catches transcription errors
                try:
                    from tools.lights import parse_and_execute as _lights_parse
                    _light_resp = _lights_parse(message)
                    if _light_resp:
                        response = _light_resp
                    else:
                        response = "I heard a device control command but couldn't identify the device. Try saying it again — for example: 'turn lab ceiling on' or 'lights off'."
                except Exception:
                    response = "I heard a device control command but couldn't identify the device. Try saying it again — for example: 'turn lab ceiling on' or 'lights off'."
            elif analysis.get("type") == "single_task":
                response = await self._handle_single_task(message, analysis)
            else:
                response = await self._orchestrate(message, analysis)

        # Hard filter — strip any "sir" the LLM snuck in, no exceptions
        response = self._strip_sir(response)

        # Append one-time update notice
        if self._update_available and not self._update_mentioned and self.update_checker:
            self._update_mentioned = True
            response += (
                f"\n\nAlso — there's a new update available (v{self.update_checker.latest_version}). "
                f"Run `git pull` in the leon-system folder to grab it. "
                f"{self.update_checker.release_url}"
            )

        self.memory.add_conversation(response, role="assistant")

        # Self-memory: passively extract facts worth remembering
        asyncio.create_task(self._extract_memory(message, response))

        return response

    async def _extract_memory(self, user_msg: str, response: str):
        """Background task: extract and persist any notable facts from this exchange."""
        msg_lower = user_msg.lower()

        # Explicit "remember" triggers — high confidence, store immediately
        remember_triggers = ["remember that", "remember my", "my name is", "i am ",
                             "i prefer ", "i like ", "i hate ", "i always ", "i never ",
                             "my favorite", "my email is", "my phone", "note that",
                             "keep in mind", "don't forget"]
        if any(t in msg_lower for t in remember_triggers):
            prompt = (
                f"The user said: \"{user_msg}\"\n\n"
                "Extract the key fact to remember as a short JSON object:\n"
                "{\"key\": \"short_key\", \"value\": \"fact to remember\"}\n"
                "Only respond with JSON. If nothing is worth remembering, return {}."
            )
            result = await self.api.analyze_json(prompt)
            if result and result.get("key") and result.get("value"):
                self.memory.learn(result["key"], result["value"])
                logger.info(f"Self-memory: learned '{result['key']}' = '{result['value']}'")

    # ------------------------------------------------------------------
    # Special command routing — see core/routing_mixin.py
    # Browser automation — see core/browser_mixin.py
    # ------------------------------------------------------------------

    # _route_special_commands → routing_mixin.py
    # _SITE_MAP               → routing_mixin.py
    # _route_to_system_skill  → routing_mixin.py
    # _web_search             → browser_mixin.py
    # _execute_browser_agent  → browser_mixin.py

    # (routing and browser methods removed — see routing_mixin.py / browser_mixin.py)

    def _check_sensitive_permissions(self, message: str) -> Optional[str]:
        """Check if the message requests a sensitive action that needs approval."""
        msg = message.lower()

        # Map keywords to permission actions
        # Note: delete_files removed — agents run with --dangerously-skip-permissions already
        checks = [
            (["purchase", "buy", "order", "checkout"], "make_purchase"),
            (["transfer money", "send money", "pay ", "wire "], "send_money"),
            (["post publicly", "tweet", "publish"], "post_publicly"),
        ]

        for keywords, action in checks:
            if any(kw in msg for kw in keywords):
                if not self.permissions.check_permission(action):
                    return (
                        f"I'll need your go-ahead for that. "
                        f"Run `/approve {action}` to unlock it."
                    )
        return None

    def _build_help_text(self) -> str:
        """Build a help text listing all available modules and commands."""
        modules = []
        modules.append("**Available Modules:**\n")
        modules.append("- **Daily Briefing** — \"daily briefing\", \"brief me\", \"catch me up\"")
        modules.append("- **CRM** — \"pipeline\", \"clients\", \"deals\"")
        modules.append("- **Finance** — \"revenue\", \"invoice\", \"earnings\"")
        modules.append("- **Leads** — \"find leads\", \"prospect\", \"generate leads\"")
        modules.append("- **Comms** — \"check email\", \"inbox\", \"send email\"")
        if self.printer:
            modules.append("- **3D Printing** — \"printer status\", \"find stl\"")
        if self.vision:
            modules.append("- **Vision** — \"what do you see\", \"look at\"")
        modules.append("- **Security** — \"audit log\", \"security log\"")
        modules.append("\n**System Skills** (natural language — AI-routed):")
        modules.append("- **App Control** — \"open firefox\", \"close spotify\", \"switch to code\"")
        modules.append("- **System Info** — \"CPU usage\", \"RAM\", \"disk space\", \"top processes\"")
        modules.append("- **Media** — \"pause\", \"next track\", \"volume up\", \"now playing\"")
        modules.append("- **Desktop** — \"screenshot\", \"lock screen\", \"brightness up\"")
        modules.append("- **Files** — \"find file\", \"recent downloads\", \"trash\"")
        modules.append("- **Network** — \"wifi status\", \"my IP\", \"ping google\"")
        modules.append("- **Timers** — \"set timer 5 minutes\", \"set alarm 7:00\"")
        modules.append("- **Web** — \"search for X\", \"weather\", \"define Y\"")
        modules.append("- **Dev** — \"git status\", \"port check 3000\"")
        modules.append("\n**Dashboard Commands** (type / in command bar):")
        modules.append("- `/agents` — list active agents")
        modules.append("- `/status` — system overview")
        modules.append("- `/queue` — queued tasks")
        modules.append("- `/kill <id>` — terminate agent")
        modules.append("- `/retry <id>` — retry failed agent")
        modules.append("- `/history` — recent completed tasks")
        modules.append("- `/bridge` — Right Brain connection status")
        modules.append("- `/plan` — show active plan status")
        modules.append("\n**Plan Mode:**")
        modules.append("- \"plan and build [goal]\" — generate + execute a multi-phase plan")
        modules.append("- \"plan status\" — check plan progress")
        modules.append("- \"cancel plan\" — stop execution (agents finish current task)")
        return "\n".join(modules)

    # ------------------------------------------------------------------
    # Request analysis
    # ------------------------------------------------------------------

    async def _analyze_request(self, message: str) -> Optional[dict]:
        """Use the API to classify and decompose the user's request."""
        # Build context from memory
        active_tasks = self.memory.get_all_active_tasks()
        projects = self.memory.list_projects()
        project_names = [p['name'] for p in projects] if projects else []

        prompt = f"""Analyze this user request and classify it.

User message: "{message}"

Current active tasks: {json.dumps(list(active_tasks.values()), default=str) if active_tasks else "None"}
Known projects: {json.dumps(project_names) if project_names else "None"}

Respond with ONLY valid JSON (no markdown fences):
{{
  "type": "simple" | "device_control" | "single_task" | "multi_task" | "plan",
  "tasks": ["description of each discrete task"],
  "projects": ["project name for each task or 'unknown'"],
  "complexity": 1-10,
  "plan_goal": "concise goal if type is plan, else null",
  "plan_project": "project name if type is plan, else null"
}}

Rules:
- "simple" = status question, quick answer, clarification, greeting, asking what you did
- "device_control" = request to control a physical device — lights, switches, thermostat, TV, fan, speaker volume, smart plug. Even if garbled or misspelled. Do NOT classify as single_task — it should never spawn a coding agent.
- "single_task" = one focused coding/research task
- "multi_task" = 2+ distinct tasks that can be parallelized
- "plan" = user wants a large, multi-hour autonomous build — they want you to take over a whole project or goal and execute it fully without interruption. Detect this from intent, not exact phrases. Examples that should classify as "plan": "go ham on X", "just go build it", "make it production ready", "work through the whole thing", "do everything needed to launch X", "take it from here and run with it", "build out the whole feature", "overhaul X completely", "just fix everything wrong with it"

For "plan" type, set plan_goal to a precise one-line description of what should be achieved, and plan_project to the most relevant known project name (or 'unknown')."""

        result = await self.api.analyze_json(prompt)
        if result:
            logger.info(f"Analysis: type={result.get('type')}, tasks={len(result.get('tasks', []))}")
        return result

    # ------------------------------------------------------------------
    # Response strategies
    # ------------------------------------------------------------------


    async def _respond_conversationally(self, message: str) -> str:
        """Direct API response for simple queries - no agent needed."""
        logger.info("Responding conversationally")

        # Build context
        recent = self.memory.get_recent_context(limit=20)
        active = self.memory.get_all_active_tasks()
        projects = self.memory.list_projects()

        # Inject memory context into system prompt
        vision_desc = self.vision.describe_scene() if self.vision and self.vision._running else "Vision inactive"

        learned = self.memory.memory.get("learned_context", {})
        learned_str = "\n".join(f"  {k}: {v}" for k, v in learned.items()) if learned else "None"

        now = datetime.now()
        context_block = f"""
## Current Time
{now.strftime("%A, %B %d, %Y — %I:%M %p %Z")} (user is in Florida, Eastern Time)

## Current State
Active tasks: {json.dumps(list(active.values()), default=str) if active else "None"}
Known projects: {json.dumps([{'name': p['name'], 'status': p.get('status')} for p in projects], default=str) if projects else "None"}
Vision: {vision_desc}

## What I know about the user
{learned_str}

## HARD RULES — Never break these in any response
- NEVER present a numbered list of options asking the user to choose. Pick and act.
- NEVER say "which way?", "what would you prefer?", "should I X or Y?" — decide yourself.
- If agent state looks stale or you can't verify it, assume it finished. Do not mention it. Focus on what the user wants right now.
- When ambiguous: pick the most reasonable interpretation and execute. Say what you're doing, not what you could do.
- The only question allowed is "Anything else?" after completing something.
"""

        messages = [{"role": m["role"], "content": m["content"]} for m in recent]
        messages.append({"role": "user", "content": message})

        return await self.api.create_message(
            system=self.system_prompt + context_block,
            messages=messages,
        )


    # ------------------------------------------------------------------
    # Task briefs
    # ------------------------------------------------------------------


    def _get_skills_manifest(self) -> str:
        """Return a concise tools/skills section to inject into task briefs."""
        oc_bin = Path.home() / ".openclaw" / "bin" / "openclaw"
        skills_dir = Path.home() / ".openclaw" / "workspace" / "skills"
        openclaw_available = oc_bin.exists()
        skill_names = []
        if skills_dir.exists():
            skill_names = sorted(d.name for d in skills_dir.iterdir() if d.is_dir())

        lines = ["## Available Tools\n"]
        lines.append("You have full bash access.")

        if openclaw_available and skill_names:
            # Key skills relevant to coding tasks
            coding_skills = [
                s for s in skill_names
                if any(k in s for k in [
                    "github", "docker", "cloud", "frontend", "debug", "clean-code",
                    "security", "database", "sql", "drizzle", "research", "search",
                    "in-depth", "senior-dev", "spark", "jarvis", "task-dev", "self-improving",
                    "kubernetes", "jenkins", "mlops", "linux",
                ])
            ]
            lines.append(f"You can also leverage these installed OpenClaw skills")
            lines.append(f"({len(skill_names)} total) — invoke via `{oc_bin} agent` or use their expertise")
            lines.append(f"directly since their knowledge is available to you:\n")
            if coding_skills:
                lines.append("**Relevant skills for this task:**")
                for s in coding_skills[:20]:
                    lines.append(f"  - `{s}`")

        lines.append("\n**Always:**")
        lines.append("  - Write tests for any code you add")
        lines.append("  - Commit changes with descriptive git messages")
        lines.append("  - Leave the codebase in a working state\n")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Project resolution
    # ------------------------------------------------------------------

    def _resolve_project(self, name_hint: str, message: str = "") -> Optional[dict]:
        """Find the best matching project from config."""
        projects = self.projects_config.get("projects", [])
        if not projects:
            return None

        combined = (name_hint + " " + message).lower()

        # Exact name match
        for p in projects:
            if p["name"].lower() == name_hint.lower():
                return p

        # Fuzzy match
        for p in projects:
            if p["name"].lower() in combined:
                return p

        # Default to first project if only one, otherwise just pick first
        return projects[0] if projects else None

    # ------------------------------------------------------------------
    # Background awareness loop
    # ------------------------------------------------------------------

    # _ram_watchdog   → awareness_mixin.py
    # _awareness_loop → awareness_mixin.py

    # ------------------------------------------------------------------
    # Personality helpers
    # ------------------------------------------------------------------

    def _translate_error(self, raw_error: str) -> str:
        """Turn a raw error string into human-friendly language."""
        error_lower = raw_error.lower()
        for pattern, friendly in self._error_translations.items():
            if pattern.lower() in error_lower:
                return friendly
        # Fallback: truncate and present simply
        short = raw_error[:120].rstrip(".")
        return f"Something went wrong — {short}"

    @staticmethod
    async def _self_update(self) -> str:
        """Run git pull and restart the process."""
        import subprocess
        try:
            result = subprocess.run(
                ["git", "pull"],
                cwd=str(Path(__file__).parent.parent),
                capture_output=True, text=True, timeout=60
            )
            output = result.stdout.strip()
            if result.returncode != 0:
                return f"Update failed — git pull returned an error:\n{result.stderr.strip() or output}"
            if "Already up to date" in output:
                return "Already on the latest version. Nothing to update."
            # Restart the process
            asyncio.create_task(self._delayed_restart(output))
            return f"Update pulled. Restarting now...\n{output}"
        except Exception as e:
            return f"Update failed: {e}"

    async def _delayed_restart(self, update_output: str):
        """Wait 2 seconds then restart the process."""
        import sys, os
        await asyncio.sleep(2)
        logger.info("Restarting after self-update...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    @staticmethod
    def _strip_sir(text: str) -> str:
        """
        Hard post-processing filter — remove every form of 'sir' the LLM might generate.
        This runs on 100% of outgoing responses. No exceptions.
        """
        import re
        # Remove patterns like ", sir.", ", sir!", " sir.", " sir,", "Sir,"
        text = re.sub(r',?\s*\bsir\b\.?', '', text, flags=re.IGNORECASE)
        # Clean up any double spaces or leading/trailing whitespace left behind
        text = re.sub(r'  +', ' ', text).strip()
        return text

    def _pick_completion_phrase(self, summary: str = "") -> str:
        """Pick a random task completion phrase, optionally with summary."""
        phrase = random.choice(self._task_complete_phrases)
        if "{summary}" in phrase and summary:
            return phrase.replace("{summary}", summary[:100])
        elif "{summary}" in phrase:
            return phrase.replace("{summary}", "").strip()
        return phrase

    def _pick_failure_phrase(self, error: str = "") -> str:
        """Pick a random task failure phrase with translated error."""
        friendly = self._translate_error(error) if error else "unknown issue"
        phrase = random.choice(self._task_failed_phrases)
        return phrase.replace("{error}", friendly)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def _watchdog_check(self):
        """Monitor agent processes for excessive resource usage or zombie state."""
        try:
            for agent_id, agent_info in list(self.agent_manager.active_agents.items()):
                proc = agent_info.get("process")
                if not proc or proc.poll() is not None:
                    continue

                pid = proc.pid
                try:
                    # Read /proc/<pid>/stat for CPU and memory
                    stat_path = Path(f"/proc/{pid}/stat")
                    if not stat_path.exists():
                        continue

                    # Check RSS memory (field 23 in /proc/pid/stat)
                    statm_path = Path(f"/proc/{pid}/statm")
                    if statm_path.exists():
                        pages = int(statm_path.read_text().split()[1])
                        rss_mb = (pages * 4096) / (1024 * 1024)

                        # Warn if agent uses > 2GB RAM
                        if rss_mb > 2048:
                            logger.warning(
                                f"Watchdog: Agent {agent_id} (PID {pid}) using {rss_mb:.0f} MB RAM"
                            )
                            self.notifications.push_system(
                                f"Agent #{agent_id[-8:]} high memory",
                                f"Using {rss_mb:.0f} MB RAM — may need attention",
                            )

                        # Kill if > 4GB (runaway process)
                        if rss_mb > 4096:
                            logger.error(
                                f"Watchdog: Killing agent {agent_id} — {rss_mb:.0f} MB RAM (runaway)"
                            )
                            await self.agent_manager.terminate_agent(agent_id)
                            self.notifications.push_system(
                                f"Agent #{agent_id[-8:]} killed",
                                f"Exceeded 4 GB memory limit ({rss_mb:.0f} MB)",
                                urgent=True,
                            )
                except (ValueError, FileNotFoundError, PermissionError):
                    pass
        except Exception as e:
            logger.debug(f"Watchdog error: {e}")

    async def _handle_screen_insight(self, insight: str):
        """Called by ScreenAwareness when it has a proactive suggestion."""
        if insight and self.notifications:
            self.notifications.push_screen_insight(insight)
            await self._broadcast_to_dashboard({
                "type": "input_response",
                "message": f"[Screen] {insight}",
                "timestamp": datetime.now().strftime("%H:%M"),
            })

    def _get_voice_status(self) -> dict:
        """Get voice system status for the dashboard."""
        vs = self.hotkey_listener.voice_system if self.hotkey_listener else None
        if not vs:
            return {"active": False}
        if hasattr(vs, 'listening_state'):
            state = vs.listening_state
            state["active"] = True
            return state
        return {"active": vs.is_listening, "state": "unknown"}

    def set_voice_system(self, voice_system):
        """Wire the voice system into the hotkey listener for push-to-talk."""
        self.hotkey_listener.voice_system = voice_system
        logger.info("Voice system connected to hotkey listener")

    def get_voice_config(self) -> dict:
        """Return voice config from settings for VoiceSystem initialization."""
        return self.config.get("voice", {})

    def get_status(self) -> dict:
        """Get full system status for UI."""
        security = {}
        if self.vault and self.owner_auth and self.audit_log:
            security = {
                "vault_unlocked": self.vault._unlocked,
                "authenticated": self.owner_auth.is_authenticated(),
                "audit_integrity": self.audit_log.verify_integrity(),
            }

        status = {
            "tasks": self.task_queue.get_status_summary(),
            "projects": self.memory.list_projects(),
            "active_agents": len(self.agent_manager.active_agents),
            "ai_provider": getattr(self.api, '_auth_method', 'none'),
            "ai_name": self.ai_name,
            "openclaw_available": self._openclaw_available,
            "claude_cli_available": self._claude_cli_available,
            "vision": self.vision.get_status() if self.vision else {},
            "security": security,
            "printer": self.printer is not None,
            "crm_pipeline": self.crm.get_pipeline_summary() if self.crm else {},
            "brain_role": self.brain_role,
            "bridge_connected": (self.bridge.connected if self.bridge else self._bridge_connected) if self.brain_role == "left" else False,
            "right_brain_online": (self.bridge.connected if self.bridge else bool(self._right_brain_status)) if self.brain_role == "left" else False,
            "right_brain_status": self._right_brain_status if self.brain_role == "left" else {},
            "screen": self.screen_awareness.get_context() if self.screen_awareness else {},
            "notifications": self.notifications.get_stats() if self.notifications else {},
            "voice": self._get_voice_status(),
        }
        return status

    # ------------------------------------------------------------------
    # Bridge dispatch + remote task handling (Left Brain)
    # ------------------------------------------------------------------

    # _dispatch_to_right_brain    → task_mixin.py
    # _handle_self_repair         → task_mixin.py

    async def _delayed_restart(self, delay_seconds: int = 3):
        """Restart Leon after a short delay (used after self-repair patch apply)."""
        await asyncio.sleep(delay_seconds)
        leon_path = str(Path(__file__).parent.parent)
        subprocess.Popen(
            ["bash", "start.sh"],
            cwd=leon_path,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _build_az_memory_context(self, project: dict) -> str:
        """
        Pull what Leon knows about this project and user and format it as
        context that Agent Zero receives at the start of every job.
        Leon = operational memory.  Agent Zero = coding/technical memory.
        This bridges the two.
        """
        lines = []

        # User preferences
        learned = self.memory.memory.get("learned_context", {})
        if learned.get("owner_name"):
            lines.append(f"Owner: {learned['owner_name']}")
        if learned.get("coding_preferences"):
            lines.append(f"Coding preferences: {learned['coding_preferences']}")
        if learned.get("favorite_language"):
            lines.append(f"Primary language: {learned['favorite_language']}")

        # Project-specific context from projects.yaml
        if project.get("context"):
            lines.append(f"\nProject context:\n{project['context'].strip()}")
        if project.get("tech_stack"):
            lines.append(f"Tech stack: {', '.join(project['tech_stack'])}")

        # Recent completed Agent Zero jobs on this project (last 3)
        completed = self.memory.memory.get("completed_tasks", {})
        proj_jobs = [
            v for v in completed.values()
            if v.get("project_name") == project.get("name")
            and v.get("executor") == "agent_zero"
        ]
        if proj_jobs:
            lines.append(f"\nPrevious Agent Zero jobs on this project:")
            for job in proj_jobs[-3:]:
                lines.append(f"  - {job.get('description', '')[:80]} [{job.get('status', '?')}]")

        # Recent memory updates mentioning this project
        updates = self.memory.memory.get("memory_updates", [])
        proj_name = project.get("name", "")
        relevant = [
            u["summary"] for u in updates[-20:]
            if proj_name.lower() in u.get("summary", "").lower()
        ]
        if relevant:
            lines.append(f"\nLeon's notes on {proj_name} (most recent first):")
            for u in relevant[-3:]:
                lines.append(f"  • {u[:120]}")

        return "\n".join(lines) if lines else ""

    async def _dispatch_to_agent_zero(
        self, task_desc: str, project: dict, original_message: str
    ) -> str:
        """
        Dispatch a heavy coding task to Agent Zero (Docker execution engine).
        Runs the job as a background asyncio task so Leon can respond immediately.
        Progress + completion are sent via Discord.
        """
        from tools.agent_zero_runner import get_runner
        az = get_runner()

        job_id_preview = f"AZ-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        logger.info("Routing to Agent Zero: %s → %s", task_desc[:60], job_id_preview)

        # Track in memory so dashboard shows it
        task_obj = {
            "id": job_id_preview,
            "description": task_desc,
            "project_name": project["name"],
            "executor": "agent_zero",
        }
        self.memory.add_active_task(job_id_preview, task_obj)

        # Gather Leon's memory context to inject into the Agent Zero job.
        # This bridges Leon's operational knowledge → Agent Zero's coding execution.
        leon_context = self._build_az_memory_context(project)

        # Fire-and-forget: job runs in background, Discord delivers results
        async def _run_and_cleanup():
            try:
                result = await az.run_job(
                    task_desc=task_desc,
                    project_path=project["path"],
                    project_name=project["name"],
                    leon_context=leon_context,
                )
                # Write compact summary back into Leon's memory so future
                # jobs and conversations benefit from what Agent Zero learned.
                summary = result.get("summary", "")
                if summary:
                    self.memory.memory_update(
                        f"[Agent Zero] {project['name']}: {task_desc[:80]} — {summary[:300]}",
                        source="agent_zero",
                    )
                completed = dict(task_obj)
                completed.update({"status": result["status"], "job_id": result["job_id"]})
                self.memory.memory.setdefault("completed_tasks", {})[result["job_id"]] = completed
                self.memory.memory.get("active_tasks", {}).pop(job_id_preview, None)
                self.memory.save()
            except Exception as exc:
                logger.exception("Agent Zero background job failed: %s", exc)

        asyncio.create_task(_run_and_cleanup())

        return (
            f"Dispatched to **Agent Zero** (Docker execution engine) 🐳\n\n"
            f"**Job ID:** `{job_id_preview}`\n"
            f"**Task:** {task_desc}\n"
            f"**Project:** {project['name']}\n\n"
            f"I'll send you Discord updates every {az.progress_every // 60}min and ping you when it's done.\n"
            f"Hard stop anytime: `kill agent zero job {job_id_preview}`"
        )

    async def _repair_openclaw_cron(self, error_text: str) -> None:
        """Auto-dispatch Agent Zero when OpenClaw cron fails — self-healing."""
        try:
            from tools.agent_zero_runner import get_runner
            runner = get_runner()
            if not runner:
                return
            task_desc = (
                f"SELF REPAIR: OpenClaw cron is broken with this error:\n{error_text}\n\n"
                f"Config file: /home/deansabr/.openclaw/openclaw.json\n"
                f"1. Check the config for any invalid/unrecognized keys (OpenClaw is strict about this)\n"
                f"2. Check if the OpenClaw gateway process is running (it should be listening on ws://127.0.0.1:18789)\n"
                f"3. Fix the config and/or restart the gateway so 'openclaw cron add' works\n"
                f"4. Test with: ~/.openclaw/bin/openclaw cron list\n"
                f"After fixing, post a brief summary of what was wrong and what you changed."
            )
            await runner.run_job(task_desc, project="leon-system")
            logger.info("Agent Zero dispatched to fix OpenClaw cron")
        except Exception as e:
            logger.debug("Could not dispatch AZ for cron repair: %s", e)

    # _fire_reminder  → reminder_mixin.py
    # _save_reminders → reminder_mixin.py
    # _load_reminders → reminder_mixin.py

    async def _send_discord_message(self, text: str, channel: str = "updates"):
        """
        Proactively push a message to the owner's Discord.

        channel:
          "updates" → #updates  (proactive messages, briefs, alerts — default)
          "chat"    → #chat     (direct conversational replies)
          "log"     → #log      (autonomous action one-liners)
        """
        try:
            from integrations.discord.dashboard import get_dashboard
            db = get_dashboard()
            if db:
                if channel == "log":
                    await db.post_to_log(text)
                    return
                elif channel == "chat":
                    ch = db._channels.get("chat")
                    if ch:
                        await ch.send(text[:2000])
                        return
                else:
                    await db.post_to_updates(text)
                    return
        except Exception:
            pass

        try:
            channel_file = Path("/tmp/leon_discord_channel.json")
            token_file   = Path("/tmp/leon_discord_bot_token.txt")
            if not channel_file.exists() or not token_file.exists():
                return
            channel_id = json.loads(channel_file.read_text())["channel_id"]
            token = token_file.read_text().strip()
            if not token or not channel_id:
                return
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers={"Authorization": f"Bot {token}"},
                    json={"content": text},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
        except Exception as e:
            logger.debug("Discord proactive message failed: %s", e)

    async def _broadcast_to_dashboard(self, data: dict):
        """Push a notification to all connected dashboard WebSocket clients."""
        try:
            from dashboard.server import ws_authenticated
            dead = set()
            for ws in ws_authenticated:
                try:
                    await ws.send_json(data)
                except Exception:
                    dead.add(ws)
            ws_authenticated -= dead
        except Exception:
            pass  # Dashboard may not be running

    # _handle_remote_task_status → task_mixin.py
    # _handle_remote_task_result → task_mixin.py
