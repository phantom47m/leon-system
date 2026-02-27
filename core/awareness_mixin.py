"""
AwarenessMixin — extracted from core/leon.py to keep that file manageable.

Contains: _ram_watchdog, _awareness_loop
All self.* references resolve through Leon's MRO at runtime.
"""

import asyncio
import functools
import logging
import subprocess

from .neural_bridge import BridgeMessage, MSG_STATUS_REQUEST

logger = logging.getLogger("leon")

# Default timeout for subprocess calls within the awareness loop.
# Prevents a hung git/python process from blocking the entire loop.
_SUBPROCESS_TIMEOUT = 30


async def _run_subprocess(*args, **kwargs) -> subprocess.CompletedProcess:
    """Run subprocess.run in a thread pool with a default timeout.

    Prevents blocking the async event loop and ensures hung processes
    are killed after ``_SUBPROCESS_TIMEOUT`` seconds.
    """
    kwargs.setdefault("timeout", _SUBPROCESS_TIMEOUT)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return await asyncio.to_thread(functools.partial(subprocess.run, *args, **kwargs))


class AwarenessMixin:
    """Background awareness loop and RAM watchdog."""

    async def _ram_watchdog(self):
        """Monitor RAM every 60s. If above 80%, kill runaway OpenClaw renderer tabs."""
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
                    r = await _run_subprocess(
                        ["pgrep", "-f", "remote-debugging-port=18800"],
                    )
                    pids = r.stdout.strip().split()
                    # Keep 2, kill the rest (oldest first = lowest PIDs)
                    pids_sorted = sorted(int(p) for p in pids if p.isdigit())
                    to_kill = pids_sorted[:-2] if len(pids_sorted) > 2 else []
                    if to_kill:
                        for pid in to_kill:
                            try:
                                await _run_subprocess(
                                    ["kill", str(pid)], check=False, timeout=5
                                )
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
            except subprocess.TimeoutExpired as e:
                logger.warning("RAM watchdog: subprocess timed out: %s", e)
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

                        # Safety gate: if the agent worked on leon-system, verify we
                        # still import cleanly before accepting the result.
                        # A broken import = auto-revert so Leon can always restart.
                        task_obj = self.task_queue.active_tasks.get(agent_id, {})
                        _is_self = task_obj.get("project_name", "").lower() in ("leon system", "leon-system")
                        if _is_self:
                            import pathlib as _pathlib
                            _project_root = str(_pathlib.Path(__file__).parent.parent)
                            try:
                                _check = await _run_subprocess(
                                    ["venv/bin/python", "-c", "from core.leon import Leon; print('ok')"],
                                    cwd=_project_root, timeout=60,
                                )
                            except subprocess.TimeoutExpired:
                                logger.error("Self-agent import check timed out after 60s")
                                _check = type("FakeResult", (), {"returncode": 1, "stderr": "import check timed out"})()
                            if _check.returncode != 0:
                                logger.error("Self-agent broke Leon imports — auto-reverting last commit")
                                try:
                                    await _run_subprocess(
                                        ["git", "revert", "--no-edit", "HEAD"],
                                        cwd=_project_root,
                                    )
                                    await _run_subprocess(
                                        ["git", "push"], cwd=_project_root,
                                    )
                                except subprocess.TimeoutExpired:
                                    logger.error("Git revert/push timed out during self-agent safety revert")
                                await self._send_discord_message(
                                    "⚠️ **Self-agent safety revert** — last commit broke Leon's imports. "
                                    "Reverted automatically. Check the diff before re-queuing.",
                                    channel="chat",
                                )
                                results["summary"] = f"[REVERTED] Import check failed: {_check.stderr[:200]}"

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

                        # Post to Discord #dev so owner can see completions
                        _summary = results.get("summary", "no summary") or "no summary"
                        _files = results.get("files_modified", [])
                        _dur = status.get("duration_seconds", 0)
                        _files_str = (", ".join(_files[:5]) + (" ..." if len(_files) > 5 else "")) if _files else "no files"
                        asyncio.create_task(self._send_discord_message(
                            f"✅ **Agent done** ({int(_dur)}s)\n"
                            f"**Summary:** {_summary[:300]}\n"
                            f"**Files:** {_files_str}",
                            channel="dev",
                        ))

                        # Auto mode: mark done + refill queue if empty so it never stops.
                        # Always works on Leon System only — self-improvement loop.
                        self.night_mode.mark_agent_completed(agent_id, results.get("summary", ""))
                        if self.night_mode.active and not self.night_mode.get_pending():
                            continuation = (
                                "Continue improving the Leon System codebase (this AI's own code). "
                                "Read LEON_PROGRESS.md to see what has already been done. "
                                "Find the next highest-value thing to improve — bugs, missing error handling, "
                                "performance, response quality, new features, integration improvements, "
                                "code quality, security hardening, anything that makes Leon smarter or more reliable. "
                                "Do ONE focused thing. Do not repeat work already in LEON_PROGRESS.md. "
                                "Commit your changes with a clear message. "
                                "Append a one-line summary to LEON_PROGRESS.md."
                            )
                            self.night_mode.add_task(continuation, "Leon System")
                            logger.info("Auto mode: queued self-improvement continuation for Leon System")
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

                        # Post to Discord #dev so owner can see failures
                        asyncio.create_task(self._send_discord_message(
                            f"❌ **Agent failed**\n{raw_error[:300]}",
                            channel="dev",
                        ))

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
                            timeout_s = sched_task.get("max_runtime_minutes", 60) * 60
                            try:
                                if cmd.startswith("__") and cmd.endswith("__"):
                                    # Built-in scheduler commands — dispatch directly
                                    from .scheduler import run_builtin
                                    success, msg = await asyncio.wait_for(
                                        run_builtin(cmd, leon=self),
                                        timeout=timeout_s,
                                    )
                                    if success:
                                        self.scheduler.mark_completed(sched_task["name"])
                                    else:
                                        self.scheduler.mark_failed(sched_task["name"], msg)
                                else:
                                    await asyncio.wait_for(
                                        self.process_user_input(cmd),
                                        timeout=timeout_s,
                                    )
                                    self.scheduler.mark_completed(sched_task["name"])
                            except asyncio.TimeoutError:
                                _timeout_msg = (
                                    f"Timed out after {sched_task.get('max_runtime_minutes', 60)}m"
                                )
                                logger.error(
                                    f"Scheduled task timed out: {sched_task['name']} ({_timeout_msg})"
                                )
                                self.scheduler.mark_failed(sched_task["name"], _timeout_msg)
                            except Exception as e:
                                logger.error(f"Scheduled task failed: {sched_task['name']}: {e}")
                                self.scheduler.mark_failed(sched_task["name"], str(e))

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

                # Proactive Discord update every 10 minutes when agents are running
                import time as _time
                _now_ts = _time.monotonic()
                _active_agents = list(self.agent_manager.active_agents.keys())
                _plan_running  = self.plan_mode.active if self.plan_mode else False
                _night_running = self.night_mode.active if self.night_mode else False
                if (_active_agents or _plan_running or _night_running) and \
                        _now_ts - self._last_discord_update >= 600:  # 600s = 10 min
                    self._last_discord_update = _now_ts
                    try:
                        _count = len(_active_agents)
                        _nm_pending = len(self.night_mode.get_pending()) if self.night_mode else 0
                        if _plan_running and self.plan_mode:
                            _ps = self.plan_mode.get_status()
                            _update = (
                                f"**Plan update** — {_ps.get('goal','')[:60]}\n"
                                f"{_ps['doneTasks']}/{_ps['totalTasks']} tasks done"
                                + (f", {_ps['runningTasks']} running" if _ps['runningTasks'] else "")
                                + (f", {_ps['failedTasks']} failed" if _ps['failedTasks'] else "")
                            )
                        else:
                            # Build a meaningful update — show last git commit per active project
                            _lines = [f"**Agent update** — {_count} agent{'s' if _count != 1 else ''} running" + (f", {_nm_pending} queued" if _nm_pending else "")]
                            _seen_paths = set()
                            for _aid in _active_agents:
                                _tobj = self.task_queue.active_tasks.get(_aid, {})
                                _proj_name = _tobj.get("project_name", "")
                                _proj = self._resolve_project(_proj_name)
                                _proj_path = _proj.get("path", "") if _proj else ""
                                if _proj_path and _proj_path not in _seen_paths:
                                    _seen_paths.add(_proj_path)
                                    try:
                                        _git = await _run_subprocess(
                                            ["git", "log", "--oneline", "-1"],
                                            cwd=_proj_path,
                                        )
                                        _last_commit = _git.stdout.strip() if _git.returncode == 0 else "no commits yet"
                                    except subprocess.TimeoutExpired:
                                        _last_commit = "(git timed out)"
                                    _lines.append(f"**{_proj_name or 'Unknown'}:** {_last_commit[:120]}")
                            _update = "\n".join(_lines)
                        await self._send_discord_message(_update)
                    except Exception as _e:
                        logger.debug("Discord update tick error: %s", _e)

                # Periodic save
                self.memory.save()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Awareness loop error: {e}")

            await asyncio.sleep(10)
