"""
Leon Core - The main AI orchestration brain

Leon analyzes user requests, decides whether to respond directly or spawn
Claude Code agents, monitors tasks, and maintains persistent memory.
"""

import asyncio
import json
import logging
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

logger = logging.getLogger("leon")


class Leon:
    """Main orchestration system - the brain that coordinates everything."""

    def __init__(self, config_path: str = "config/settings.yaml"):
        logger.info("Initializing Leon...")

        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        # Core components
        self.memory = MemorySystem(self.config["leon"]["memory_file"])
        self.openclaw = OpenClawInterface(self.config["openclaw"]["config_path"])
        self.agent_manager = AgentManager(self.openclaw, self.config["agents"])
        self.task_queue = TaskQueue(self.config["agents"]["max_concurrent"])
        self.api = AnthropicAPI(self.config["api"])

        # Load personality
        with open(self.config["leon"]["personality_file"], "r") as f:
            personality = yaml.safe_load(f)
        self.system_prompt = personality["system_prompt"]

        # Load projects
        projects_file = self.config.get("leon", {}).get("projects_file", "config/projects.yaml")
        if Path(projects_file).exists():
            with open(projects_file, "r") as f:
                self.projects_config = yaml.safe_load(f) or {}
        else:
            self.projects_config = {"projects": []}

        self.running = False
        self._awareness_task = None

        logger.info("Leon initialized successfully")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        self.running = True
        self._awareness_task = asyncio.create_task(self._awareness_loop())
        logger.info("Leon is now running")

    async def stop(self):
        logger.info("Stopping Leon...")
        self.running = False
        if self._awareness_task:
            self._awareness_task.cancel()
        self.memory.save()
        logger.info("Leon stopped")

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

        # Analyze what the user wants
        analysis = await self._analyze_request(message)

        if analysis is None or analysis.get("type") == "simple":
            response = await self._respond_conversationally(message)
        elif analysis.get("type") == "single_task":
            response = await self._handle_single_task(message, analysis)
        else:
            response = await self._orchestrate(message, analysis)

        self.memory.add_conversation(response, role="assistant")
        return response

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
        context_block = f"""
## Current State
Active tasks: {json.dumps(list(active.values()), default=str) if active else "None"}
Known projects: {json.dumps([{'name': p['name'], 'status': p.get('status')} for p in projects], default=str) if projects else "None"}
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
            f"On it. Spawned Agent #{agent_id[-8:]} to handle:\n"
            f"**{task_desc}** (project: {project['name']})\n\n"
            f"I'll update you when it's done."
        )

    async def _orchestrate(self, message: str, analysis: dict) -> str:
        """Break down a complex request and spawn multiple agents."""
        tasks = analysis.get("tasks", [])
        project_names = analysis.get("projects", [])
        spawned = []

        for i, task_desc in enumerate(tasks):
            proj_name = project_names[i] if i < len(project_names) else "unknown"
            project = self._resolve_project(proj_name, task_desc)

            if not project:
                spawned.append((None, task_desc, "⚠️ No project matched"))
                continue

            brief_path = await self._create_task_brief(task_desc, project)
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
            spawned.append((agent_id, task_desc, project["name"]))

        # Build response
        lines = [f"On it. I've broken this into {len(spawned)} tasks:\n"]
        for idx, (aid, desc, proj) in enumerate(spawned, 1):
            tag = f"Agent #{aid[-8:]}" if aid else "⚠️ Needs project"
            lines.append(f"{idx}. **{desc}** → {tag} ({proj})")
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
                agent_ids = list(self.agent_manager.active_agents.keys())
                for agent_id in agent_ids:
                    status = await self.agent_manager.check_status(agent_id)

                    if status.get("completed"):
                        results = await self.agent_manager.get_agent_results(agent_id)
                        self.memory.complete_task(agent_id, results)
                        self.task_queue.complete_task(agent_id)
                        self.agent_manager.cleanup_agent(agent_id)
                        logger.info(f"Agent {agent_id} finished: {results.get('summary', '')[:80]}")

                    elif status.get("failed"):
                        results = await self.agent_manager.get_agent_results(agent_id)
                        self.memory.complete_task(agent_id, {
                            "summary": f"FAILED: {results.get('errors', 'unknown error')[:200]}",
                            "files_modified": [],
                        })
                        self.task_queue.fail_task(agent_id, results.get("errors", ""))
                        self.agent_manager.cleanup_agent(agent_id)
                        logger.warning(f"Agent {agent_id} failed")

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
        return {
            "tasks": self.task_queue.get_status_summary(),
            "projects": self.memory.list_projects(),
            "active_agents": len(self.agent_manager.active_agents),
        }
