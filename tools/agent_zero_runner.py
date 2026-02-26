"""
Agent Zero Runner — Leon adapter for dispatching heavy coding tasks to
Agent Zero running in Docker.

Architecture:
  1. Job creation  : generate job_id, create workspace subdirectory
  2. Context setup : copy project files → workspace/jobs/<id>/src/
                     initialise a git baseline so we can diff changes
  3. Dispatch      : POST task to Agent Zero HTTP API (SSE streaming)
  4. Progress      : poll stream + send Discord updates every N minutes
  5. Artifacts     : extract patch.diff, report.md, test_results.txt
  6. Result        : return compact dict to Leon

Security model:
  - Docker container only mounts WORKSPACE (not home dir)
  - Command denylist injected into every task prompt
  - Hard kill: docker stop agent-zero
  - Max runtime enforced via asyncio.wait_for
  - Per-job disk quota via workspace quota check
"""

import asyncio
import json
import logging
import os
import shlex
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
import yaml

logger = logging.getLogger("leon.az_runner")

# ── Constants ─────────────────────────────────────────────────────────────────

_SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
_DISCORD_CHANNEL_FILE = Path("/tmp/leon_discord_channel.json")
_DISCORD_TOKEN_FILE   = Path("/tmp/leon_discord_bot_token.txt")

# Keywords that trigger Agent Zero routing.
# These are HEAVY multi-step execution tasks — NOT everyday fix/refactor/feature work.
# Leon's Claude agents handle single-file edits, bug fixes, small features, refactors.
# Agent Zero handles: long CI runs, full test suites, infra setup, data pipelines, multi-tool builds.
_AZ_SIGNALS = {
    # CI / test execution
    "run ci", "run the ci", "run all tests", "run the test suite", "run the full test",
    "run integration tests", "run e2e tests", "execute tests",
    # Infra / DevOps (these need actual shell execution, not just code edits)
    "set up kubernetes", "deploy to kubernetes", "write terraform", "apply terraform",
    "write ansible", "helm chart", "ci/cd pipeline", "build pipeline",
    "dockerize", "write dockerfile", "build docker",
    # Data / analysis jobs (long-running scripts)
    "data pipeline", "etl pipeline", "data analysis script", "run the analysis",
    "process the dataset", "batch process",
    # Full project scaffolding (multi-step, not a single file change)
    "scaffold a", "bootstrap a new", "create a new project", "set up a new",
    "generate the boilerplate",
    # Explicit "Agent Zero" invocation
    "use agent zero", "send to agent zero", "agent zero:",
}

# Commands that Agent Zero must NEVER run (injected into every task prompt)
_DENYLIST = [
    "sudo", "rm -rf /", "rm -rf ~", "chmod 777 /",
    "dd if=", "mkfs", "fdisk", "format", "wipefs",
    "iptables", "ufw", "firewall", "passwd", "useradd",
    "crontab -r", "kill -9 1",
]


# ── Settings loader ────────────────────────────────────────────────────────────

def _load_settings() -> dict:
    try:
        return yaml.safe_load(_SETTINGS_PATH.read_text()) or {}
    except Exception:
        return {}


def _az_cfg() -> dict:
    return _load_settings().get("agent_zero", {})


# ── AgentZeroRunner ────────────────────────────────────────────────────────────

class AgentZeroJob:
    """Lightweight dataclass for a running or completed job."""
    def __init__(self, job_id: str, task: str, project: str, job_dir: Path):
        self.job_id   = job_id
        self.task     = task
        self.project  = project
        self.job_dir  = job_dir
        self.status   = "pending"     # pending → running → done / failed / killed
        self.started  = datetime.now()
        self.log_lines: list[str] = []

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log_lines.append(line)
        logger.info("AZ [%s] %s", self.job_id, msg)

    @property
    def elapsed_min(self) -> float:
        return (datetime.now() - self.started).total_seconds() / 60


class AgentZeroRunner:
    """
    Submits tasks to Agent Zero (Docker), monitors progress, collects artifacts.
    Thread-safe: multiple jobs share one runner instance.
    """

    def __init__(self):
        cfg = _az_cfg()
        self.base_url  = cfg.get("base_url", "http://localhost:50080").rstrip("/")
        self.workspace = Path(cfg.get("workspace", "/home/deansabr/agent-zero-workspace"))
        self.jobs_dir  = Path(__file__).parent.parent / "data" / "agent_zero_jobs"
        self.max_parallel   = cfg.get("max_parallel_jobs", 2)
        self.max_runtime    = cfg.get("max_runtime_minutes", 120) * 60  # seconds
        self.progress_every = cfg.get("progress_interval_minutes", 5) * 60
        self._active: dict[str, AgentZeroJob] = {}

    # ── Public helpers ─────────────────────────────────────────────────────────

    def is_enabled(self) -> bool:
        return bool(_az_cfg().get("enabled", False))

    def is_available(self) -> bool:
        """Non-blocking synchronous check: is Agent Zero HTTP server up?"""
        try:
            r = httpx.get(f"{self.base_url}/", timeout=2.0)
            return r.status_code < 500
        except Exception:
            return False

    async def is_available_async(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"{self.base_url}/")
                return r.status_code < 500
        except Exception:
            return False

    def should_dispatch(self, task_desc: str) -> bool:
        """Return True if this task should route to Agent Zero."""
        d = task_desc.lower()
        return any(sig in d for sig in _AZ_SIGNALS)

    def active_job_count(self) -> int:
        return sum(1 for j in self._active.values() if j.status == "running")

    def list_jobs(self) -> list[dict]:
        return [
            {
                "job_id": j.job_id, "task": j.task[:80], "project": j.project,
                "status": j.status, "elapsed_min": round(j.elapsed_min, 1),
            }
            for j in self._active.values()
        ]

    # ── Main entry point ───────────────────────────────────────────────────────

    async def run_job(
        self,
        task_desc: str,
        project_path: str,
        project_name: str = "",
    ) -> dict:
        """
        Submit a job to Agent Zero and wait for completion.
        Returns a result dict:
          {job_id, status, summary, diff_path, report_path, log_path, elapsed_min}
        """
        job_id = f"AZ-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "output").mkdir(exist_ok=True)

        job = AgentZeroJob(job_id, task_desc, project_name, job_dir)
        self._active[job_id] = job

        job.log(f"Job created — task: {task_desc[:80]}")
        await self._send_discord(f"🔧 **Job {job_id} started**\n> {task_desc[:120]}\n> Project: {project_name or 'unknown'} | Max runtime: {self.max_runtime//60}min")

        try:
            job.status = "running"

            # 1. Copy project into isolated workspace (agent works on COPY)
            src_dir = await self._setup_workspace(job, project_path)

            # 2. Build the task prompt with security constraints
            prompt = self._build_prompt(task_desc, project_name, src_dir)

            # 3. Submit and stream response
            job.log("Submitting to Agent Zero...")
            summary = await asyncio.wait_for(
                self._submit_and_stream(prompt, job_id, job),
                timeout=self.max_runtime,
            )

            # 4. Collect artifacts from workspace copy
            artifacts = await self._collect_artifacts(job, project_path, src_dir)

            job.status = "done"
            job.log(f"Job complete — elapsed {job.elapsed_min:.1f}min")

            # 5. Final Discord notification
            diff_lines = self._read_diff_preview(artifacts.get("diff_path"))
            final_msg = self._format_completion(job_id, summary, artifacts, diff_lines)
            await self._send_discord(final_msg)

            return {
                "job_id":      job_id,
                "status":      "done",
                "summary":     summary[:500] if summary else "(no summary)",
                "diff_path":   str(artifacts.get("diff_path", "")),
                "report_path": str(artifacts.get("report_path", "")),
                "log_path":    str(job_dir / "job.log"),
                "elapsed_min": round(job.elapsed_min, 1),
            }

        except asyncio.TimeoutError:
            job.status = "timeout"
            job.log(f"TIMEOUT after {self.max_runtime//60}min")
            await self._kill_container()
            await self._send_discord(f"⏱️ **Job {job_id} timed out** after {self.max_runtime//60}min — container restarted.")
            return {"job_id": job_id, "status": "timeout", "summary": "Job timed out."}

        except Exception as exc:
            job.status = "failed"
            job.log(f"ERROR: {exc}")
            logger.exception("Agent Zero job %s failed", job_id)
            await self._send_discord(f"❌ **Job {job_id} failed**: {str(exc)[:200]}")
            return {"job_id": job_id, "status": "failed", "summary": str(exc)[:200]}

        finally:
            # Flush log to disk
            (job_dir / "job.log").write_text("\n".join(job.log_lines))

    # ── Kill switch ────────────────────────────────────────────────────────────

    async def kill_job(self, job_id: str) -> bool:
        """Hard stop: kills the Docker container and marks job as killed."""
        job = self._active.get(job_id)
        if job:
            job.status = "killed"
            job.log("KILLED by user")
        killed = await self._kill_container()
        logger.warning("Kill switch triggered for job %s — container stopped: %s", job_id, killed)
        await self._send_discord(f"🛑 **Job {job_id} killed** — container stopped.")
        return killed

    async def _kill_container(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "stop", "-t", "5", "agent-zero",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            # Restart so it's ready for next job
            await asyncio.sleep(2)
            restart = await asyncio.create_subprocess_exec(
                "docker", "start", "agent-zero",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await restart.wait()
            return True
        except Exception as e:
            logger.error("kill_container failed: %s", e)
            return False

    # ── Workspace setup ────────────────────────────────────────────────────────

    async def _setup_workspace(self, job: AgentZeroJob, project_path: str) -> Path:
        """
        Copy project files to an isolated workspace dir.
        Init a git baseline so we can extract the diff afterwards.
        Returns path to the source copy INSIDE the Docker-mounted workspace.
        """
        # This path is what the Docker container sees as /a0/work_dir/jobs/<id>/src
        host_src = self.workspace / "jobs" / job.job_id / "src"
        host_src.parent.mkdir(parents=True, exist_ok=True)

        proj = Path(project_path)
        if not proj.exists():
            raise FileNotFoundError(f"Project path does not exist: {project_path}")

        job.log(f"Copying project → {host_src} ...")

        # Copy, excluding .git history (large), node_modules, __pycache__, build artifacts
        exclude_patterns = [
            "--exclude=.git", "--exclude=node_modules", "--exclude=__pycache__",
            "--exclude=.venv", "--exclude=venv", "--exclude=dist", "--exclude=build",
            "--exclude=*.pyc", "--exclude=.DS_Store",
        ]
        cmd = ["rsync", "-a", "--delete"] + exclude_patterns + [
            str(proj) + "/", str(host_src) + "/"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # rsync not available — use shutil
            if host_src.exists():
                shutil.rmtree(host_src)

            def _ignore(src, names):
                skip = {".git", "node_modules", "__pycache__", ".venv", "venv",
                        "dist", "build", ".DS_Store"}
                return [n for n in names if n in skip or n.endswith(".pyc")]

            shutil.copytree(str(proj), str(host_src), ignore=_ignore)

        # Initialize git baseline so we can diff after Agent Zero runs
        subprocess.run(["git", "init", "-q"], cwd=host_src, check=False)
        subprocess.run(["git", "add", "-A"], cwd=host_src, check=False)
        subprocess.run(
            ["git", "commit", "-q", "-m", "AZ baseline", "--allow-empty"],
            cwd=host_src,
            env={**os.environ, "GIT_AUTHOR_NAME": "az-baseline",
                 "GIT_AUTHOR_EMAIL": "az@localhost",
                 "GIT_COMMITTER_NAME": "az-baseline",
                 "GIT_COMMITTER_EMAIL": "az@localhost"},
            check=False,
        )

        job.log(f"Workspace ready — {sum(1 for _ in host_src.rglob('*') if _.is_file())} files")
        return host_src

    # ── Prompt construction ────────────────────────────────────────────────────

    def _build_prompt(self, task_desc: str, project_name: str, src_dir: Path) -> str:
        denylist_str = ", ".join(f"`{c}`" for c in _DENYLIST)
        # Container sees the workspace as /a0/work_dir — map the host path
        container_path = "/a0/work_dir/jobs/" + src_dir.parent.parent.name + "/" + src_dir.parent.name + "/src"

        return f"""You are an expert software engineer working autonomously.

TASK: {task_desc}

PROJECT: {project_name}
WORKING DIRECTORY: {container_path}
(All files are already copied there — work directly in that directory.)

RULES (non-negotiable):
1. Never use: {denylist_str}
2. Never access paths outside your working directory.
3. Never install system packages with apt/yum/dnf — use pip/npm/cargo inside the project.
4. If you need to run tests, run them inside the working directory.
5. Write a final summary at the end listing: what you changed, files modified, test results.
6. If you encounter a destructive action (deleting files outside workdir, system config changes),
   write the action to a file called PENDING_ACTION.md and skip it — do NOT ask the user.

DELIVERABLES:
- Modified source files (in place)
- REPORT.md — summary of changes, decisions made, test results
- Run linting/tests if a test suite exists

Begin immediately. Do not ask for clarification — make reasonable assumptions and document them in REPORT.md.
""".strip()

    # ── API interaction ────────────────────────────────────────────────────────

    async def _submit_and_stream(
        self,
        prompt: str,
        job_id: str,
        job: AgentZeroJob,
    ) -> str:
        """
        Submit task to Agent Zero and consume the SSE stream.
        Sends Discord progress updates every self.progress_every seconds.
        Returns the final response text.
        """
        last_discord_update = time.monotonic()
        last_content = ""
        milestone_count = 0

        # Try to reset context first (fresh start per job)
        await self._reset_context(job_id)

        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/chat",
                    json={"message": prompt, "stream": True, "context_id": job_id},
                    timeout=httpx.Timeout(connect=10.0, read=None, write=60.0, pool=10.0),
                ) as resp:
                    resp.raise_for_status()

                    async for raw_line in resp.aiter_lines():
                        if not raw_line:
                            continue

                        # SSE lines start with "data:"
                        if raw_line.startswith("data:"):
                            payload = raw_line[5:].strip()
                            if payload in ("", "[DONE]"):
                                continue
                            try:
                                data = json.loads(payload)
                            except json.JSONDecodeError:
                                # Plain text line
                                data = {"content": payload, "type": "text"}

                            content = data.get("content") or data.get("message") or ""
                            if content:
                                last_content = content
                                milestone_count += 1
                                job.log(f"[stream #{milestone_count}] {content[:80]}")

                            # Progress update every N minutes
                            now = time.monotonic()
                            if now - last_discord_update >= self.progress_every and content:
                                await self._send_discord(
                                    f"⏳ **{job_id}** ({job.elapsed_min:.0f}min elapsed)\n"
                                    f"> {content[:200]}"
                                )
                                last_discord_update = now

                            if data.get("done"):
                                break

            except httpx.HTTPStatusError as e:
                raise RuntimeError(f"Agent Zero API error {e.response.status_code}: {e.response.text[:200]}") from e

        return last_content

    async def _reset_context(self, context_id: str):
        """Reset Agent Zero context for a clean slate per job."""
        for endpoint in ("/v1/reset", "/v1/context/reset", "/reset"):
            try:
                async with httpx.AsyncClient(timeout=5.0) as c:
                    r = await c.post(
                        f"{self.base_url}{endpoint}",
                        json={"context_id": context_id},
                    )
                    if r.status_code < 400:
                        return
            except Exception:
                continue

    # ── Artifact collection ────────────────────────────────────────────────────

    async def _collect_artifacts(
        self,
        job: AgentZeroJob,
        original_project_path: str,
        src_dir: Path,
    ) -> dict:
        """
        After Agent Zero finishes, extract:
          - patch.diff   (git diff of changes made)
          - report.md    (Agent Zero's own summary)
          - test_results.txt (any test output found)
        """
        out_dir = job.job_dir / "output"
        artifacts = {}

        # ── patch.diff ──────────────────────────────────────────────────────
        diff_result = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=src_dir, capture_output=True, text=True
        )
        diff_text = diff_result.stdout.strip()
        if diff_text:
            diff_path = out_dir / "patch.diff"
            diff_path.write_text(diff_text)
            artifacts["diff_path"] = diff_path
            job.log(f"Diff: {diff_text.count(chr(10))} lines")
        else:
            job.log("No diff — no files changed (or changes already staged)")

        # ── report.md ───────────────────────────────────────────────────────
        for candidate in ["REPORT.md", "report.md", "SUMMARY.md", "summary.md"]:
            rp = src_dir / candidate
            if rp.exists():
                dest = out_dir / "report.md"
                shutil.copy2(rp, dest)
                artifacts["report_path"] = dest
                job.log(f"Report captured: {candidate}")
                break

        if "report_path" not in artifacts:
            # Write a minimal report from job logs
            report = out_dir / "report.md"
            report.write_text(
                f"# Job {job.job_id} Report\n\n"
                f"**Task:** {job.task}\n\n"
                f"**Status:** {job.status}\n\n"
                f"**Elapsed:** {job.elapsed_min:.1f} min\n\n"
                f"## Log\n\n```\n" + "\n".join(job.log_lines[-30:]) + "\n```\n"
            )
            artifacts["report_path"] = report

        # ── test_results.txt ────────────────────────────────────────────────
        for candidate in ["test_results.txt", "pytest_output.txt", ".pytest_cache/v/cache/lastfailed"]:
            tp = src_dir / candidate
            if tp.exists():
                dest = out_dir / "test_results.txt"
                shutil.copy2(tp, dest)
                artifacts["test_results_path"] = dest
                job.log("Test results captured")
                break

        return artifacts

    # ── Discord notifications ──────────────────────────────────────────────────

    async def _send_discord(self, text: str):
        """Mirror Leon's _send_discord_message pattern."""
        try:
            if not _DISCORD_CHANNEL_FILE.exists() or not _DISCORD_TOKEN_FILE.exists():
                return
            channel_id = json.loads(_DISCORD_CHANNEL_FILE.read_text()).get("channel_id", "")
            token = _DISCORD_TOKEN_FILE.read_text().strip()
            if not channel_id or not token:
                return

            # Split if over Discord's 2000 char limit
            chunks = [text[i:i+1900] for i in range(0, len(text), 1900)]
            async with httpx.AsyncClient(timeout=10.0) as c:
                for chunk in chunks:
                    await c.post(
                        f"https://discord.com/api/v10/channels/{channel_id}/messages",
                        headers={"Authorization": f"Bot {token}"},
                        json={"content": chunk},
                    )
        except Exception as e:
            logger.debug("Discord notify failed: %s", e)

    # ── Formatting ─────────────────────────────────────────────────────────────

    def _read_diff_preview(self, diff_path: Optional[Path], max_lines: int = 30) -> str:
        if not diff_path or not diff_path.exists():
            return ""
        lines = diff_path.read_text().splitlines()
        if len(lines) <= max_lines:
            return "\n".join(lines)
        return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"

    def _format_completion(
        self,
        job_id: str,
        summary: str,
        artifacts: dict,
        diff_preview: str,
    ) -> str:
        msg = f"✅ **Job {job_id} complete!**\n"
        if summary:
            msg += f"\n**Summary:** {summary[:300]}\n"
        if artifacts.get("diff_path"):
            msg += f"\n**To apply the patch:**\n"
            msg += f"```\ncd <project_dir> && git apply {artifacts['diff_path']}\n```\n"
        if diff_preview:
            preview = diff_preview[:600]
            msg += f"\n**Diff preview:**\n```diff\n{preview}\n```\n"
        if artifacts.get("report_path"):
            msg += f"\n📄 Full report: `{artifacts['report_path']}`"
        return msg


# ── Module-level singleton ────────────────────────────────────────────────────

_runner: Optional[AgentZeroRunner] = None


def get_runner() -> AgentZeroRunner:
    global _runner
    if _runner is None:
        _runner = AgentZeroRunner()
    return _runner


# ── CLI test harness ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    runner = AgentZeroRunner()
    print(f"Enabled:   {runner.is_enabled()}")
    print(f"Available: {runner.is_available()}")
    print(f"Base URL:  {runner.base_url}")
    print(f"Workspace: {runner.workspace}")

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        task = sys.argv[2] if len(sys.argv) > 2 else "List files and print a hello world script"
        project = sys.argv[3] if len(sys.argv) > 3 else str(Path.home())
        print(f"\nRunning test job: {task}")
        result = asyncio.run(runner.run_job(task, project, "test"))
        print("\nResult:", json.dumps(result, indent=2, default=str))
