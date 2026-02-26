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

logger = logging.getLogger("leon")


class Leon:
    """Main orchestration system - the brain that coordinates everything."""

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
    # Special command routing (hardware, business, vision, security)
    # ------------------------------------------------------------------

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
                projects = self.projects_config.get("projects", [])
                project_name = next(
                    (p["name"] for p in projects if "motorev" in p["name"].lower()), None
                ) or (projects[0]["name"] if projects else "Motorev")
                nm.add_task(_inline_task, project_name)
                asyncio.create_task(nm.try_dispatch())
                return f"Auto mode on. Task queued for {project_name} — spawning agent now. I'll text you updates every 30 minutes."

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

        # ── Self-optimization / code review ─────────────────────────────
        # Self-optimize triggers — must clearly refer to Leon's OWN code, not task briefs
        self_opt_triggers = [
            "optimize yourself", "fix your own code", "improve yourself",
            "review your own code", "check your own code", "audit your own code",
            "fix your code", "optimize your code", "review your code",
            "what's broken in your code", "optimize leon", "fix leon's code",
        ]
        # Extra guard: skip if this looks like a task brief (long message about another project)
        _is_task_brief = len(message) > 200 and any(w in msg for w in ["motorev", "app from railway", "phase 1", "phase 2", "codebase"])
        if not _is_task_brief and any(p in msg for p in self_opt_triggers):
            leon_path = str(Path(__file__).parent.parent)
            project = {
                "name": f"{self.ai_name} System",
                "path": leon_path,
                "tech_stack": ["Python", "aiohttp", "asyncio", "JavaScript", "Node.js"],
                "description": f"{self.ai_name}'s own source code",
            }
            task_desc = (
                f"Perform a self-improvement audit of the {self.ai_name} AI system codebase. "
                "Read the core Python files (core/leon.py, core/voice.py, dashboard/server.py, "
                "core/agent_manager.py, core/task_queue.py, core/night_mode.py). "
                "Identify: (1) bugs or error handling gaps, (2) code that could be more robust, "
                "(3) anything that might cause unexpected behavior. "
                "Fix the top 3 most impactful issues you find. Write a summary of what you changed."
            )
            brief_path = await self._create_task_brief(task_desc, project)
            agent_id = await self.agent_manager.spawn_agent(
                brief_path=brief_path,
                project_path=leon_path,
            )
            task_obj = {
                "id": agent_id,
                "description": task_desc,
                "project_name": "Leon System",
                "brief_path": brief_path,
            }
            self.task_queue.add_task(agent_id, task_obj)
            self.memory.add_active_task(agent_id, task_obj)
            return "Running a self-audit now. I'll read my own code, find the top issues, and fix them. I'll let you know what I find."

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
            # home assistant
            "turn on ", "turn off ", "turn the ", "lights ", "thermostat",
            "home assistant", "smart home", "switch on", "switch off",
            # telegram
            "send telegram", "telegram message", "message on telegram",
        ]
        if any(hint in msg for hint in system_hints):
            skill_result = await self._route_to_system_skill(message)
            if skill_result:
                return skill_result

        return None

    async def _route_to_system_skill(self, message: str) -> Optional[str]:
        """Route PC control commands — browser agent via OpenClaw, system info via skills."""
        msg = message.lower()

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

        # Direct cron dispatches — don't burn an AI call for unambiguous cron requests
        cron_list_hints = ["list cron", "cron jobs", "scheduled tasks", "my schedules",
                           "what's scheduled", "whats scheduled", "show schedules",
                           "show cron", "cron list"]
        if any(h in msg for h in cron_list_hints):
            jobs = self.openclaw.cron.list_jobs(include_disabled=True)
            return self.openclaw.cron.format_jobs(jobs)

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
            return await self._execute_browser_agent(goal, start_url=url)

        if action == "browser_screenshot":
            path = self.openclaw.browser.screenshot()
            return f"Screenshot saved to {path}." if path else "Couldn't take screenshot."

        if action == "cron_list":
            jobs = self.openclaw.cron.list_jobs(include_disabled=True)
            return self.openclaw.cron.format_jobs(jobs)

        if action == "cron_add":
            args = result.get("args", {})
            name = args.get("name", f"{self.ai_name} task")
            message = args.get("message", "")
            every = args.get("every")
            cron_expr = args.get("cron")
            at = args.get("at")
            tz = args.get("tz")
            if not message:
                return "Couldn't schedule — no task message specified."
            job = self.openclaw.cron.add_job(name, message, every=every, cron=cron_expr, at=at, tz=tz)
            if job.get("ok") is False:
                return f"Failed to schedule: {job.get('error', 'unknown error')}"
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
            return await self.system_skills.execute(skill_name, args)

        return None

    async def _execute_browser_agent(self, goal: str, start_url: str = None, max_steps: int = 8) -> str:
        """
        Agentic browser loop — reads the page, decides next action, executes, repeats.
        Gives Leon the full power of OpenClaw's browser control.
        """
        browser = self.openclaw.browser
        history = []

        logger.info(f"Browser agent: goal='{goal}' start_url={start_url}")

        # Ensure browser is running (sessions persist in ~/.openclaw/browser/openclaw/user-data/)
        browser.ensure_running()
        await asyncio.sleep(1.0)

        # Navigate to starting URL — reuse existing tab (navigate) instead of
        # open_url (which spawns a new 300MB renderer tab every time)
        if start_url:
            browser.navigate(start_url)
            logger.info(f"Browser agent: navigated to {start_url}")
            await asyncio.sleep(2.5)  # Let page load

        for step in range(max_steps):
            # Get current page state
            snapshot = browser.snapshot()
            if not snapshot:
                return "Browser isn't responding — is it running?"

            # Truncate snapshot to avoid token overflow
            snap_truncated = snapshot[:4000] if len(snapshot) > 4000 else snapshot

            history_summary = "\n".join(
                f"Step {h['step']}: {h['action']} — {h['reason']}"
                for h in history[-5:]  # Last 5 steps
            )

            prompt = f"""You are controlling a browser to accomplish this goal: "{goal}"

Current page (accessibility tree — element refs are numbers like [1], [23], etc.):
{snap_truncated}

Steps taken so far:
{history_summary or "None yet"}

What is the SINGLE best next action? Respond with ONLY valid JSON (no markdown):
{{
  "action": "click" | "type" | "press" | "navigate" | "scroll" | "fill" | "select" | "evaluate" | "wait" | "download" | "dialog" | "done",
  "ref": "element ref number — required for click/type/select/download/fill",
  "text": "text to type — required for type",
  "key": "key name — required for press (Enter, Tab, Escape, ArrowDown, etc.)",
  "url": "full URL — required for navigate; also used for wait",
  "fields": [{{"ref": "12", "value": "text"}}, ...],
  "values": ["option1", "option2"],
  "fn": "JS function string e.g. '() => document.title' — for evaluate",
  "wait_text": "text to wait for — for wait action",
  "wait_load": "load|domcontentloaded|networkidle — for wait action",
  "download_path": "/tmp/openclaw/downloads/filename.ext",
  "dialog_accept": true,
  "reason": "one sentence explaining this action",
  "done": true | false
}}

Action guide:
- click: click a button, link, or element
- type: type text into an input (use fill for multiple fields at once)
- fill: fill several form fields at once (more efficient than click+type per field)
- select: pick an option from a <select> dropdown
- press: keyboard shortcut (Enter to submit, Tab to advance, Escape to close)
- navigate: go to a new URL
- scroll: scroll down the page
- evaluate: run JS to read data from the page (e.g. get text that's not in snapshot)
- wait: wait for text/URL/load before next action (use after navigation)
- download: click a download link and save the file
- dialog: accept or dismiss a browser popup/alert
- done: goal fully accomplished

Rules:
- Use element refs from the page snapshot (numbers in brackets like [42])
- "done" = true only when the goal is fully accomplished
- For search boxes: type the search text, then press Enter
- After clicking links or buttons that load new pages, prefer wait action next
- If stuck after 3+ steps, try a different approach
- AUTH RULE: The browser is pre-authenticated with saved sessions for GitHub, Google, Railway, Discord, Reddit and more. NEVER enter passwords or credentials. If you see a login page, navigate directly to the dashboard/home URL instead (e.g. https://github.com, https://railway.app/dashboard).
- AUTH RULE: If a page asks to log in, assume you ARE logged in and navigate to the main app URL — the session cookie will kick in automatically.
- MEDIA RULE: For goals involving playing a song/video/audio — mark done=true immediately after clicking the video/song. Do NOT keep looping to verify it's playing.
- MEDIA RULE: If a video/song is already playing on the current page, immediately return done=true.
- Do NOT navigate away from a page if the goal has already been accomplished on that page."""

            result = await self.api.analyze_json(prompt, smart=True)
            if not result:
                logger.warning("Browser agent: AI returned no result")
                break

            action = result.get("action", "done")
            reason = result.get("reason", "")
            is_done = result.get("done", False)

            history.append({"step": step + 1, "action": action, "reason": reason})
            logger.info(f"Browser agent step {step+1}: {action} — {reason}")

            if action == "done" or is_done:
                # Return the reason if it's a natural sentence, otherwise generic done
                _action_words = ("click", "type", "navigate", "fill", "scroll",
                                 "press", "select", "wait", "step ")
                if reason and not any(reason.lower().startswith(w) for w in _action_words):
                    return reason
                return "Done."

            elif action == "click":
                ref = str(result.get("ref", ""))
                if ref:
                    browser.click(ref)
                    await asyncio.sleep(1.5)

            elif action == "type":
                ref = str(result.get("ref", ""))
                text = result.get("text", "")
                if ref and text:
                    browser.type_text(ref, text)
                    await asyncio.sleep(0.5)

            elif action == "press":
                key = result.get("key", "Enter")
                browser.press(key)
                await asyncio.sleep(1.5)

            elif action == "navigate":
                url = result.get("url", "")
                if url:
                    browser.navigate(url)
                    await asyncio.sleep(2.5)

            elif action == "scroll":
                browser.press("PageDown")
                await asyncio.sleep(0.8)

            elif action == "fill":
                fields = result.get("fields", [])
                if fields:
                    browser.fill(fields)
                    await asyncio.sleep(0.5)

            elif action == "select":
                ref = str(result.get("ref", ""))
                values = result.get("values", [])
                if ref and values:
                    browser.select(ref, *values)
                    await asyncio.sleep(0.5)

            elif action == "evaluate":
                fn = result.get("fn", "() => document.title")
                ref = result.get("ref")
                eval_result = browser.evaluate(fn, ref=ref)
                # Inject result into next step's history so AI can use it
                history[-1]["reason"] += f" | result: {eval_result[:200]}"
                await asyncio.sleep(0.3)

            elif action == "wait":
                wait_text = result.get("wait_text")
                wait_url = result.get("url")
                wait_load = result.get("wait_load")
                browser.wait(text=wait_text, url=wait_url, load=wait_load or ("load" if not wait_text and not wait_url else None))
                await asyncio.sleep(0.5)

            elif action == "download":
                ref = str(result.get("ref", ""))
                path = result.get("download_path", "/tmp/openclaw/downloads/download")
                if ref:
                    saved = browser.download(ref, path)
                    history[-1]["reason"] += f" | saved: {saved}"
                await asyncio.sleep(1.0)

            elif action == "dialog":
                accept = result.get("dialog_accept", True)
                browser.dialog(accept=accept)
                await asyncio.sleep(0.5)

        return "Done."

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
  "type": "simple" | "single_task" | "multi_task" | "plan",
  "tasks": ["description of each discrete task"],
  "projects": ["project name for each task or 'unknown'"],
  "complexity": 1-10,
  "plan_goal": "concise goal if type is plan, else null",
  "plan_project": "project name if type is plan, else null"
}}

Rules:
- "simple" = status question, quick answer, clarification, greeting, asking what you did
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

    async def _handle_plan_request(self, message: str, analysis: dict) -> str:
        """Route a plan-type request from _analyze_request into PlanMode."""
        if self.plan_mode.active:
            status = self.plan_mode.get_status()
            done = status["doneTasks"]
            total = status["totalTasks"]
            return f"There's already a plan running ({done}/{total} tasks done). Say 'cancel plan' if you want to start a new one."

        goal = analysis.get("plan_goal") or message
        project_name = analysis.get("plan_project") or ""

        project = self._resolve_project(project_name, message)
        if not project:
            projects = self.projects_config.get("projects", [])
            project = projects[0] if projects else None
        if not project:
            return "No projects configured — add one in config/projects.yaml first."

        asyncio.create_task(self.plan_mode.run(goal, project))
        return (
            f"On it. Analyzing {project['name']} and building a plan now — "
            f"I'll execute everything automatically. Check the dashboard for live progress."
        )

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
"""

        messages = [{"role": m["role"], "content": m["content"]} for m in recent]
        messages.append({"role": "user", "content": message})

        return await self.api.create_message(
            system=self.system_prompt + context_block,
            messages=messages,
        )

    async def _handle_single_task(self, message: str, analysis: dict) -> str:
        """Spawn a single Claude Code agent for one task."""
        task_desc = analysis["tasks"][0] if analysis.get("tasks") else message
        project_name = (analysis.get("projects") or ["unknown"])[0]
        project = self._resolve_project(project_name, message)

        if not project:
            return await self._respond_conversationally(message)

        brief_path = await self._create_task_brief(task_desc, project)

        # Dispatch to Right Brain if connected, otherwise run locally
        if self.brain_role == "left" and self.bridge and self.bridge.connected:
            return await self._dispatch_to_right_brain(task_desc, brief_path, project)

        agent_id = await self.agent_manager.spawn_agent(
            brief_path=brief_path,
            project_path=project["path"],
        )

        task_obj = {
            "id": agent_id,
            "description": task_desc,
            "project_name": project["name"],
            "brief_path": brief_path,
        }
        self.task_queue.add_task(agent_id, task_obj)
        self.memory.add_active_task(agent_id, task_obj)
        self.agent_index.record_spawn(
            agent_id, task_desc, project["name"], brief_path,
            str(self.agent_manager.output_dir / f"{agent_id}.log"),
        )

        return (
            f"On it. Spinning up an agent for **{task_desc}** "
            f"in {project['name']}.\n\n"
            f"I'll let you know when it's done."
        )

    async def _orchestrate(self, message: str, analysis: dict) -> str:
        """Break down a complex request and spawn multiple agents."""
        tasks = analysis.get("tasks", [])
        project_names = analysis.get("projects", [])
        spawned = []
        queued_to_night = []  # tasks deferred to night mode to avoid project conflicts
        use_right_brain = (
            self.brain_role == "left" and self.bridge and self.bridge.connected
        )

        # Projects that already have a running agent (from task queue)
        active_projects = {
            t.get("project_name", "") for t in self.task_queue.active_tasks.values()
        }
        # Projects claimed in this batch (prevent same-project duplicates within one orchestration)
        batch_projects: set[str] = set()

        for i, task_desc in enumerate(tasks):
            proj_name = project_names[i] if i < len(project_names) else "unknown"
            project = self._resolve_project(proj_name, task_desc)

            if not project:
                spawned.append((None, task_desc, "No project matched", ""))
                continue

            # Per-project guard: never run two agents on the same codebase simultaneously.
            # If this project already has a running agent (from this batch or the task queue),
            # queue the task in night mode instead of spawning a new agent.
            proj_key = project["name"]
            if proj_key in active_projects or proj_key in batch_projects:
                self.night_mode.add_task(task_desc, proj_name)
                queued_to_night.append((task_desc, proj_key))
                continue
            batch_projects.add(proj_key)

            brief_path = await self._create_task_brief(task_desc, project)

            if use_right_brain:
                # Dispatch to Right Brain
                dispatch_msg = BridgeMessage(
                    type=MSG_TASK_DISPATCH,
                    payload={
                        "brief_path": brief_path,
                        "project_path": project["path"],
                        "description": task_desc,
                        "project_name": project["name"],
                        "task_id": uuid.uuid4().hex[:8],
                    },
                )
                resp = await self.bridge.send_and_wait(dispatch_msg, timeout=15)
                if resp and resp.payload.get("status") == "spawned":
                    agent_id = resp.payload.get("agent_id", "remote")
                    task_obj = {
                        "id": agent_id,
                        "description": task_desc,
                        "project_name": project["name"],
                        "brief_path": brief_path,
                        "remote": True,
                    }
                    self.memory.add_active_task(agent_id, task_obj)
                    spawned.append((agent_id, task_desc, project["name"], "Right Brain"))
                else:
                    # Log rejection reason if applicable
                    if resp and resp.payload.get("status") == "rejected":
                        logger.warning(f"Right Brain rejected task: {resp.payload.get('reason', 'unknown')}")
                    # Fallback to local
                    agent_id = await self.agent_manager.spawn_agent(
                        brief_path=brief_path, project_path=project["path"],
                    )
                    task_obj = {"id": agent_id, "description": task_desc,
                                "project_name": project["name"], "brief_path": brief_path}
                    self.task_queue.add_task(agent_id, task_obj)
                    self.memory.add_active_task(agent_id, task_obj)
                    spawned.append((agent_id, task_desc, project["name"], "local fallback"))
            else:
                agent_id = await self.agent_manager.spawn_agent(
                    brief_path=brief_path,
                    project_path=project["path"],
                )
                task_obj = {
                    "id": agent_id,
                    "description": task_desc,
                    "project_name": project["name"],
                    "brief_path": brief_path,
                }
                self.task_queue.add_task(agent_id, task_obj)
                self.memory.add_active_task(agent_id, task_obj)
                spawned.append((agent_id, task_desc, project["name"], "local"))

        # Build response — conversational, not robotic
        actual = [(aid, desc, proj, loc) for (aid, desc, proj, loc) in spawned if aid]
        if not actual and not queued_to_night:
            return "Couldn't match any of those to a project I know about."

        lines = []
        if actual:
            lines.append(f"On it. Spawning {len(actual)} agent{'s' if len(actual) != 1 else ''}:\n")
            for idx, (aid, desc, proj, loc) in enumerate(actual, 1):
                where = f" [{loc}]" if loc and self.brain_role == "left" else ""
                lines.append(f"{idx}. **{desc}** — {proj}{where}")
        if queued_to_night:
            if actual:
                lines.append("")
            lines.append(
                f"{len(queued_to_night)} more task{'s' if len(queued_to_night) != 1 else ''} queued — "
                f"will run after the current agent finishes (one at a time per codebase)."
            )
        if actual:
            lines.append("\nI'll keep you posted.")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Task briefs
    # ------------------------------------------------------------------

    async def _create_task_brief(self, task_desc: str, project: dict) -> str:
        """Generate a detailed task brief and write it to disk."""
        task_id = uuid.uuid4().hex[:8]
        brief_path = self.agent_manager.brief_dir / f"task_{task_id}.md"

        # Get project context from memory
        mem_context = self.memory.get_project_context(project["name"])
        recent_changes = ""
        if mem_context:
            rc = mem_context.get("context", {}).get("recent_changes", [])
            recent_changes = "\n".join(f"- {c}" for c in rc[-5:])

        prompt = f"""Create a concise task brief for a Claude Code agent.

Task: {task_desc}
Project: {project['name']}
Path: {project['path']}
Tech stack: {', '.join(project.get('tech_stack', []))}
Recent changes:
{recent_changes or 'None'}

Write a markdown brief with these sections:
# Task Brief
## Objective
## Project Context
## Requirements
## Success Criteria

Keep it focused and actionable. The agent should be able to start immediately."""

        brief_content = await self.api.quick_request(prompt)

        # Add metadata header + skills manifest
        skills_section = self._get_skills_manifest()
        header = f"""---
agent_task_id: {task_id}
project: {project['name']}
created: {datetime.now().isoformat()}
spawned_by: {self.ai_name} v1.0
---

{skills_section}
"""
        brief_path.write_text(header + brief_content)
        logger.info(f"Created task brief: {brief_path}")
        return str(brief_path)

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

    async def _ram_watchdog(self):
        """Monitor RAM every 60s. If above 80%, kill runaway OpenClaw renderer tabs."""
        import subprocess as _sp
        while self.running:
            try:
                await asyncio.sleep(60)
                with open("/proc/meminfo") as f:
                    info = {k.strip(): v.strip() for k, v in
                            (line.split(":", 1) for line in f if ":" in line)}
                total = int(info.get("MemTotal", "0 kB").split()[0])
                avail = int(info.get("MemAvailable", "0 kB").split()[0])
                used_pct = (total - avail) / total * 100 if total else 0

                if used_pct > 80:
                    # Find OpenClaw renderer PIDs
                    r = _sp.run(
                        ["pgrep", "-f", "remote-debugging-port=18800"],
                        capture_output=True, text=True
                    )
                    pids = r.stdout.strip().split()
                    # Keep 2, kill the rest (oldest first = lowest PIDs)
                    pids_sorted = sorted(int(p) for p in pids if p.isdigit())
                    to_kill = pids_sorted[:-2] if len(pids_sorted) > 2 else []
                    if to_kill:
                        for pid in to_kill:
                            try:
                                _sp.run(["kill", str(pid)], check=False)
                            except Exception:
                                pass
                        logger.warning(
                            "RAM watchdog: %.0f%% used — killed %d OpenClaw renderer(s)",
                            used_pct, len(to_kill)
                        )
                        # Notify via voice if available
                        vs = self.hotkey_listener.voice_system if self.hotkey_listener else None
                        if vs and vs.is_listening:
                            asyncio.create_task(vs.speak(
                                f"Heads up — RAM hit {used_pct:.0f}%. "
                                "Cleaned up some browser processes to free memory."
                            ))
            except Exception as e:
                logger.debug("RAM watchdog error: %s", e)

    async def _awareness_loop(self):
        """Continuously monitor active agents and update state."""
        logger.info("Awareness loop started")
        while self.running:
            try:
                # Monitor local agents
                # check_status handles all state transitions including 500-error retries.
                # Do NOT pre-clean failed agents here — that would bypass the retry logic.
                agent_ids = list(self.agent_manager.active_agents.keys())
                for agent_id in agent_ids:
                    status = await self.agent_manager.check_status(agent_id)

                    if status.get("retrying"):
                        # Agent is being retried — update memory with new agent ID
                        new_id = status.get("new_agent_id")
                        if new_id:
                            old_task = self.memory.get_active_task(agent_id)
                            if old_task:
                                self.memory.remove_active_task(agent_id)
                                old_task["id"] = new_id
                                self.memory.add_active_task(new_id, old_task)
                            # Update task queue mapping and persist to disk
                            task = self.task_queue.active_tasks.pop(agent_id, None)
                            if task:
                                task["agent_id"] = new_id
                                self.task_queue.active_tasks[new_id] = task
                                self.task_queue._save()
                        logger.info(f"Agent {agent_id} retrying as {new_id}")

                    elif status.get("completed"):
                        results = await self.agent_manager.get_agent_results(agent_id)
                        self.memory.complete_task(agent_id, results)
                        self.task_queue.complete_task(agent_id)
                        self.agent_index.record_completion(
                            agent_id,
                            results.get("summary", ""),
                            results.get("files_modified", []),
                            status.get("duration_seconds", 0),
                        )
                        self.agent_manager.cleanup_agent(agent_id)
                        logger.info(f"Agent {agent_id} finished: {results.get('summary', '')[:80]}")

                        # Push natural completion message to dashboard + desktop
                        completion_msg = self._pick_completion_phrase(
                            results.get("summary", "")
                        )
                        await self._broadcast_to_dashboard({
                            "type": "agent_completed",
                            "agent_id": agent_id,
                            "summary": completion_msg,
                        })
                        self.notifications.push_agent_completed(
                            agent_id, completion_msg
                        )

                        # Auto mode: mark done + spawn a self-directed continuation if queue is empty
                        self.night_mode.mark_agent_completed(agent_id, results.get("summary", ""))
                        if self.night_mode.active and not self.night_mode.get_pending():
                            # Find the project from the last completed task
                            last_task = next((t for t in reversed(self.night_mode._backlog)
                                             if t.get("status") == "completed"), None)
                            if last_task:
                                project_name = last_task.get("project", "unknown")
                                continuation = (
                                    f"Continue improving the {project_name} codebase. "
                                    f"Read LEON_PROGRESS.md to see what has already been done, "
                                    f"then find the next highest-value things to improve — "
                                    f"performance, code quality, UI polish, bugs, anything that makes it better. "
                                    f"Do not repeat work already done. Self-direct entirely. "
                                    f"Commit your changes. Log progress to LEON_PROGRESS.md."
                                )
                                self.night_mode.add_task(continuation, project_name)
                                logger.info(f"Auto mode: queued self-directed continuation for {project_name}")
                        asyncio.create_task(self.night_mode.try_dispatch())

                    elif status.get("failed"):
                        results = await self.agent_manager.get_agent_results(agent_id)
                        raw_error = results.get("errors", "unknown error")
                        self.memory.complete_task(agent_id, {
                            "summary": f"FAILED: {raw_error[:200]}",
                            "files_modified": [],
                        })
                        self.task_queue.fail_task(agent_id, raw_error)
                        self.agent_index.record_failure(
                            agent_id,
                            raw_error,
                            status.get("duration_seconds", 0),
                        )
                        self.agent_manager.cleanup_agent(agent_id)
                        logger.warning(f"Agent {agent_id} failed")

                        # Push natural failure message to dashboard + desktop
                        failure_msg = self._pick_failure_phrase(raw_error)
                        await self._broadcast_to_dashboard({
                            "type": "agent_failed",
                            "agent_id": agent_id,
                            "error": failure_msg,
                        })
                        self.notifications.push_agent_failed(
                            agent_id, failure_msg
                        )

                        # Night mode: mark failed + try to dispatch next task
                        self.night_mode.mark_agent_failed(agent_id, raw_error)
                        asyncio.create_task(self.night_mode.try_dispatch())

                # --- Per-cycle operations (outside per-agent loop) ---

                # Poll Right Brain status if Left Brain
                if self.brain_role == "left" and self.bridge and self.bridge.connected:
                    resp = await self.bridge.send_and_wait(
                        BridgeMessage(type=MSG_STATUS_REQUEST), timeout=5
                    )
                    if resp:
                        self._right_brain_status = resp.payload
                        self._bridge_connected = True
                    else:
                        self._bridge_connected = self.bridge.connected
                elif self.brain_role == "left":
                    self._bridge_connected = False
                    self._right_brain_status = {}

                # Check scheduled tasks
                if self.scheduler:
                    due_tasks = self.scheduler.get_due_tasks()
                    for sched_task in due_tasks:
                        cmd = sched_task.get("command", "")
                        if cmd:
                            logger.info(f"Running scheduled task: {sched_task['name']} -> {cmd}")
                            try:
                                await self.process_user_input(cmd)
                            except Exception as e:
                                logger.error(f"Scheduled task failed: {sched_task['name']}: {e}")
                            self.scheduler.mark_completed(sched_task["name"])

                # Watchdog: check agent resource usage
                await self._watchdog_check()

                # Periodic update check
                if self.update_checker:
                    import time as _time
                    now_ts = _time.monotonic()
                    if now_ts - self._last_update_check >= self._update_interval:
                        self._last_update_check = now_ts
                        try:
                            found = await self.update_checker.check()
                            if found and self.update_checker.should_notify():
                                self._update_available = True
                                self._update_mentioned = False
                                self.update_checker.mark_notified()
                                # Dashboard notification
                                msg = (
                                    f"Update available: v{self.update_checker.latest_version}\n"
                                    f"Run: cd ~/leon-system && git pull\n"
                                    f"{self.update_checker.release_url}"
                                )
                                logger.info("Update notification: %s", msg)
                                await self._broadcast_to_dashboard({
                                    "type": "update_available",
                                    "version": self.update_checker.latest_version,
                                    "url": self.update_checker.release_url,
                                })
                        except Exception as e:
                            logger.debug("Update check error: %s", e)

                # Periodic save
                self.memory.save()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Awareness loop error: {e}")

            await asyncio.sleep(10)

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

    async def _dispatch_to_right_brain(self, task_desc: str, brief_path: str, project: dict) -> str:
        """Send a single task to the Right Brain for execution."""
        task_id = uuid.uuid4().hex[:8]
        dispatch_msg = BridgeMessage(
            type=MSG_TASK_DISPATCH,
            payload={
                "brief_path": brief_path,
                "project_path": project["path"],
                "description": task_desc,
                "project_name": project["name"],
                "task_id": task_id,
            },
        )

        resp = await self.bridge.send_and_wait(dispatch_msg, timeout=15)

        if resp and resp.payload.get("status") == "spawned":
            agent_id = resp.payload.get("agent_id", f"remote_{task_id}")
            task_obj = {
                "id": agent_id,
                "description": task_desc,
                "project_name": project["name"],
                "brief_path": brief_path,
                "remote": True,
            }
            self.memory.add_active_task(agent_id, task_obj)
            return (
                f"Sent that to the homelab. Working on **{task_desc}** "
                f"in {project['name']}.\n\nI'll let you know when it's done."
            )

        # Handle explicit rejection (backpressure)
        if resp and resp.payload.get("status") == "rejected":
            reason = resp.payload.get("reason", "unknown")
            logger.warning(f"Right Brain rejected task — reason: {reason}, falling back to local")
        else:
            # Fallback to local execution
            logger.warning("Right Brain dispatch failed — falling back to local")
        agent_id = await self.agent_manager.spawn_agent(
            brief_path=brief_path,
            project_path=project["path"],
        )
        task_obj = {
            "id": agent_id,
            "description": task_desc,
            "project_name": project["name"],
            "brief_path": brief_path,
        }
        self.task_queue.add_task(agent_id, task_obj)
        self.memory.add_active_task(agent_id, task_obj)
        return (
            f"Homelab's not responding — running it locally instead. "
            f"Working on **{task_desc}** in {project['name']}.\n\n"
            f"I'll let you know when it's done."
        )

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

    async def _handle_remote_task_status(self, msg: BridgeMessage):
        """Handle task status updates from Right Brain."""
        payload = msg.payload
        task_id = payload.get("task_id", "")
        agent_id = payload.get("agent_id", task_id)
        status = payload.get("status", "")
        logger.info(f"Remote task {task_id} status: {status}")

        # Update task state in memory
        active_task = self.memory.get_active_task(agent_id)
        if active_task:
            active_task["remote_status"] = status
            if payload.get("error"):
                active_task["remote_error"] = payload["error"]
            self.memory.update_active_task(agent_id, active_task)
        elif status == "spawned" and agent_id:
            # Track newly spawned remote agent
            self.memory.add_active_task(agent_id, {
                "id": agent_id,
                "description": payload.get("description", "remote task"),
                "remote": True,
                "remote_status": status,
            })

    async def _handle_remote_task_result(self, msg: BridgeMessage):
        """Handle completed/failed task results from Right Brain."""
        payload = msg.payload
        task_id = payload.get("task_id", "")
        agent_id = payload.get("agent_id", task_id)
        status = payload.get("status", "")
        results = payload.get("results", {})

        if status == "completed":
            self.memory.complete_task(agent_id, results)
            logger.info(f"Remote agent {agent_id} completed: {results.get('summary', '')[:80]}")
        elif status == "failed":
            self.memory.complete_task(agent_id, {
                "summary": results.get("summary", "Remote task failed"),
                "files_modified": [],
            })
            logger.warning(f"Remote agent {agent_id} failed")
