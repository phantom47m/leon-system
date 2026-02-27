"""
TaskMixin — extracted from core/leon.py to keep that file manageable.

Contains: _handle_plan_request, _handle_single_task, _orchestrate,
          _create_task_brief, _dispatch_to_right_brain, _handle_self_repair,
          _handle_remote_task_status, _handle_remote_task_result
All self.* references resolve through Leon's MRO at runtime.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .neural_bridge import BridgeMessage, MSG_TASK_DISPATCH

logger = logging.getLogger("leon")


class TaskMixin:
    """Task spawning, orchestration, plan routing, self-repair, and bridge dispatch."""

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

    async def _handle_single_task(self, message: str, analysis: dict) -> str:
        """Spawn a single Claude Code agent for one task."""
        task_desc = analysis["tasks"][0] if analysis.get("tasks") else message
        project_name = (analysis.get("projects") or ["unknown"])[0]
        project = self._resolve_project(project_name, message)

        if not project:
            projects = self.projects_config.get("projects", [])
            if not projects:
                return "No projects configured — add one to config/projects.yaml first."
            return f"Couldn't match that to a project. Known projects: {', '.join(p['name'] for p in projects)}. Which one?"

        # ── Agent Zero routing ─────────────────────────────────────────────
        # For heavy coding/CI/infra tasks, dispatch to the Docker execution engine
        try:
            from tools.agent_zero_runner import get_runner
            az = get_runner()
            if az.is_enabled() and az.should_dispatch(task_desc):
                if az.active_job_count() >= az.max_parallel:
                    logger.info("Agent Zero at capacity (%d jobs) — falling back to Claude", az.max_parallel)
                elif await az.is_available_async():
                    return await self._dispatch_to_agent_zero(task_desc, project, message)
                else:
                    logger.warning("Agent Zero enabled but not reachable — run setup-agent-zero.sh")
        except ImportError:
            pass  # tools.agent_zero_runner not yet installed — skip silently

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

    async def _handle_self_repair(self, message: str, component: str, file_hint: str) -> str:
        """
        Leon received criticism / a bug report about itself.
        Dispatch a targeted self-repair job to Agent Zero (or Claude agent fallback)
        pointed at the leon-system codebase.

        Agent Zero is ideal here: it can read the code, reproduce the issue,
        write a fix, run a quick test, and hand back a diff — just like it would
        for any other project, but the project IS Leon itself.
        """
        leon_path = str(Path(__file__).parent.parent)
        project = {
            "name": "Leon System",
            "path": leon_path,
            "type": "system",
            "tech_stack": ["Python", "aiohttp", "asyncio", "JavaScript"],
            "context": (
                f"This is Leon's OWN source code at {leon_path}.\n"
                "You are repairing a bug that the owner just reported.\n"
                "Key files: core/leon.py (main brain), integrations/discord/bot.py "
                "(Discord bridge), core/voice.py (TTS/STT), core/agent_manager.py, "
                "core/memory.py, dashboard/server.py.\n"
                "After fixing, write a REPORT.md with: what the bug was, what you changed, "
                "and the exact file + line numbers."
            ),
        }

        # Pull the last few conversation turns for full context
        recent = self.memory.get_recent_context(limit=6)
        context_lines = []
        for turn in recent[-6:]:
            role = turn.get("role", "?")
            content = turn.get("content", "")[:300]
            context_lines.append(f"{role.upper()}: {content}")
        conversation_context = "\n".join(context_lines)

        task_desc = (
            f"SELF-REPAIR: Fix a bug in Leon's own code.\n\n"
            f"WHAT THE USER REPORTED:\n{message}\n\n"
            f"SUSPECTED COMPONENT: {component}\n"
            f"LIKELY FILE: {file_hint}\n\n"
            f"RECENT CONVERSATION (for context):\n{conversation_context}\n\n"
            f"YOUR JOB:\n"
            f"1. Read {file_hint} and understand the current implementation.\n"
            f"2. Identify exactly what is causing the reported problem.\n"
            f"3. Fix it. Test if possible.\n"
            f"4. Write REPORT.md: bug root cause, files changed, lines changed.\n"
            f"Do NOT make unrelated changes. Stay focused on the reported issue."
        )

        # Use Agent Zero if available (can build tools on the fly, test, verify)
        try:
            from tools.agent_zero_runner import get_runner
            az = get_runner()
            if az.is_enabled() and await az.is_available_async():
                leon_context = self._build_az_memory_context(project)
                job_id_preview = f"AZ-SELFREPAIR-{datetime.now().strftime('%H%M%S')}"
                task_obj = {
                    "id": job_id_preview,
                    "description": f"Self-repair: {component}",
                    "project_name": "Leon System",
                    "executor": "agent_zero",
                }
                self.memory.add_active_task(job_id_preview, task_obj)

                async def _repair_and_notify():
                    try:
                        result = await az.run_job(
                            task_desc=task_desc,
                            project_path=leon_path,
                            project_name="Leon System",
                            leon_context=leon_context,
                        )
                        summary = result.get("summary", "")
                        diff_path = result.get("diff_path", "")
                        self.memory.memory.get("active_tasks", {}).pop(job_id_preview, None)
                        self.memory.save()

                        # Offer to apply the patch and restart
                        if diff_path and Path(diff_path).exists():
                            await self._send_discord_message(
                                f"🔧 **Self-repair complete** ({component})\n"
                                f"{summary[:300]}\n\n"
                                f"**To apply:** `git apply {diff_path}` then restart Leon.\n"
                                f"Or say: **apply self-repair and restart**"
                            )
                        else:
                            await self._send_discord_message(
                                f"🔧 **Self-repair complete** ({component})\n{summary[:400]}"
                            )
                    except Exception as exc:
                        logger.exception("Self-repair job failed: %s", exc)

                asyncio.create_task(_repair_and_notify())
                return (
                    f"Got it — I know my {component} failed. "
                    f"Dispatching Agent Zero to diagnose and fix it now.\n\n"
                    f"It'll read `{file_hint}`, find the root cause, and send you the fix via Discord."
                )
        except ImportError:
            pass

        # Fallback: Claude Code agent directly on leon-system (no workspace copy)
        brief_path = await self._create_task_brief(task_desc, project)
        agent_id = await self.agent_manager.spawn_agent(
            brief_path=brief_path,
            project_path=leon_path,
        )
        task_obj = {
            "id": agent_id,
            "description": f"Self-repair: {component}",
            "project_name": "Leon System",
            "brief_path": brief_path,
        }
        self.task_queue.add_task(agent_id, task_obj)
        self.memory.add_active_task(agent_id, task_obj)
        return (
            f"Got it — my {component} is broken. "
            f"Spawning an agent to read `{file_hint}` and fix it.\n"
            f"I'll let you know what it finds."
        )

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
