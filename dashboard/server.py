"""
Leon Brain Dashboard — Web server for the 3D neural visualization.

Serves the Three.js brain visualization and provides real-time
WebSocket updates from Leon's core system.

Usage:
    python3 dashboard/server.py          # standalone
    # or imported and started by main.py
"""

import asyncio
import json
import logging
import os
import secrets
import time
from datetime import datetime
from pathlib import Path

from collections import defaultdict

from aiohttp import web

logger = logging.getLogger("leon.dashboard")

# Rate limiting for /api/message
_rate_limit_window = 60  # seconds
_rate_limit_max = 20  # max requests per window per IP
_rate_limit_buckets: dict[str, list[float]] = defaultdict(list)

DASHBOARD_DIR = Path(__file__).parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"

# Connected WebSocket clients (unauthenticated, waiting for auth)
ws_clients: set[web.WebSocketResponse] = set()

# Authenticated WebSocket clients (passed auth check)
ws_authenticated: set[web.WebSocketResponse] = set()

# Startup time for uptime tracking
_start_time = time.monotonic()

# Auth timeout for new connections
WS_AUTH_TIMEOUT = 5.0


# ── Routes ───────────────────────────────────────────────

async def index(request):
    """Serve the main brain dashboard page."""
    html = (TEMPLATES_DIR / "index.html").read_text()
    return web.Response(text=html, content_type="text/html")


async def health(request):
    """Health check endpoint."""
    uptime = int(time.monotonic() - _start_time)
    leon = request.app.get("leon_core")
    return web.json_response({
        "status": "ok",
        "uptime": uptime,
        "clients": len(ws_authenticated),
        "leon_core": leon is not None,
    })


async def api_health(request):
    """
    GET /api/health — Detailed system health for monitoring/widgets.
    No auth required — read-only system stats.
    """
    import shutil

    leon = request.app.get("leon_core")
    uptime = int(time.monotonic() - _start_time)

    # System stats
    cpu_line = ""
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        idle = int(parts[4])
        total = sum(int(x) for x in parts[1:])
        cpu_line = f"{100.0 * (1 - idle / total):.1f}%"
    except Exception:
        cpu_line = "unknown"

    # Memory
    mem = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    mem["total_mb"] = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable"):
                    mem["available_mb"] = int(line.split()[1]) // 1024
        mem["used_mb"] = mem.get("total_mb", 0) - mem.get("available_mb", 0)
        mem["percent"] = f"{100 * mem['used_mb'] / max(mem['total_mb'], 1):.1f}%"
    except Exception:
        pass

    # Disk
    disk = {}
    try:
        usage = shutil.disk_usage("/")
        disk["total_gb"] = round(usage.total / (1024**3), 1)
        disk["used_gb"] = round(usage.used / (1024**3), 1)
        disk["free_gb"] = round(usage.free / (1024**3), 1)
        disk["percent"] = f"{100 * usage.used / max(usage.total, 1):.1f}%"
    except Exception:
        pass

    # Leon stats
    leon_stats = {}
    if leon:
        try:
            status = leon.get_status()
            tasks = status.get("tasks", {})
            leon_stats = {
                "active_agents": status.get("active_agents", 0),
                "active_tasks": tasks.get("active", 0),
                "queued_tasks": tasks.get("queued", 0),
                "completed_tasks": tasks.get("completed", 0),
                "brain_role": status.get("brain_role", "unified"),
                "bridge_connected": status.get("bridge_connected", False),
                "screen": status.get("screen", {}),
                "notifications": status.get("notifications", {}),
            }
        except Exception:
            pass

    return web.json_response({
        "status": "ok",
        "uptime_seconds": uptime,
        "ws_clients": len(ws_authenticated),
        "cpu": cpu_line,
        "memory": mem,
        "disk": disk,
        "leon": leon_stats,
        "timestamp": datetime.now().isoformat(),
    })


async def api_message(request):
    """
    POST /api/message — HTTP API for external integrations (WhatsApp bridge, etc.)

    Expects JSON: {"message": "user text"}
    Requires: Authorization: Bearer <token>
    Returns JSON: {"response": "leon's reply", "timestamp": "HH:MM"}
    """
    # ── Rate limiting ──
    client_ip = request.remote or "unknown"
    now_ts = time.monotonic()
    bucket = _rate_limit_buckets[client_ip]
    # Prune old entries
    bucket[:] = [t for t in bucket if now_ts - t < _rate_limit_window]
    if len(bucket) >= _rate_limit_max:
        return web.json_response(
            {"error": f"Rate limited — max {_rate_limit_max} requests per {_rate_limit_window}s"},
            status=429,
        )
    bucket.append(now_ts)

    # ── Auth check ──
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return web.json_response({"error": "Missing Bearer token"}, status=401)

    token = auth_header[7:]
    session_token = request.app.get("session_token", "")
    api_token = request.app.get("api_token", "")

    if token != session_token and token != api_token:
        return web.json_response({"error": "Invalid token"}, status=403)

    # ── Parse body ──
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    message = body.get("message", "").strip()
    if not message:
        return web.json_response({"error": "Empty message"}, status=400)

    source = body.get("source", "api")
    leon = request.app.get("leon_core")

    # ── Audit log ──
    if leon and leon.audit_log:
        leon.audit_log.log(
            "api_message",
            f"[{source}] {message[:120]}",
            "info",
        )

    # ── Broadcast incoming message to dashboard ──
    timestamp = datetime.now().strftime("%H:%M")
    await _broadcast_ws(request.app, {
        "type": "input_response",
        "message": f"[{source}] {message}",
        "timestamp": timestamp,
        "source": source,
        "direction": "incoming",
    })

    # ── Process through Leon ──
    if leon and hasattr(leon, "process_user_input"):
        try:
            response = await leon.process_user_input(message)
        except Exception as e:
            logger.error(f"API message processing error: {e}")
            response = f"Error processing message: {e}"
    else:
        response = f"[Demo] Received: {message}"

    # ── Broadcast response to dashboard ──
    await _broadcast_ws(request.app, {
        "type": "input_response",
        "message": response,
        "timestamp": datetime.now().strftime("%H:%M"),
        "source": source,
        "direction": "outgoing",
    })

    return web.json_response({
        "response": response,
        "timestamp": datetime.now().strftime("%H:%M"),
    })


async def _broadcast_ws(app, data: dict):
    """Push a message to all authenticated WebSocket clients."""
    dead = set()
    for ws in ws_authenticated:
        try:
            await ws.send_json(data)
        except Exception:
            dead.add(ws)
    ws_authenticated.difference_update(dead)


async def websocket_handler(request):
    """WebSocket endpoint for real-time brain state updates."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    session_token = request.app.get("session_token", "")

    # ── Require authentication as first message ──
    ws_clients.add(ws)
    logger.info(f"Dashboard client connected, awaiting auth ({len(ws_clients)} pending)")

    authenticated = False
    try:
        # Wait for auth message within timeout
        auth_msg = await asyncio.wait_for(ws.receive(), timeout=WS_AUTH_TIMEOUT)
        if auth_msg.type == web.WSMsgType.TEXT:
            try:
                data = json.loads(auth_msg.data)
            except (json.JSONDecodeError, ValueError):
                data = {}

            if (data.get("command") == "auth"
                    and data.get("token") == session_token):
                authenticated = True
                ws_authenticated.add(ws)
                await ws.send_json({"type": "auth_result", "success": True})
                logger.info(f"Dashboard client authenticated ({len(ws_authenticated)} total)")
            else:
                await ws.send_json({"type": "auth_result", "success": False,
                                    "message": "Invalid token"})
                logger.warning("Dashboard client failed authentication")
                await ws.close()
                return ws
        else:
            await ws.close()
            return ws
    except asyncio.TimeoutError:
        logger.warning("Dashboard client did not authenticate within timeout")
        await ws.send_json({"type": "auth_result", "success": False,
                            "message": "Auth timeout"})
        await ws.close()
        return ws
    finally:
        ws_clients.discard(ws)

    if not authenticated:
        return ws

    # ── Send initial state ──
    leon = request.app.get("leon_core")
    if leon:
        state = _build_state(leon)
        await ws.send_json(state)

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Received malformed JSON on WebSocket")
                    continue
                # Handle commands from dashboard
                if data.get("command") == "status":
                    if leon:
                        await ws.send_json(_build_state(leon))
                elif data.get("command") == "input":
                    user_msg = data.get("message", "").strip()

                    # Slash commands — handled directly, no API call
                    if user_msg.startswith("/") and leon:
                        slash_response = _handle_slash_command(user_msg, leon, request.app)
                        await ws.send_json({
                            "type": "input_response",
                            "message": slash_response,
                            "timestamp": datetime.now().strftime("%H:%M"),
                        })
                    elif leon and hasattr(leon, "process_user_input"):
                        try:
                            response = await leon.process_user_input(user_msg)
                            await ws.send_json({
                                "type": "input_response",
                                "message": str(response),
                                "timestamp": datetime.now().strftime("%H:%M"),
                            })
                        except Exception as e:
                            await ws.send_json({
                                "type": "input_response",
                                "message": f"Error: {e}",
                                "timestamp": datetime.now().strftime("%H:%M"),
                            })
                    else:
                        await ws.send_json({
                            "type": "input_response",
                            "message": f"[Demo] Received: {user_msg}",
                            "timestamp": datetime.now().strftime("%H:%M"),
                        })
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")
    finally:
        ws_authenticated.discard(ws)
        ws_clients.discard(ws)
        logger.info(f"Dashboard client disconnected ({len(ws_authenticated)} authenticated)")

    return ws


async def broadcast_state(app):
    """Background task that broadcasts brain state to all connected clients."""
    global ws_authenticated
    leon = app.get("leon_core")
    while True:
        if ws_authenticated and leon:
            state = _build_state(leon)
            dead = set()
            for ws in ws_authenticated:
                try:
                    await ws.send_json(state)
                except Exception:
                    dead.add(ws)
            ws_authenticated -= dead

        await asyncio.sleep(2)  # Update every 2 seconds


def _handle_slash_command(command: str, leon, app=None) -> str:
    """Handle dashboard slash commands directly — no API call needed."""
    parts = command.strip().split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    try:
        if cmd == "/agents":
            agents = leon.agent_manager.active_agents
            if not agents:
                return "No active agents."
            lines = [f"**Active Agents ({len(agents)}):**\n"]
            for aid, info in agents.items():
                status = info.get("status", "unknown")
                elapsed = ""
                try:
                    started = datetime.fromisoformat(info.get("started_at", ""))
                    secs = int((datetime.now() - started).total_seconds())
                    elapsed = f" ({secs // 60}m {secs % 60}s)"
                except Exception:
                    pass
                lines.append(f"- `{aid}` [{status}]{elapsed} — {info.get('brief_path', 'N/A')}")
            return "\n".join(lines)

        elif cmd == "/status":
            status = leon.get_status()
            tasks = status.get("tasks", {})
            uptime_s = int(time.monotonic() - _start_time)
            h, m, s = uptime_s // 3600, (uptime_s % 3600) // 60, uptime_s % 60
            lines = [
                "**System Status:**\n",
                f"- Uptime: {h:02d}:{m:02d}:{s:02d}",
                f"- Brain role: {status.get('brain_role', 'unified')}",
                f"- Active agents: {tasks.get('active', 0)}",
                f"- Queued tasks: {tasks.get('queued', 0)}",
                f"- Completed: {tasks.get('completed', 0)}",
                f"- Max concurrent: {tasks.get('max_concurrent', 5)}",
            ]
            if status.get("brain_role") == "left":
                lines.append(f"- Bridge connected: {status.get('bridge_connected', False)}")
                lines.append(f"- Right Brain online: {status.get('right_brain_online', False)}")
            return "\n".join(lines)

        elif cmd == "/kill":
            if not arg:
                return "Usage: `/kill <agent_id>`"
            # Permission check
            if leon.permissions and not leon.permissions.check_permission("modify_system"):
                return "Permission denied: `modify_system` required. Use `/approve modify_system` to grant."
            agents = leon.agent_manager.active_agents
            # Match partial agent ID
            match = None
            for aid in agents:
                if arg in aid:
                    match = aid
                    break
            if not match:
                return f"Agent not found: `{arg}`"
            asyncio.create_task(leon.agent_manager.terminate_agent(match))
            return f"Terminating agent `{match}`..."

        elif cmd == "/queue":
            summary = leon.task_queue.get_status_summary()
            queued = summary.get("queued_tasks", [])
            if not queued:
                return "Queue is empty."
            lines = [f"**Queued Tasks ({len(queued)}):**\n"]
            for i, t in enumerate(queued, 1):
                lines.append(f"{i}. {t.get('description', 'N/A')[:60]} (project: {t.get('project', 'unknown')})")
            return "\n".join(lines)

        elif cmd == "/retry":
            if not arg:
                return "Usage: `/retry <agent_id>`"
            # Find the agent in completed/failed tasks
            agents = leon.agent_manager.active_agents
            match = None
            for aid in agents:
                if arg in aid:
                    match = aid
                    break
            if match:
                asyncio.create_task(leon.agent_manager._retry_agent(match))
                return f"Retrying agent `{match}`..."
            return f"Agent not found: `{arg}`. Use `/agents` to see active agents."

        elif cmd == "/history":
            completed = leon.task_queue.completed[-10:]
            if not completed:
                return "No completed tasks yet."
            lines = ["**Recent Completed Tasks:**\n"]
            for t in reversed(completed):
                status_icon = "+" if t.get("status") == "completed" else "x"
                ts = t.get("completed_at") or t.get("failed_at") or ""
                if ts:
                    try:
                        ts = datetime.fromisoformat(ts).strftime("%H:%M")
                    except Exception:
                        pass
                desc = t.get("description", "N/A")[:50]
                lines.append(f"[{status_icon}] {ts} — {desc}")
            return "\n".join(lines)

        elif cmd == "/bridge":
            if leon.brain_role != "left":
                return "Bridge info only available in Left Brain mode."
            connected = leon.bridge.connected if leon.bridge else False
            rb_status = leon._right_brain_status
            lines = [
                "**Neural Bridge Status:**\n",
                f"- Connected: {connected}",
                f"- Right Brain agents: {rb_status.get('active_agents', 0)}",
                f"- Right Brain queued: {rb_status.get('queued', 0)}",
                f"- Right Brain completed: {rb_status.get('completed', 0)}",
            ]
            return "\n".join(lines)

        elif cmd == "/setkey":
            if not arg:
                return "Usage: `/setkey <api_key>`"
            # Store key in vault and set in environment
            if leon.vault and leon.vault._unlocked:
                leon.vault.store("ANTHROPIC_API_KEY", arg)
                os.environ["ANTHROPIC_API_KEY"] = arg
                if hasattr(leon, 'api') and hasattr(leon.api, 'set_api_key'):
                    leon.api.set_api_key(arg)
                if leon.audit_log:
                    leon.audit_log.log("set_api_key", "API key updated via dashboard", "info")
                return "API key stored in vault and activated. Leon can now respond to messages."
            else:
                # Vault not unlocked — just set env
                os.environ["ANTHROPIC_API_KEY"] = arg
                if hasattr(leon, 'api') and hasattr(leon.api, 'set_api_key'):
                    leon.api.set_api_key(arg)
                return "API key set in environment (vault locked — key not persisted). Leon can now respond."

        elif cmd == "/vault":
            if arg == "list" or not arg:
                if not leon.vault:
                    return "Vault module not loaded."
                if not leon.vault._unlocked:
                    return "Vault is locked. Set LEON_MASTER_KEY env var to unlock."
                keys = leon.vault.list_keys()
                if not keys:
                    return "Vault is empty — no keys stored."
                lines = ["**Vault Keys:**\n"]
                for k in keys:
                    lines.append(f"- `{k}`")
                return "\n".join(lines)
            else:
                return "Usage: `/vault list`"

        elif cmd == "/approve":
            if not arg:
                valid = ", ".join(sorted(leon.permissions.REQUIRE_APPROVAL)) if leon.permissions else "N/A"
                return f"Usage: `/approve <action>`\n\nActions requiring approval:\n{valid}"
            if not leon.permissions:
                return "Permission system not loaded."
            action = arg.strip()
            leon.permissions.grant_temporary(action, duration_minutes=30)
            return f"Temporary permission granted for `{action}` (30 minutes)."

        elif cmd == "/login":
            if not arg:
                return "Usage: `/login <pin>`"
            if not leon.owner_auth:
                return "Auth module not loaded."
            if leon.owner_auth.verify_pin(arg):
                return "Authentication successful."
            else:
                remaining = leon.owner_auth.max_attempts - leon.owner_auth.failed_attempts
                return f"Authentication failed. {remaining} attempts remaining."

        elif cmd == "/schedule":
            if hasattr(leon, 'scheduler'):
                schedule = leon.scheduler.get_schedule_summary()
                if not schedule:
                    return "No scheduled tasks configured. Add them to `scheduler.tasks` in settings.yaml."
                lines = ["**Scheduled Tasks:**\n"]
                for s in schedule:
                    status = "ON" if s["enabled"] else "OFF"
                    lines.append(
                        f"- [{status}] **{s['name']}** — every {s['interval_hours']}h "
                        f"(last: {s['last_run']}, next: {s['next_run']})"
                    )
                return "\n".join(lines)
            return "Scheduler not available."

        elif cmd == "/search":
            if not arg:
                return "Usage: `/search <query>` — search agent history by description, project, or files"
            if hasattr(leon, 'agent_index'):
                results = leon.agent_index.search(arg, limit=10)
                if not results:
                    return f"No results for `{arg}`."
                lines = [f"**Search results for `{arg}`:**\n"]
                for r in results:
                    status_icon = "+" if r.get("status") == "completed" else "x"
                    ts = (r.get("completed_at") or r.get("spawned_at") or "")[:16]
                    dur = r.get("duration_seconds")
                    dur_str = f" ({dur:.0f}s)" if dur else ""
                    desc = r.get("description", "N/A")[:50]
                    lines.append(f"[{status_icon}] {ts}{dur_str} — {desc} ({r.get('project', '?')})")
                return "\n".join(lines)
            return "Agent index not available."

        elif cmd == "/stats":
            if hasattr(leon, 'agent_index'):
                stats = leon.agent_index.get_stats()
                lines = [
                    "**Agent Stats:**\n",
                    f"- Total runs: {stats['total_runs']}",
                    f"- Completed: {stats['completed']}",
                    f"- Failed: {stats['failed']}",
                    f"- Running: {stats['running']}",
                    f"- Success rate: {stats['success_rate']}",
                    "\n**By project:**",
                ]
                for proj, count in sorted(stats['projects'].items(), key=lambda x: -x[1]):
                    lines.append(f"- {proj}: {count} runs")
                return "\n".join(lines)
            return "Agent index not available."

        elif cmd == "/notifications":
            if hasattr(leon, 'notifications'):
                if arg == "stats":
                    stats = leon.notifications.get_stats()
                    return (
                        f"**Notification Stats:**\n\n"
                        f"- Total sent: {stats['total']}\n"
                        f"- Pending: {stats['pending']}\n"
                        f"- By source: {json.dumps(stats['by_source'])}\n"
                        f"- By priority: {json.dumps(stats['by_priority'])}"
                    )
                recent = leon.notifications.get_recent(15)
                if not recent:
                    return "No notifications yet."
                lines = ["**Recent Notifications:**\n"]
                for n in reversed(recent):
                    icon = {"LOW": "-", "NORMAL": "*", "HIGH": "!", "URGENT": "!!!"}.get(n["priority"], "*")
                    delivered = "sent" if n["delivered"] else "dropped"
                    ts = n["timestamp"][:16] if n["timestamp"] else ""
                    lines.append(f"[{icon}] {ts} [{n['source']}] **{n['title']}** — {n['message'][:80]} ({delivered})")
                return "\n".join(lines)
            return "Notification system not available."

        elif cmd == "/screen":
            if hasattr(leon, 'screen_awareness'):
                ctx = leon.screen_awareness.get_context()
                if arg == "history":
                    history = leon.screen_awareness.get_recent_activity(10)
                    if not history:
                        return "No screen activity recorded yet."
                    lines = ["**Screen Activity History:**\n"]
                    for h in reversed(history):
                        ts = h.get("timestamp", "")[:16]
                        err = " [ERROR VISIBLE]" if h.get("error_visible") else ""
                        lines.append(f"[{ts}] {h.get('activity', '?')} ({h.get('category', '?')}){err}")
                    return "\n".join(lines)
                return (
                    f"**Screen Awareness:**\n\n"
                    f"- Active app: {ctx.get('active_app', 'unknown')}\n"
                    f"- Activity: {ctx.get('activity', 'unknown')}\n"
                    f"- Category: {ctx.get('category', 'N/A')}\n"
                    f"- Mood: {ctx.get('mood', 'N/A')}\n"
                    f"- Last update: {ctx.get('last_update', 'never')}\n"
                    f"- Monitoring: {'ON' if ctx.get('monitoring') else 'OFF'}\n"
                    f"- History entries: {ctx.get('history_count', 0)}\n\n"
                    f"Use `/screen history` for activity log."
                )
            return "Screen awareness not available."

        elif cmd == "/gpu":
            if hasattr(leon, 'system_skills'):
                result = leon.system_skills.gpu_usage()
                return result
            return "System skills not available."

        elif cmd == "/clipboard":
            if hasattr(leon, 'system_skills'):
                if arg == "history":
                    result = leon.system_skills.clipboard_history()
                    return result
                result = leon.system_skills.clipboard_get()
                return result
            return "System skills not available."

        elif cmd == "/changes":
            if hasattr(leon, 'project_watcher'):
                if arg:
                    changes = leon.project_watcher.get_recent_changes(arg, 20)
                    if not changes:
                        return f"No recent changes for `{arg}`."
                    lines = [f"**Recent changes in {arg}:**\n"]
                    for c in reversed(changes[-15:]):
                        ts = c["timestamp"][11:19]
                        path = Path(c["path"]).name
                        lines.append(f"[{ts}] {c['type']}: {path}")
                    return "\n".join(lines)
                summary = leon.project_watcher.get_all_changes_summary()
                if not summary or all(v == 0 for v in summary.values()):
                    return "No file changes detected yet."
                lines = ["**Project Changes:**\n"]
                for name, count in summary.items():
                    lines.append(f"- **{name}**: {count} changes")
                lines.append("\nUse `/changes <project>` for details.")
                return "\n".join(lines)
            return "Project watcher not available."

        elif cmd == "/export":
            if hasattr(leon, 'memory'):
                recent = leon.memory.get_recent_context(limit=100)
                if not recent:
                    return "No conversation history to export."
                lines = [f"# Leon Conversation Export — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
                for msg in recent:
                    role = msg.get("role", "?").upper()
                    content = msg.get("content", "")
                    ts = msg.get("timestamp", "")[:19]
                    lines.append(f"**[{ts}] {role}:**\n{content}\n")
                export_path = f"data/conversation_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
                with open(export_path, "w") as f:
                    f.write("\n".join(lines))
                return f"Exported {len(recent)} messages to `{export_path}`"
            return "Memory system not available."

        elif cmd == "/context":
            if hasattr(leon, 'memory'):
                conversations = len(leon.memory.get_recent_context(limit=9999))
                active = leon.memory.get_all_active_tasks()
                projects = leon.memory.list_projects()
                lines = [
                    "**Memory Context:**\n",
                    f"- Conversations stored: {conversations}",
                    f"- Active tasks: {len(active)}",
                    f"- Known projects: {len(projects)}",
                ]
                for p in projects:
                    ctx = leon.memory.get_project_context(p.get("name", ""))
                    ctx_size = len(json.dumps(ctx)) if ctx else 0
                    lines.append(f"  - {p.get('name', '?')}: {ctx_size} bytes context")
                if hasattr(leon, 'agent_index'):
                    stats = leon.agent_index.get_stats()
                    lines.append(f"- Agent history: {stats['total_runs']} runs indexed")
                if hasattr(leon, 'screen_awareness'):
                    lines.append(f"- Screen history: {len(leon.screen_awareness.history)} snapshots")
                return "\n".join(lines)
            return "Memory system not available."

        elif cmd == "/voice":
            if hasattr(leon, 'voice_system') and leon.hotkey_listener and leon.hotkey_listener.voice_system:
                vs = leon.hotkey_listener.voice_system
                state = vs.listening_state if hasattr(vs, 'listening_state') else {}
                return (
                    f"**Voice System:**\n\n"
                    f"- State: {state.get('state', 'unknown')}\n"
                    f"- Listening: {state.get('is_listening', False)}\n"
                    f"- Awake: {state.get('is_awake', False)}\n"
                    f"- Deepgram: {'healthy' if state.get('deepgram_healthy', True) else 'DEGRADED'}\n"
                    f"- ElevenLabs: {'degraded (using local TTS)' if state.get('elevenlabs_degraded', False) else 'active'}\n"
                    f"- Voice ID: {vs.voice_id}\n"
                    f"- Wake words: {'enabled' if vs.wake_words_enabled else 'disabled'}\n"
                    f"- Push-to-talk: {leon.hotkey_listener.ptt_key_name}"
                )
            return "Voice system not active. Start Leon with `--voice` or `--full` flag."

        elif cmd == "/restart":
            return (
                "To restart Leon:\n\n"
                "1. Run `./stop.sh` in the leon-system directory\n"
                "2. Run `./start.sh` to start everything back up\n\n"
                "Or if using systemd: `systemctl --user restart leon`"
            )

        elif cmd == "/whatsapp":
            import urllib.request
            try:
                with urllib.request.urlopen("http://127.0.0.1:3001/health", timeout=3) as resp:
                    data = json.loads(resp.read().decode())
                    ready = data.get("whatsapp_ready", False)
                    num = data.get("my_number", "unknown")
                    uptime = data.get("uptime_seconds", 0)
                    reconn = data.get("reconnect_count", 0)
                    return (
                        f"**WhatsApp Bridge:**\n\n"
                        f"- Status: {'CONNECTED' if ready else 'NOT READY'}\n"
                        f"- Phone: {num}\n"
                        f"- Uptime: {uptime // 60}m {uptime % 60}s\n"
                        f"- Reconnects: {reconn}"
                    )
            except Exception:
                return "WhatsApp bridge is not running. Start it with `./start.sh`"

        elif cmd == "/help":
            return (
                "**Dashboard Commands:**\n\n"
                "- `/agents` — list active agents with status\n"
                "- `/status` — system overview\n"
                "- `/kill <id>` — terminate an agent\n"
                "- `/queue` — show queued tasks\n"
                "- `/retry <id>` — retry a failed agent\n"
                "- `/history` — recent completed tasks\n"
                "- `/search <query>` — search agent history\n"
                "- `/stats` — agent run statistics\n"
                "- `/schedule` — view scheduled tasks\n"
                "- `/notifications` — recent notifications (`/notifications stats` for stats)\n"
                "- `/screen` — screen awareness status (`/screen history` for log)\n"
                "- `/gpu` — GPU usage and temperature\n"
                "- `/clipboard` — clipboard contents (`/clipboard history`)\n"
                "- `/changes` — file changes in projects (`/changes <project>`)\n"
                "- `/export` — export conversation history to markdown\n"
                "- `/context` — memory usage and context stats\n"
                "- `/bridge` — Right Brain connection status\n"
                "- `/setkey <key>` — store API key in vault\n"
                "- `/vault list` — show stored vault keys\n"
                "- `/approve <action>` — grant temporary permission\n"
                "- `/login <pin>` — authenticate as owner\n"
                "- `/voice` — voice system status\n"
                "- `/restart` — how to restart Leon\n"
                "- `/whatsapp` — WhatsApp bridge status\n"
                "- `/help` — this message"
            )

        else:
            return f"Unknown command: `{cmd}`. Type `/help` for available commands."

    except Exception as e:
        logger.error(f"Slash command error ({command}): {e}")
        return f"Error executing `{cmd}`: {e}"


def _build_state(leon) -> dict:
    """Build the brain state dict from Leon core."""
    uptime_seconds = int(time.monotonic() - _start_time)
    try:
        status = leon.get_status()
        tasks = status.get("tasks", {})
        raw_tasks = tasks.get("active_tasks", [])
        active_count = tasks.get("active", 0)
        queued_count = tasks.get("queued", 0)
        completed_count = tasks.get("completed", 0)
        max_concurrent = tasks.get("max_concurrent", 5)

        # Normalize active tasks for dashboard (ensure startedAt exists)
        active_tasks = []
        for t in raw_tasks:
            agent = dict(t)
            # Map created_at/started_at → startedAt for dashboard JS
            if "startedAt" not in agent:
                agent["startedAt"] = agent.get("started_at") or agent.get("created_at", "")
            active_tasks.append(agent)

        # Build activity feed
        feed = []
        now = datetime.now().strftime("%H:%M")

        # Add active task info
        for t in active_tasks:
            feed.append({
                "time": now,
                "message": f"⚡ Agent working: {t.get('description', 'unknown')[:40]}"
            })

        # Determine brain states
        left_active = True  # Left brain always listening
        right_active = active_count > 0
        bridge_active = right_active  # Bridge active when right brain has tasks

        # Fire signals when tasks complete
        signal = None
        if right_active:
            signal = "left-to-right" if len(feed) % 2 == 0 else "right-to-left"

        # Brain split status
        brain_role = status.get("brain_role", "unified")
        bridge_connected = status.get("bridge_connected", False)
        right_brain_online = status.get("right_brain_online", False)
        right_brain_status = status.get("right_brain_status", {})

        # If Left Brain with Right Brain connected, merge agent counts
        remote_active = right_brain_status.get("active_agents", 0)
        remote_queued = right_brain_status.get("queued", 0)
        total_agents = active_count + remote_active

        # Override bridge_active with real bridge state when in split mode
        if brain_role == "left":
            bridge_active = bridge_connected
            right_active = right_brain_online or active_count > 0

        return {
            "leftActive": left_active,
            "rightActive": right_active,
            "bridgeActive": bridge_active,
            "activeAgents": active_tasks,
            "agentCount": total_agents,
            "taskCount": queued_count + active_count + remote_active + remote_queued,
            "completedCount": completed_count,
            "queuedCount": queued_count + remote_queued,
            "maxConcurrent": max_concurrent,
            "uptime": uptime_seconds,
            "taskFeed": feed,
            "signal": signal,
            "timestamp": now,
            "brainRole": brain_role,
            "bridgeConnected": bridge_connected,
            "rightBrainOnline": right_brain_online,
            "remoteAgents": remote_active,
            "rightBrainTasks": right_brain_status.get("active_tasks", []),
            "voice": status.get("voice", {}),
        }
    except Exception as e:
        logger.error(f"Error building state: {e}")
        return {
            "leftActive": True,
            "rightActive": False,
            "bridgeActive": False,
            "activeAgents": [],
            "agentCount": 0,
            "taskCount": 0,
            "completedCount": 0,
            "queuedCount": 0,
            "maxConcurrent": 5,
            "uptime": uptime_seconds,
            "taskFeed": [{"time": datetime.now().strftime("%H:%M"), "message": f"Error: {e}"}],
            "brainRole": "unified",
            "bridgeConnected": False,
            "rightBrainOnline": False,
            "remoteAgents": 0,
            "rightBrainTasks": [],
        }


# ── App Setup ────────────────────────────────────────────

def create_app(leon_core=None) -> web.Application:
    """Create the dashboard web application."""
    app = web.Application()
    app["leon_core"] = leon_core

    # Generate session token for WebSocket authentication
    token = secrets.token_hex(16)
    app["session_token"] = token
    print(f"\n  Dashboard session token: {token}\n", flush=True)
    logger.info(f"Dashboard session token: {token}")

    # Persistent API token (survives restarts via vault)
    api_token = None
    if leon_core and hasattr(leon_core, "vault") and leon_core.vault and leon_core.vault._unlocked:
        api_token = leon_core.vault.retrieve("LEON_API_TOKEN")
        if not api_token:
            api_token = secrets.token_hex(24)
            leon_core.vault.store("LEON_API_TOKEN", api_token)
            logger.info("Generated new persistent API token and stored in vault")
    if not api_token:
        api_token = os.environ.get("LEON_API_TOKEN", secrets.token_hex(24))
    app["api_token"] = api_token
    print(f"  API token (for WhatsApp bridge): {api_token}\n", flush=True)
    logger.info(f"API token: {api_token}")

    # Routes
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/api/health", api_health)
    app.router.add_post("/api/message", api_message)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_static("/static", STATIC_DIR)

    # Background broadcaster
    async def start_broadcaster(app):
        app["broadcaster"] = asyncio.create_task(broadcast_state(app))

    async def stop_broadcaster(app):
        app["broadcaster"].cancel()

    app.on_startup.append(start_broadcaster)
    app.on_cleanup.append(stop_broadcaster)

    return app


def run_standalone(host="127.0.0.1", port=3000):
    """Run dashboard in standalone/demo mode (no Leon core)."""
    logger.info(f"Starting Leon Brain Dashboard at http://localhost:{port}")
    app = create_app(leon_core=None)
    web.run_app(app, host=host, port=port, print=lambda _: None)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_standalone()
