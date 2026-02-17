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
import time
from datetime import datetime
from pathlib import Path

from aiohttp import web

logger = logging.getLogger("leon.dashboard")

DASHBOARD_DIR = Path(__file__).parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"

# Connected WebSocket clients
ws_clients: set[web.WebSocketResponse] = set()

# Startup time for uptime tracking
_start_time = time.monotonic()


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
        "clients": len(ws_clients),
        "leon_core": leon is not None,
    })


async def websocket_handler(request):
    """WebSocket endpoint for real-time brain state updates."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    ws_clients.add(ws)
    logger.info(f"Dashboard client connected ({len(ws_clients)} total)")

    # Send initial state
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
                    user_msg = data.get("message", "")
                    if leon and hasattr(leon, "process_user_input"):
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
        ws_clients.discard(ws)
        logger.info(f"Dashboard client disconnected ({len(ws_clients)} total)")

    return ws


async def broadcast_state(app):
    """Background task that broadcasts brain state to all connected clients."""
    leon = app.get("leon_core")
    while True:
        if ws_clients and leon:
            state = _build_state(leon)
            dead = set()
            for ws in ws_clients:
                try:
                    await ws.send_json(state)
                except Exception:
                    dead.add(ws)
            ws_clients -= dead

        await asyncio.sleep(2)  # Update every 2 seconds


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

        return {
            "leftActive": left_active,
            "rightActive": right_active,
            "bridgeActive": bridge_active,
            "activeAgents": active_tasks,
            "agentCount": active_count,
            "taskCount": queued_count + active_count,
            "completedCount": completed_count,
            "queuedCount": queued_count,
            "maxConcurrent": max_concurrent,
            "uptime": uptime_seconds,
            "taskFeed": feed,
            "signal": signal,
            "timestamp": now,
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
        }


# ── App Setup ────────────────────────────────────────────

def create_app(leon_core=None) -> web.Application:
    """Create the dashboard web application."""
    app = web.Application()
    app["leon_core"] = leon_core

    # Routes
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
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


def run_standalone(host="0.0.0.0", port=3000):
    """Run dashboard in standalone/demo mode (no Leon core)."""
    logger.info(f"Starting Leon Brain Dashboard at http://localhost:{port}")
    app = create_app(leon_core=None)
    web.run_app(app, host=host, port=port, print=lambda _: None)
    print(f"\n🧠 Leon Brain Dashboard running at http://localhost:{port}\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_standalone()
