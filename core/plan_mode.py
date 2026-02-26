"""
Leon Plan Mode — Structured multi-phase autonomous execution.

When given a large goal, Plan Mode:
  1. Uses the LLM to analyze the project file structure and generate a JSON plan
  2. Broadcasts the plan to the dashboard so you can see exactly what's coming
  3. Executes phase by phase — tasks within each phase run in parallel
  4. Each agent gets a precise brief: files it owns, files off-limits, what the
     previous phase did, and specific acceptance criteria
  5. Sends a completion summary when done

No approval gate — you asked for it, it runs.
"""

import asyncio
import json
import logging
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .leon import Leon

logger = logging.getLogger("leon.plan")


class PlanMode:
    PLAN_PATH = Path("data/current_plan.json")

    def __init__(self, leon: "Leon"):
        self.leon = leon
        self.current_plan: Optional[dict] = None
        self._active = False
        self._plan_task: Optional[asyncio.Task] = None
        self._completed_task_results: dict[str, dict] = {}  # task_id -> agent results

    # ─── Public API ───────────────────────────────────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    async def run(self, goal: str, project: dict):
        """Build a plan for `goal` and execute it immediately. Non-blocking."""
        if self._active:
            logger.warning("Plan mode already active — ignoring new request")
            return
        self._active = True
        self._plan_task = asyncio.create_task(self._run(goal, project))

    async def cancel(self):
        """Stop plan execution. Already-running agents keep going."""
        self._active = False
        if self._plan_task:
            self._plan_task.cancel()
            self._plan_task = None
        logger.info("Plan mode cancelled")

    def get_status(self) -> dict:
        """For _build_state in server.py."""
        if not self.current_plan:
            return {"active": False, "goal": "", "phases": [], "totalTasks": 0,
                    "doneTasks": 0, "runningTasks": 0, "failedTasks": 0}
        plan = self.current_plan
        total = sum(len(p.get("tasks", [])) for p in plan.get("phases", []))
        done = sum(1 for p in plan.get("phases", [])
                   for t in p.get("tasks", []) if t.get("status") == "completed")
        running = sum(1 for p in plan.get("phases", [])
                      for t in p.get("tasks", []) if t.get("status") == "running")
        failed = sum(1 for p in plan.get("phases", [])
                     for t in p.get("tasks", []) if t.get("status") == "failed")
        return {
            "active": self._active,
            "goal": plan.get("goal", ""),
            "project": plan.get("project", ""),
            "totalTasks": total,
            "doneTasks": done,
            "runningTasks": running,
            "failedTasks": failed,
            "phases": [
                {
                    "phase": p["phase"],
                    "name": p.get("name", ""),
                    "parallel": p.get("parallel", True),
                    "tasks": [
                        {
                            "id": t["id"],
                            "title": t["title"],
                            "status": t.get("status", "pending"),
                            "agentId": t.get("agent_id"),
                        }
                        for t in p.get("tasks", [])
                    ],
                }
                for p in plan.get("phases", [])
            ],
        }

    # ─── Internal orchestration ───────────────────────────────────────────

    async def _run(self, goal: str, project: dict):
        plan_id = uuid.uuid4().hex[:8]
        try:
            await self._broadcast(f"Planning: analyzing {project['name']} codebase…")
            plan = await self._generate_plan(goal, project, plan_id)

            self._save_plan(plan)
            await self._broadcast_plan(plan)
            logger.info(f"Plan {plan_id}: {len(plan.get('phases', []))} phases, "
                        f"{sum(len(p.get('tasks',[])) for p in plan.get('phases',[]))} tasks")

            await self._execute_plan(plan, project)

            summary = self._build_summary(plan)
            await self._broadcast(f"Plan complete — {summary}")
            await self._broadcast_plan(plan)  # final state
            logger.info(f"Plan {plan_id} done: {summary}")

            # Notify via Discord
            done = sum(1 for p in plan.get("phases", []) for t in p.get("tasks", []) if t.get("status") == "completed")
            total = sum(len(p.get("tasks", [])) for p in plan.get("phases", []))
            discord_msg = (
                f"✅ **Plan complete** — {plan.get('goal', '')}\n"
                f"{done}/{total} tasks done\n"
                f"Project: {project.get('name', '')}"
            )
            await self.leon._send_discord_message(discord_msg)

        except asyncio.CancelledError:
            logger.info(f"Plan {plan_id} cancelled")
        except Exception as e:
            logger.error(f"Plan {plan_id} error: {e}", exc_info=True)
            await self._broadcast(f"Plan failed: {e}")
        finally:
            self._active = False

    async def _generate_plan(self, goal: str, project: dict, plan_id: str) -> dict:
        """Use the LLM + file context to produce a structured JSON plan."""
        file_listing = self._get_file_listing(project["path"])
        key_content = self._read_key_files(project["path"])

        project_context = project.get("context", "")
        context_section = f"\nPROJECT CONTEXT (read carefully):\n{project_context}\n" if project_context else ""

        infra_note = ""
        if project.get("type") == "system" or "(no source files" in file_listing:
            infra_note = """
IMPORTANT — INFRASTRUCTURE / SYSTEM ADMINISTRATION MODE:
This is NOT a software project. Do not generate code tasks.
Each task brief must contain the EXACT shell commands to run, in sequence.
Example brief format: "Run: sudo apt install -y qemu-system-x86_64 qemu-utils ovmf virt-manager\\nThen run: git clone https://github.com/kholia/OSX-KVM.git /home/deansabr/macos-vm/OSX-KVM"
Agents have sudo access, bash, internet connectivity, and run with --dangerously-skip-permissions.
Tasks are infrastructure steps, not code changes.
"""

        prompt = f"""You are a senior software architect creating an autonomous execution plan.

GOAL: {goal}
PROJECT: {project["name"]}
PATH: {project["path"]}
TECH STACK: {", ".join(project.get("tech_stack", []))}{context_section}
FILE STRUCTURE:
{file_listing}

KEY FILE CONTENTS:
{key_content}

Output ONLY a JSON object — no explanation, no markdown, just the JSON:
{{
  "id": "{plan_id}",
  "goal": "precise one-line description of what will be achieved",
  "project": "{project["name"]}",
  "phases": [
    {{
      "phase": 1,
      "name": "short phase name",
      "parallel": false,
      "description": "what this phase achieves",
      "tasks": [
        {{
          "id": "t1",
          "title": "short task title",
          "brief": "250+ word exact description of what to do. Include specific file sections, function names, before/after state, edge cases. An agent must be able to execute this without asking any questions.",
          "files_owned": ["relative/path/from/project/root.ts"],
          "files_read": ["other/file/for/context.ts"],
          "acceptance_criteria": ["specific measurable outcome 1", "specific measurable outcome 2"]
        }}
      ]
    }}
  ]
}}

Design rules:
- Tasks in the same phase must NOT touch overlapping files
- If task B depends on task A's output, put B in a later phase
- parallel: true = all tasks in the phase run simultaneously; false = run sequentially
- Phase 1 is always the foundation (types, store, shared utils) — set parallel: false if order matters
- Later phases are features/screens that build on the foundation — set parallel: true
- files_owned paths must be relative to the project root
- Max 3 phases, max 5 tasks per phase
- Only include tasks that directly achieve the goal — no gold-plating{infra_note}"""

        result = await self.leon.api.analyze_json(prompt)

        if not result:
            raise ValueError("LLM returned empty plan")

        # Ensure required fields
        if "phases" not in result:
            raise ValueError(f"Plan missing 'phases' field: {list(result.keys())}")

        result["id"] = plan_id
        result.setdefault("created_at", datetime.now().isoformat())
        result["status"] = "executing"

        # Stamp task status fields
        for phase in result.get("phases", []):
            phase.setdefault("parallel", True)
            for task in phase.get("tasks", []):
                task.setdefault("status", "pending")
                task.setdefault("agent_id", None)
                task.setdefault("started_at", None)
                task.setdefault("completed_at", None)
                task.setdefault("result", None)
                task.setdefault("files_owned", [])
                task.setdefault("files_read", [])
                task.setdefault("acceptance_criteria", [])

        return result

    async def _execute_plan(self, plan: dict, project: dict):
        """Execute phases sequentially; tasks within each phase run in parallel."""
        phases = plan.get("phases", [])
        total_phases = len(phases)
        all_plan_files = self._get_all_owned_files(plan)

        for phase in phases:
            if not self._active:
                break

            phase_num = phase["phase"]
            phase_name = phase.get("name", f"Phase {phase_num}")
            tasks = [t for t in phase.get("tasks", []) if t.get("status") == "pending"]

            if not tasks:
                logger.info(f"Plan phase {phase_num}: no pending tasks, skipping")
                continue

            logger.info(f"Plan phase {phase_num}/{total_phases} '{phase_name}': "
                        f"{len(tasks)} task(s), parallel={phase.get('parallel', True)}")
            await self._broadcast(
                f"Phase {phase_num}/{total_phases}: {phase_name} — {len(tasks)} task(s) starting"
            )

            prev_context = self._build_prev_context(plan, phase_num)

            if phase.get("parallel", True):
                # Dispatch all tasks in the phase simultaneously
                jobs = []
                for task in tasks:
                    off_limits = self._off_limits_for_task(task, all_plan_files)
                    brief_path = await self._create_task_brief(
                        task, phase, plan, project, off_limits, prev_context
                    )
                    agent_id = await self.leon.agent_manager.spawn_agent(
                        brief_path, project["path"]
                    )
                    task["agent_id"] = agent_id
                    task["status"] = "running"
                    task["started_at"] = datetime.now().isoformat()
                    self._save_plan(plan)
                    jobs.append((task, agent_id))
                    logger.info(f"Plan: task '{task['title']}' → agent {agent_id}")

                await self._wait_for_jobs(jobs, plan)
            else:
                # Sequential: wait for each task before starting the next
                for task in tasks:
                    if not self._active:
                        break
                    off_limits = self._off_limits_for_task(task, all_plan_files)
                    brief_path = await self._create_task_brief(
                        task, phase, plan, project, off_limits, prev_context
                    )
                    agent_id = await self.leon.agent_manager.spawn_agent(
                        brief_path, project["path"]
                    )
                    task["agent_id"] = agent_id
                    task["status"] = "running"
                    task["started_at"] = datetime.now().isoformat()
                    self._save_plan(plan)
                    logger.info(f"Plan: task '{task['title']}' → agent {agent_id}")

                    await self._wait_for_jobs([(task, agent_id)], plan)

            completed = sum(1 for t in phase["tasks"] if t.get("status") == "completed")
            failed = sum(1 for t in phase["tasks"] if t.get("status") == "failed")
            await self._broadcast(
                f"Phase {phase_num} done: {completed} completed"
                + (f", {failed} failed" if failed else "")
            )

    async def _wait_for_jobs(self, jobs: list[tuple[dict, str]], plan: dict):
        """Poll until all (task, agent_id) pairs have finished."""
        pending = list(jobs)

        while pending:
            await asyncio.sleep(30)
            done = []

            for task, agent_id in pending:
                if agent_id not in self.leon.agent_manager.active_agents:
                    # Agent was already cleaned up externally
                    task["status"] = "completed"
                    task["completed_at"] = datetime.now().isoformat()
                    self._save_plan(plan)
                    done.append((task, agent_id))
                    continue

                status = await self.leon.agent_manager.check_status(agent_id)

                if status.get("retrying"):
                    new_id = status.get("new_agent_id")
                    if new_id:
                        task["agent_id"] = new_id
                        # Update the pending list for this task
                        done.append((task, agent_id))
                        pending.append((task, new_id))
                    continue

                if status.get("completed"):
                    results = await self.leon.agent_manager.get_agent_results(agent_id)
                    task["status"] = "completed"
                    task["completed_at"] = datetime.now().isoformat()
                    task["result"] = results.get("summary", "Done")[:300]
                    self._completed_task_results[task["id"]] = results
                    self._save_plan(plan)
                    self.leon.agent_manager.cleanup_agent(agent_id)
                    done.append((task, agent_id))
                    logger.info(f"Plan: task '{task['title']}' completed")
                    await self._broadcast(f"✓ {task['title']}")

                elif status.get("failed"):
                    task["status"] = "failed"
                    task["completed_at"] = datetime.now().isoformat()
                    task["result"] = (status.get("errors") or "")[:200] or "failed"
                    self._save_plan(plan)
                    self.leon.agent_manager.cleanup_agent(agent_id)
                    done.append((task, agent_id))
                    logger.warning(f"Plan: task '{task['title']}' failed — continuing")
                    await self._broadcast(f"✗ {task['title']} failed — moving on")

            for item in done:
                try:
                    pending.remove(item)
                except ValueError:
                    pass

    # ─── Brief generation ──────────────────────────────────────────────────

    async def _create_task_brief(
        self,
        task: dict,
        phase: dict,
        plan: dict,
        project: dict,
        off_limits: list[str],
        prev_context: str,
    ) -> str:
        brief_id = uuid.uuid4().hex[:8]
        brief_path = (
            self.leon.agent_manager.brief_dir
            / f"plan_{plan['id']}_t{task['id']}_{brief_id}.md"
        )

        total_phases = len(plan.get("phases", []))
        files_owned = "\n".join(f"- {f}" for f in task.get("files_owned", [])) or "- (see task description)"
        files_read = "\n".join(f"- {f}" for f in task.get("files_read", [])) or "- (none specified)"
        files_off = "\n".join(f"- {f}" for f in off_limits[:30]) or "- (none)"
        criteria = "\n".join(f"- {c}" for c in task.get("acceptance_criteria", [])) or "- Complete the task as described"
        prev_section = f"\n## Previous Phase Output\nThe following work was completed in earlier phases:\n{prev_context}\n" if prev_context else ""
        skills_section = self.leon._get_skills_manifest()

        content = f"""---
plan_id: {plan["id"]}
task_id: {task["id"]}
phase: {phase["phase"]}/{total_phases}
spawned_by: {self.leon.ai_name} v1.0
created: {datetime.now().isoformat()}
---
{skills_section}

# Task: {task["title"]}

## Plan Context
**Overall goal:** {plan.get("goal", "")}
**This phase ({phase["phase"]}/{total_phases} — {phase.get("name", "")}):** {phase.get("description", "")}

## What To Do
{task["brief"]}

## Files You Own (modify these freely)
{files_owned}

## Files You May Read (do not modify)
{files_read}

## Files Off-Limits (owned by other tasks — do NOT touch)
{files_off}
{prev_section}
## Acceptance Criteria
{criteria}

## Rules
- Working directory: {project["path"]}
- Only modify files listed under "Files You Own"
- Do not refactor or improve code outside your scope
- Do not add features beyond what is described above
- If tests exist, run them before finishing
"""

        brief_path.write_text(content)
        logger.debug(f"Plan brief: {brief_path.name}")
        return str(brief_path)

    # ─── Helpers ──────────────────────────────────────────────────────────

    def _get_file_listing(self, project_path: str) -> str:
        """Get a sorted list of source files in the project."""
        try:
            extensions = ["*.ts", "*.tsx", "*.py", "*.js", "*.jsx",
                          "*.sh", "*.yaml", "*.yml", "*.md", "*.conf", "*.json"]
            files = []
            for ext in extensions:
                for p in Path(project_path).rglob(ext):
                    parts = p.parts
                    if any(skip in parts for skip in [
                        "node_modules", ".next", "dist", "__pycache__", ".git", "build", ".expo"
                    ]):
                        continue
                    files.append(str(p.relative_to(project_path)))
            return "\n".join(sorted(files)[:120]) or "(no source files found)"
        except Exception as e:
            logger.warning(f"File listing failed: {e}")
            return "(could not list files)"

    def _read_key_files(self, project_path: str) -> str:
        """Read a curated selection of key files to give the LLM real context."""
        candidates = [
            "package.json",
            "tsconfig.json",
            "requirements.txt",
            "app/store/useStore.ts",
            "app/constants/DesignTokens.ts",
            "app/contexts/ThemeContext.tsx",
            "app/(tabs)/_layout.tsx",
            "src/store/index.ts",
            "src/types/index.ts",
            "src/types/models.ts",
        ]
        chunks = []
        total = 0
        limit = 10_000

        for rel in candidates:
            if total >= limit:
                break
            p = Path(project_path) / rel
            if p.exists():
                try:
                    text = p.read_text()[:2500]
                    chunks.append(f"### {rel}\n```\n{text}\n```")
                    total += len(text)
                except Exception:
                    pass

        return "\n\n".join(chunks) if chunks else "(key files not readable)"

    def _get_all_owned_files(self, plan: dict) -> list[str]:
        files = []
        for phase in plan.get("phases", []):
            for task in phase.get("tasks", []):
                files.extend(task.get("files_owned", []))
        return list(set(files))

    def _off_limits_for_task(self, task: dict, all_files: list[str]) -> list[str]:
        owned = set(task.get("files_owned", []))
        readable = set(task.get("files_read", []))
        return [f for f in all_files if f not in owned and f not in readable]

    def _build_prev_context(self, plan: dict, current_phase_num: int) -> str:
        if current_phase_num <= 1:
            return ""
        lines = []
        for phase in plan.get("phases", []):
            if phase["phase"] >= current_phase_num:
                break
            for task in phase.get("tasks", []):
                if task.get("status") == "completed":
                    result = self._completed_task_results.get(task["id"], {})
                    summary = result.get("summary") or task.get("result") or "completed"
                    lines.append(f"- {task['title']}: {str(summary)[:150]}")
                elif task.get("status") == "failed":
                    lines.append(f"- {task['title']}: FAILED — {str(task.get('result', ''))[:100]}")
        return "\n".join(lines)

    def _build_summary(self, plan: dict) -> str:
        total = sum(len(p.get("tasks", [])) for p in plan.get("phases", []))
        done = sum(
            1 for p in plan.get("phases", [])
            for t in p.get("tasks", []) if t.get("status") == "completed"
        )
        failed = total - done
        s = f"{done}/{total} tasks completed"
        if failed:
            s += f", {failed} failed"
        return s

    # ─── Persistence ──────────────────────────────────────────────────────

    def _save_plan(self, plan: dict):
        self.current_plan = plan
        self.PLAN_PATH.parent.mkdir(exist_ok=True)
        tmp = self.PLAN_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(plan, indent=2, default=str))
        shutil.move(str(tmp), str(self.PLAN_PATH))

    def load_saved_plan(self) -> Optional[dict]:
        if self.PLAN_PATH.exists():
            try:
                self.current_plan = json.loads(self.PLAN_PATH.read_text())
                return self.current_plan
            except Exception:
                pass
        return None

    # ─── Dashboard / Discord broadcast ────────────────────────────────────

    async def _broadcast(self, message: str):
        try:
            await self.leon._broadcast_to_dashboard({
                "type": "plan_update",
                "message": message,
                "timestamp": datetime.now().strftime("%H:%M"),
            })
        except Exception:
            pass

    async def _broadcast_plan(self, plan: dict):
        try:
            await self.leon._broadcast_to_dashboard({
                "type": "plan_created",
                "plan": plan,
                "status": self.get_status(),
                "timestamp": datetime.now().strftime("%H:%M"),
            })
        except Exception:
            pass
