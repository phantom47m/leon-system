"""
Leon Core - The main AI orchestration brain

Leon analyzes user requests, decides whether to respond directly or spawn
Claude Code agents, monitors tasks, and maintains persistent memory.
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from .memory import MemorySystem
from .agent_manager import AgentManager
from .task_queue import TaskQueue
from .openclaw_interface import OpenClawInterface
from .api_client import AnthropicAPI
from .neural_bridge import (
    BridgeServer, BridgeMessage,
    MSG_TASK_DISPATCH, MSG_TASK_STATUS, MSG_TASK_RESULT,
    MSG_STATUS_REQUEST, MSG_STATUS_RESPONSE, MSG_MEMORY_SYNC,
)

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
        except FileNotFoundError:
            logger.warning(f"Personality file not found: {personality_file}, using default")
            self.system_prompt = "You are Leon, a helpful AI assistant and orchestrator."

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

        logger.info(f"Leon initialized — brain_role={self.brain_role}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        self.running = True

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

        # Auto-trigger daily briefing on startup
        asyncio.create_task(self._auto_daily_briefing())

        logger.info("Leon is now running — all systems active")

    async def stop(self):
        logger.info("Stopping Leon...")
        self.running = False
        if self._awareness_task:
            self._awareness_task.cancel()
        if self._vision_task:
            self._vision_task.cancel()
        if self.vision:
            self.vision.stop()
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
                    lines = ["Found some STL files:\n"]
                    for i, r in enumerate(results[:5], 1):
                        lines.append(f"{i}. **{r.get('name', 'Untitled')}** — {r.get('url', 'N/A')}")
                    return "\n".join(lines)
                return "Couldn't find any matching STL files. Try different keywords."
            if "status" in msg and self.printer:
                printers = self.printer.list_printers()
                if not printers:
                    return "No printers configured. Update config/printers.yaml with your printer details."
                lines = ["Printer status:\n"]
                for p in printers:
                    s = p.get_status()
                    lines.append(f"**{p.name}**: {s.get('state', 'unknown')} — {s.get('progress', 0)}%")
                return "\n".join(lines)

        # Vision
        if any(w in msg for w in ["what do you see", "look at", "who's here", "what's around", "describe the room", "camera"]):
            if not self.vision:
                return "Vision system is not available."
            return self.vision.describe_scene()

        # Business — CRM
        if any(w in msg for w in ["crm", "pipeline", "clients", "contacts", "deals", "customer list"]):
            if not self.crm:
                return "CRM module is not available."
            return json.dumps(self.crm.get_pipeline_summary(), indent=2, default=str)

        # Business — leads
        if any(w in msg for w in ["find clients", "find leads", "hunt leads", "prospect", "generate leads", "new leads", "lead search"]):
            if self.audit_log:
                self.audit_log.log("lead_hunt", message, "info")
            return "Starting lead hunt... I'll search for businesses that need websites and score them. Check back shortly."

        # Business — finance
        if any(w in msg for w in ["revenue", "invoice", "how much money", "financial", "earnings", "profit", "expenses", "income", "billing"]):
            if not self.finance:
                return "Finance module is not available."
            return self.finance.get_daily_summary()

        # Business — communications
        if any(w in msg for w in ["send email", "check email", "inbox", "messages", "unread", "compose"]):
            if not self.comms:
                return "Communications module is not available."
            # Sending email requires permission
            if "send" in msg and self.permissions:
                if not self.permissions.check_permission("send_email"):
                    return "This requires owner approval. Use `/approve send_email` to grant temporary access."
            return "Comms hub active. What would you like to do — check inbox, send an email, or review messages?"

        # Business — briefing
        if any(w in msg for w in ["briefing", "brief me", "daily brief", "morning brief", "daily briefing", "what's happening", "catch me up", "daily summary"]):
            if not self.assistant:
                return "Personal assistant module is not available."
            return await self.assistant.generate_daily_briefing()

        # Business — schedule/calendar
        if any(w in msg for w in ["schedule", "calendar", "appointments", "meetings today", "what's on my calendar"]):
            if not self.assistant:
                return "Personal assistant module is not available."
            return await self.assistant.generate_daily_briefing()

        # Security
        if any(w in msg for w in ["audit log", "security log", "audit trail", "recent actions"]):
            if not self.audit_log:
                return "Audit log is not available."
            entries = self.audit_log.get_recent(10)
            if not entries:
                return "Audit log is clean — no entries yet."
            lines = ["Recent audit entries:\n"]
            for e in entries:
                lines.append(f"[{e.get('timestamp', '?')}] **{e.get('action')}** — {e.get('details', '')} ({e.get('severity')})")
            return "\n".join(lines)

        return None

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
                        f"This requires owner approval for `{action}`. "
                        f"Use `/approve {action}` to grant temporary access."
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
                "I'm not sure which project to work on. "
                "Which project directory should I use for this task?"
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

        location = " (local fallback)" if self.brain_role == "left" else ""
        return (
            f"On it. Spawned Agent #{agent_id[-8:]} to handle:\n"
            f"**{task_desc}** (project: {project['name']}){location}\n\n"
            f"I'll update you when it's done."
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

        # Build response
        lines = [f"On it. I've broken this into {len(spawned)} tasks:\n"]
        for idx, (aid, desc, proj, loc) in enumerate(spawned, 1):
            if aid:
                tag = f"Agent #{aid[-8:]}"
                where = f" [{loc}]" if loc and self.brain_role == "left" else ""
                lines.append(f"{idx}. **{desc}** → {tag} ({proj}){where}")
            else:
                lines.append(f"{idx}. **{desc}** → Needs project")
        lines.append("\nI'm monitoring all of them and will update you on progress.")

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
                        self.agent_manager.cleanup_agent(agent_id)
                        logger.info(f"Agent {agent_id} finished: {results.get('summary', '')[:80]}")

                        # Push completion to dashboard WebSocket
                        await self._broadcast_to_dashboard({
                            "type": "agent_completed",
                            "agent_id": agent_id,
                            "summary": results.get("summary", "")[:200],
                        })

                    elif status.get("failed"):
                        results = await self.agent_manager.get_agent_results(agent_id)
                        self.memory.complete_task(agent_id, {
                            "summary": f"FAILED: {results.get('errors', 'unknown error')[:200]}",
                            "files_modified": [],
                        })
                        self.task_queue.fail_task(agent_id, results.get("errors", ""))
                        self.agent_manager.cleanup_agent(agent_id)
                        logger.warning(f"Agent {agent_id} failed")

                        # Push failure to dashboard WebSocket
                        await self._broadcast_to_dashboard({
                            "type": "agent_failed",
                            "agent_id": agent_id,
                            "error": results.get("errors", "")[:200],
                        })

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

                # Periodic save
                self.memory.save()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Awareness loop error: {e}")

            await asyncio.sleep(10)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

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
                f"On it. Dispatched to Right Brain — Agent #{agent_id[-8:]}:\n"
                f"**{task_desc}** (project: {project['name']})\n\n"
                f"Running on homelab. I'll update you when it's done."
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
            f"Right Brain unavailable — running locally. Agent #{agent_id[-8:]}:\n"
            f"**{task_desc}** (project: {project['name']})\n\n"
            f"I'll update you when it's done."
        )

    async def _broadcast_to_dashboard(self, data: dict):
        """Push a notification to all connected dashboard WebSocket clients."""
        try:
            from dashboard.server import ws_clients
            import json as _json
            dead = set()
            for ws in ws_clients:
                try:
                    await ws.send_json(data)
                except Exception:
                    dead.add(ws)
            ws_clients -= dead
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
