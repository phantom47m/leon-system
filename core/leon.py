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

        # Load personality
        personality_file = self.config["leon"]["personality_file"]
        try:
            with open(personality_file, "r") as f:
                personality = yaml.safe_load(f)
            self.system_prompt = personality["system_prompt"]
            self._wake_responses = personality.get("wake_responses", ["Yeah?"])
            self._task_complete_phrases = personality.get("task_complete", ["Done."])
            self._task_failed_phrases = personality.get("task_failed", ["That didn't work — {error}."])
            self._error_translations = personality.get("error_translations", {})
        except FileNotFoundError:
            logger.warning(f"Personality file not found: {personality_file}, using default")
            self.system_prompt = "You are Leon, a helpful AI assistant and orchestrator."
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

        # Vision system
        try:
            from vision.vision import VisionSystem
            self.vision = VisionSystem(api_client=self.api, analysis_interval=3.0)
        except Exception as e:
            logger.warning(f"Vision module not available: {e}")
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

        self.running = False
        self._awareness_task = None
        self._vision_task = None
        self._whatsapp_process = None

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

        # Auto-start WhatsApp bridge if configured
        wa_config = self.config.get("whatsapp", {})
        if wa_config.get("enabled") and wa_config.get("auto_start"):
            self._start_whatsapp_bridge(wa_config)

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
        if self._vision_task:
            self._vision_task.cancel()
        if self.vision:
            self.vision.stop()
        if self._whatsapp_process:
            logger.info("Stopping WhatsApp bridge...")
            self._whatsapp_process.terminate()
            try:
                self._whatsapp_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._whatsapp_process.kill()
            self._whatsapp_process = None
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        if self.project_watcher:
            self.project_watcher.stop()
        if self.screen_awareness:
            await self.screen_awareness.stop()
        if self.notifications:
            await self.notifications.stop()
        if self.bridge:
            await self.bridge.stop()
        if self.vault:
            self.vault.lock()
        if self.audit_log:
            self.audit_log.log("system_stop", "Leon stopped", "info")
        self.memory.save()
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

    def _start_whatsapp_bridge(self, wa_config: dict):
        """Spawn the WhatsApp bridge as a subprocess."""
        bridge_dir = Path(wa_config.get("bridge_dir", "integrations/whatsapp"))
        bridge_script = bridge_dir / "bridge.js"

        if not bridge_script.exists():
            logger.warning(f"WhatsApp bridge script not found: {bridge_script}")
            return

        # Build env vars for the bridge
        env = os.environ.copy()
        env["LEON_API_URL"] = "http://127.0.0.1:3000"

        # Get API token from vault or env
        api_token = os.environ.get("LEON_API_TOKEN", "")
        if not api_token and self.vault and self.vault._unlocked:
            api_token = self.vault.retrieve("LEON_API_TOKEN") or ""
        env["LEON_API_TOKEN"] = api_token

        # Set allowed numbers from config
        allowed = wa_config.get("allowed_numbers", [])
        env["LEON_WHATSAPP_ALLOWED"] = ",".join(allowed)

        if not api_token:
            logger.warning("WhatsApp bridge: no API token available, skipping auto-start")
            return

        try:
            self._whatsapp_process = subprocess.Popen(
                ["node", "bridge.js"],
                cwd=str(bridge_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            logger.info(f"WhatsApp bridge started (PID {self._whatsapp_process.pid})")
            if self.audit_log:
                self.audit_log.log("whatsapp_bridge_start", f"PID {self._whatsapp_process.pid}", "info")
        except Exception as e:
            logger.error(f"Failed to start WhatsApp bridge: {e}")
            self._whatsapp_process = None

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

        # Permission check for sensitive actions
        if self.permissions:
            denied = self._check_sensitive_permissions(message)
            if denied:
                self.memory.add_conversation(denied, role="assistant")
                return denied

        # Analyze what the user wants
        analysis = await self._analyze_request(message)

        # Check for special command routing
        routed = await self._route_special_commands(message)
        if routed:
            response = routed
        elif analysis is None or analysis.get("type") == "simple":
            response = await self._respond_conversationally(message)
        elif analysis.get("type") == "single_task":
            response = await self._handle_single_task(message, analysis)
        else:
            response = await self._orchestrate(message, analysis)

        self.memory.add_conversation(response, role="assistant")
        return response

    # ------------------------------------------------------------------
    # Special command routing (hardware, business, vision, security)
    # ------------------------------------------------------------------

    async def _route_special_commands(self, message: str) -> Optional[str]:
        """Route messages to specialized modules when keywords match."""
        msg = message.lower()

        # Help command — list available modules
        if msg.strip() in ("help", "what can you do", "commands", "modules"):
            return self._build_help_text()

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
            "cpu", "ram", "memory", "disk", "storage", "processes", "uptime",
            "ip address", "battery", "temperature", "temp",
            "play", "pause", "next track", "previous track", "volume", "mute",
            "now playing", "what's playing", "music",
            "screenshot", "clipboard", "notify", "notification", "lock screen",
            "brightness", "find file", "recent files", "downloads", "trash",
            "wifi", "network", "speed test", "ping",
            "timer", "alarm", "set timer", "set alarm", "remind",
            "search for", "google", "define", "weather",
            "git status", "npm", "pip install", "port",
            "what's eating", "what's hogging", "what's running",
            "gpu", "graphics card", "vram", "cuda",
            "clipboard history", "clipboard search", "what did i copy",
            "workspace", "tile", "minimize", "maximize", "snap",
        ]
        if any(hint in msg for hint in system_hints):
            skill_result = await self._route_to_system_skill(message)
            if skill_result:
                return skill_result

        return None

    async def _route_to_system_skill(self, message: str) -> Optional[str]:
        """Use AI to classify a message into a system skill call, then execute it."""
        skill_list = self.system_skills.get_skill_list()

        prompt = f"""You are a skill router. Given the user's message, determine which system skill to call.

User message: "{message}"

{skill_list}

Respond with ONLY valid JSON (no markdown fences):
{{
  "skill": "skill_name",
  "args": {{"arg1": "value1"}},
  "confidence": 0.0-1.0
}}

If NO skill matches, respond: {{"skill": null, "args": {{}}, "confidence": 0.0}}

Examples:
- "what's eating my RAM" -> {{"skill": "top_processes", "args": {{"n": 10}}, "confidence": 0.95}}
- "open Firefox" -> {{"skill": "open_app", "args": {{"name": "firefox"}}, "confidence": 0.99}}
- "pause the music" -> {{"skill": "play_pause", "args": {{}}, "confidence": 0.95}}
- "how's the weather" -> {{"skill": "weather", "args": {{}}, "confidence": 0.9}}
- "set a timer for 5 minutes" -> {{"skill": "set_timer", "args": {{"minutes": 5, "label": "Timer"}}, "confidence": 0.95}}
- "take a screenshot" -> {{"skill": "screenshot", "args": {{}}, "confidence": 0.99}}
- "what's my IP" -> {{"skill": "ip_address", "args": {{}}, "confidence": 0.95}}"""

        result = await self.api.analyze_json(prompt)
        if not result or not result.get("skill"):
            return None

        confidence = result.get("confidence", 0)
        if confidence < 0.7:
            return None

        skill_name = result["skill"]
        args = result.get("args", {})

        logger.info(f"System skill: {skill_name}({args}) confidence={confidence}")

        skill_result = await self.system_skills.execute(skill_name, args)
        return skill_result

    def _check_sensitive_permissions(self, message: str) -> Optional[str]:
        """Check if the message requests a sensitive action that needs approval."""
        msg = message.lower()

        # Map keywords to permission actions
        checks = [
            (["delete", "remove file", "rm ", "erase"], "delete_files"),
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
        return "\n".join(modules)

    # ------------------------------------------------------------------
    # Request analysis
    # ------------------------------------------------------------------

    async def _analyze_request(self, message: str) -> Optional[dict]:
        """Use the API to classify and decompose the user's request."""
        # Build context from memory
        active_tasks = self.memory.get_all_active_tasks()
        projects = self.memory.list_projects()

        prompt = f"""Analyze this user request and classify it.

User message: "{message}"

Current active tasks: {json.dumps(list(active_tasks.values()), default=str) if active_tasks else "None"}
Known projects: {json.dumps([p['name'] for p in projects]) if projects else "None"}

Respond with ONLY valid JSON (no markdown fences):
{{
  "type": "simple" | "single_task" | "multi_task",
  "tasks": ["description of each discrete task"],
  "projects": ["project name for each task or 'unknown'"],
  "complexity": 1-10
}}

Rules:
- "simple" = status question, quick answer, clarification, greeting
- "single_task" = one coding/research task
- "multi_task" = 2+ distinct tasks that can be parallelized"""

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

        context_block = f"""
## Current State
Active tasks: {json.dumps(list(active.values()), default=str) if active else "None"}
Known projects: {json.dumps([{'name': p['name'], 'status': p.get('status')} for p in projects], default=str) if projects else "None"}
Vision: {vision_desc}
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
            return (
                "Not sure which project this is for. "
                "Which one should I target?"
            )

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

        location = " Ran locally since Right Brain's not available." if self.brain_role == "left" else ""
        return (
            f"On it. Spinning up an agent for **{task_desc}** "
            f"in {project['name']}.{location}\n\n"
            f"I'll let you know when it's done."
        )

    async def _orchestrate(self, message: str, analysis: dict) -> str:
        """Break down a complex request and spawn multiple agents."""
        tasks = analysis.get("tasks", [])
        project_names = analysis.get("projects", [])
        spawned = []
        use_right_brain = (
            self.brain_role == "left" and self.bridge and self.bridge.connected
        )

        for i, task_desc in enumerate(tasks):
            proj_name = project_names[i] if i < len(project_names) else "unknown"
            project = self._resolve_project(proj_name, task_desc)

            if not project:
                spawned.append((None, task_desc, "No project matched", ""))
                continue

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
        lines = [f"Right. Breaking that into {len(spawned)} tasks:\n"]
        for idx, (aid, desc, proj, loc) in enumerate(spawned, 1):
            if aid:
                where = f" [{loc}]" if loc and self.brain_role == "left" else ""
                lines.append(f"{idx}. **{desc}** — {proj}{where}")
            else:
                lines.append(f"{idx}. **{desc}** — need to know which project for this one")
        lines.append("\nAll running in parallel. I'll keep you posted.")

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

        # Add metadata header
        header = f"""---
agent_task_id: {task_id}
project: {project['name']}
created: {datetime.now().isoformat()}
spawned_by: Leon v1.0
---

"""
        brief_path.write_text(header + brief_content)
        logger.info(f"Created task brief: {brief_path}")
        return str(brief_path)

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

        # Default to first project if only one
        if len(projects) == 1:
            return projects[0]

        return None

    # ------------------------------------------------------------------
    # Background awareness loop
    # ------------------------------------------------------------------

    async def _awareness_loop(self):
        """Continuously monitor active agents and update state."""
        logger.info("Awareness loop started")
        while self.running:
            try:
                # Monitor local agents
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
                            # Update task queue mapping
                            task = self.task_queue.active_tasks.pop(agent_id, None)
                            if task:
                                task["agent_id"] = new_id
                                self.task_queue.active_tasks[new_id] = task
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
