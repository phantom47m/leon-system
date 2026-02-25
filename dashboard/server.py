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
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

logger = logging.getLogger("leon.dashboard")

# Rate limiting for /api/message
_rate_limit_window = 60  # seconds
_rate_limit_max = 20  # max requests per window per IP
_rate_limit_buckets: dict[str, list[float]] = defaultdict(list)
_rate_limit_request_count = 0  # counter for periodic stale-IP cleanup

DASHBOARD_DIR = Path(__file__).parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"
BASE_DIR = DASHBOARD_DIR.parent          # leon-system/
CONFIG_DIR = BASE_DIR / "config"         # leon-system/config/
USER_CONFIG = CONFIG_DIR / "user_config.yaml"

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
    import yaml as _yaml
    # Redirect to setup wizard if user_config.yaml is missing or incomplete
    user_cfg_path = USER_CONFIG
    if not user_cfg_path.exists():
        raise web.HTTPFound("/setup")
    try:
        _ucfg = _yaml.safe_load(user_cfg_path.read_text()) or {}
        if not _ucfg.get("setup_complete"):
            raise web.HTTPFound("/setup")
    except Exception:
        raise web.HTTPFound("/setup")

    index_path = TEMPLATES_DIR / "index.html"
    if not index_path.exists():
        logger.error(f"Dashboard template not found: {index_path}")
        return web.Response(text="Dashboard template not found", status=500)
    try:
        html = index_path.read_text()
    except OSError as e:
        logger.error(f"Failed to read dashboard template: {e}")
        return web.Response(text="Failed to load dashboard", status=500)
    # Inject file-mtime-based cache busters so the browser always gets fresh assets
    # after any on-disk change — no manual version bumping required.
    import re
    try:
        js_v  = int((STATIC_DIR / "js"  / "brain.js").stat().st_mtime)
        css_v = int((STATIC_DIR / "css" / "dashboard.css").stat().st_mtime)
        html = re.sub(r'brain\.js\?v=\w+',      f'brain.js?v={js_v}',      html)
        html = re.sub(r'dashboard\.css\?v=\w+', f'dashboard.css?v={css_v}', html)
    except OSError:
        pass  # Static files missing — serve HTML without cache-busting
    # Patch AI name into dashboard branding (every hardcoded LEON reference)
    leon = request.app.get("leon_core")
    ai_name = getattr(leon, 'ai_name', 'LEON').upper() if leon else 'LEON'
    html = re.sub(r'(<span class="logo-text">)LEON(</span>)', rf'\g<1>{ai_name}\2', html)
    html = re.sub(r'<title>LEON\b', f'<title>{ai_name}', html)
    html = html.replace('LEON Neural Interface — Initialized. Ready.',
                        f'{ai_name} Neural Interface — Initialized. Ready.')
    # SVG hub circle text
    html = re.sub(r'>LEON</text>', f'>{ai_name}</text>', html)
    # Settings panel title
    html = html.replace('⚙ LEON SETTINGS', f'⚙ {ai_name} SETTINGS')
    # Loading screen logo
    html = html.replace('◆ LEON</div>', f'◆ {ai_name}</div>')
    return web.Response(
        text=html,
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_elevenlabs_voices(request):
    """GET /api/elevenlabs-voices?key=sk_... — fetch user's available ElevenLabs voices."""
    key = request.rel_url.query.get("key", "").strip()
    if not key:
        return web.json_response({"error": "No API key provided"}, status=400)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": key},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 401:
                    return web.json_response({"error": "Invalid API key — check it and try again"}, status=401)
                if resp.status != 200:
                    return web.json_response({"error": f"ElevenLabs returned {resp.status}"}, status=502)
                data = await resp.json()
                voices = sorted(
                    [
                        {
                            "voice_id": v["voice_id"],
                            "name": v["name"],
                            "gender": (v.get("labels") or {}).get("gender", ""),
                            "accent": (v.get("labels") or {}).get("accent", ""),
                        }
                        for v in data.get("voices", [])
                    ],
                    key=lambda x: x["name"].lower(),
                )
                return web.json_response({"voices": voices})
    except asyncio.TimeoutError:
        return web.json_response({"error": "Request timed out — check your internet connection"}, status=504)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def setup_page(request):
    """Serve the first-run setup wizard."""
    setup_path = TEMPLATES_DIR / "setup.html"
    if not setup_path.exists():
        return web.Response(text="Setup template not found", status=500)
    return web.Response(
        text=setup_path.read_text(),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def api_setup(request):
    """POST /api/setup — validate setup form and write config/user_config.yaml."""
    import yaml as _yaml
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    try:
        ai_name = (body.get("ai_name") or "").strip()
        owner_name = (body.get("owner_name") or "").strip()
        if not ai_name or not owner_name:
            return web.json_response({"error": "AI name and your name are required"}, status=400)

        claude_auth = body.get("claude_auth", "max")
        claude_api_key = (body.get("claude_api_key") or "").strip()
        elevenlabs_api_key = (body.get("elevenlabs_api_key") or "").strip()
        elevenlabs_voice_id = (body.get("elevenlabs_voice_id") or "").strip()
        groq_api_key = (body.get("groq_api_key") or "").strip()
        discord_bot_token = (body.get("discord_bot_token") or "").strip()
        discord_allowed_users = (body.get("discord_allowed_users") or "").strip()

        # Validate: if "max" selected, check claude CLI is actually installed
        if claude_auth == "max":
            import shutil as _shutil
            if not _shutil.which("claude"):
                return web.json_response({
                    "error": (
                        "claude CLI not found. Install it from https://claude.ai/download "
                        "and run 'claude' once to log in. Or switch to API key mode."
                    )
                }, status=400)

        # Validate: if "api" selected, key must be provided
        if claude_auth == "api" and not claude_api_key:
            return web.json_response({"error": "Please enter your Anthropic API key."}, status=400)

        # Validate: must have at least one AI provider
        if claude_auth not in ("max", "api") and not groq_api_key:
            return web.json_response({
                "error": "You need at least one AI provider — Claude Max, an API key, or a Groq key."
            }, status=400)

        cfg = {
            "ai_name": ai_name,
            "owner_name": owner_name,
            "claude_auth": claude_auth,
            "claude_api_key": claude_api_key,
            "elevenlabs_api_key": elevenlabs_api_key,
            "elevenlabs_voice_id": elevenlabs_voice_id,
            "groq_api_key": groq_api_key,
            "discord_bot_token": discord_bot_token,
            "discord_allowed_users": discord_allowed_users,
            "setup_complete": True,
        }

        USER_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        USER_CONFIG.write_text(_yaml.dump(cfg, default_flow_style=False, allow_unicode=True))
        logger.info(f"Setup complete — AI: {ai_name}, Owner: {owner_name}")

        # Set env vars for current session — direct assignment so these win over .env
        if groq_api_key:
            os.environ["GROQ_API_KEY"] = groq_api_key
        if elevenlabs_api_key:
            os.environ["ELEVENLABS_API_KEY"] = elevenlabs_api_key
        if elevenlabs_voice_id:
            os.environ["LEON_VOICE_ID"] = elevenlabs_voice_id
        if claude_api_key and claude_auth == "api":
            os.environ["ANTHROPIC_API_KEY"] = claude_api_key

        # Update leon_core in current session (no restart needed)
        leon = request.app.get("leon_core")
        if leon:
            leon.ai_name = ai_name
            leon.owner_name = owner_name
            vs = getattr(getattr(leon, 'hotkey_listener', None), 'voice_system', None)
            if vs:
                if elevenlabs_api_key:
                    vs.elevenlabs_api_key = elevenlabs_api_key
                if elevenlabs_voice_id:
                    vs.voice_id = elevenlabs_voice_id
                if hasattr(vs, 'reset_elevenlabs'):
                    vs.reset_elevenlabs()

    except Exception as e:
        logger.exception("api_setup failed")
        return web.json_response({"error": f"Setup failed: {e}"}, status=500)

    return web.json_response({"ok": True})


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


_gpu_cache: dict = {}
_gpu_cache_time: float = 0
_GPU_CACHE_TTL = 10  # seconds


def _get_gpu_info() -> dict:
    """Get GPU info from nvidia-smi with 10s caching."""
    global _gpu_cache, _gpu_cache_time
    import shutil as _shutil

    now = time.monotonic()
    if now - _gpu_cache_time < _GPU_CACHE_TTL and _gpu_cache:
        return _gpu_cache

    gpu = {}
    if _shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = [p.strip() for p in result.stdout.strip().split(",")]
                if len(parts) >= 5:
                    gpu = {
                        "name": parts[0],
                        "usage": f"{parts[1]}%",
                        "vram_used": f"{parts[2]} MB",
                        "vram_total": f"{parts[3]} MB",
                        "temp": f"{parts[4]}°C",
                        "vram_pct": f"{100 * int(parts[2]) / max(int(parts[3]), 1):.0f}%",
                    }
        except Exception:
            pass

    _gpu_cache = gpu
    _gpu_cache_time = now
    return gpu


async def api_health(request):
    """
    GET /api/health — Detailed system health for monitoring/widgets.
    No auth required — read-only system stats.
    """
    import shutil

    leon = request.app.get("leon_core")
    uptime = int(time.monotonic() - _start_time)

    # System stats — use load average for a responsive CPU metric
    # (single /proc/stat read gives cumulative-since-boot, not current usage)
    cpu_line = ""
    try:
        with open("/proc/loadavg") as f:
            load1 = float(f.read().split()[0])
        import os as _os
        ncpu = _os.cpu_count() or 1
        cpu_pct = min(100.0, 100.0 * load1 / ncpu)
        cpu_line = f"{cpu_pct:.1f}%"
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

    # GPU — cache nvidia-smi results for 10s to avoid frequent subprocess calls
    gpu = _get_gpu_info()

    # Network (quick)
    net = {}
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if ":" in line and not line.strip().startswith("lo:"):
                    parts = line.split()
                    iface = parts[0].rstrip(":")
                    rx_bytes = int(parts[1])
                    tx_bytes = int(parts[9])
                    net[iface] = {
                        "rx_gb": round(rx_bytes / (1024**3), 2),
                        "tx_gb": round(tx_bytes / (1024**3), 2),
                    }
                    break  # Just first non-lo interface
    except Exception:
        pass

    # Process count
    proc_count = 0
    try:
        import os as _os
        proc_count = len([d for d in _os.listdir("/proc") if d.isdigit()])
    except Exception:
        pass

    # Load average
    load_avg = ""
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            load_avg = f"{parts[0]} / {parts[1]} / {parts[2]}"
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
        "gpu": gpu,
        "network": net,
        "processes": proc_count,
        "load_avg": load_avg,
        "leon": leon_stats,
        "timestamp": datetime.now().isoformat(),
    })


async def api_openclaw_url(request):
    """
    GET /api/openclaw-url — Start OpenClaw gateway if needed; return authed dashboard URL.
    Returns JSON: {"url": "http://127.0.0.1:18789/#token=..."} or {"error": "..."}
    """
    import socket as _socket
    from pathlib import Path as _Path
    gw = str(_Path.home() / ".openclaw" / "bin" / "openclaw")
    if not os.path.exists(gw):
        return web.json_response({"error": "OpenClaw not installed"}, status=404)

    # Check if gateway is already listening on port 18789
    running = False
    try:
        with _socket.create_connection(("127.0.0.1", 18789), timeout=0.5):
            running = True
    except Exception:
        pass

    if not running:
        subprocess.Popen(
            [gw, "gateway", "--port", "18789"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Wait up to 4 s for it to bind
        for _ in range(8):
            await asyncio.sleep(0.5)
            try:
                with _socket.create_connection(("127.0.0.1", 18789), timeout=0.5):
                    running = True
                    break
            except Exception:
                pass

    if not running:
        return web.json_response({"error": "Gateway failed to start"}, status=503)

    # Get the dashboard URL with embedded auth token
    try:
        result = subprocess.run([gw, "dashboard"], capture_output=True, text=True, timeout=5)
        url = ""
        for line in result.stdout.splitlines():
            if "Dashboard URL:" in line:
                url = line.split("Dashboard URL:", 1)[1].strip()
                break
        if not url:
            url = "http://127.0.0.1:18789"
        return web.json_response({"url": url})
    except Exception as e:
        return web.json_response({"url": "http://127.0.0.1:18789", "error": str(e)})


async def api_message(request):
    """
    POST /api/message — HTTP API for external integrations (WhatsApp bridge, etc.)

    Expects JSON: {"message": "user text"}
    Requires: Authorization: Bearer <token>
    Returns JSON: {"response": "leon's reply", "timestamp": "HH:MM"}
    """
    # ── Rate limiting (localhost is exempt) ──
    client_ip = request.remote or "unknown"
    if client_ip not in ("127.0.0.1", "::1"):
        now_ts = time.monotonic()
        bucket = _rate_limit_buckets[client_ip]
        bucket[:] = [t for t in bucket if now_ts - t < _rate_limit_window]
        if len(bucket) >= _rate_limit_max:
            return web.json_response(
                {"error": f"Rate limited — max {_rate_limit_max} requests per {_rate_limit_window}s"},
                status=429,
            )
        bucket.append(now_ts)
        # Purge stale IP entries periodically to prevent unbounded memory growth.
        # Runs every 50 requests OR when >200 IPs accumulate, whichever comes first.
        global _rate_limit_request_count
        _rate_limit_request_count += 1
        if _rate_limit_request_count >= 50 or len(_rate_limit_buckets) > 200:
            _rate_limit_request_count = 0
            stale_ips = [
                ip for ip, b in _rate_limit_buckets.items()
                if not b or b[-1] < now_ts - _rate_limit_window
            ]
            for ip in stale_ips:
                del _rate_limit_buckets[ip]

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

    # ── Speak the response via TTS ──
    audio_base64 = None
    audio_mime = None
    try:
        vs = leon.hotkey_listener.voice_system if (leon and leon.hotkey_listener) else None
        response_mode = request.app.get("response_mode", "both")  # voice, text, both
        should_speak = response_mode in ("voice", "both")
        if vs and should_speak and response and not response.startswith("["):
            import asyncio as _asyncio
            if source == "whatsapp":
                # For WhatsApp: generate audio to send as voice note, also play locally
                audio_bytes = await vs.generate_audio(response)
                if audio_bytes:
                    import base64 as _base64
                    audio_base64 = _base64.b64encode(audio_bytes).decode("utf-8")
                    audio_mime = "audio/mpeg"
                # Still play locally on the PC
                _asyncio.create_task(vs.speak(response))
            else:
                _asyncio.create_task(vs.speak(response))
    except Exception as e:
        logger.debug(f"TTS speak error: {e}")

    reply: dict = {
        "response": response,
        "timestamp": datetime.now().strftime("%H:%M"),
    }
    if audio_base64:
        reply["audio_base64"] = audio_base64
        reply["audio_mime"] = audio_mime

    return web.json_response(reply)


async def _broadcast_ws(app, data: dict):
    """Push a message to all authenticated WebSocket clients."""
    dead = set()
    for ws in set(ws_authenticated):  # snapshot to avoid RuntimeError during iteration
        try:
            await ws.send_json(data)
        except Exception:
            dead.add(ws)
    ws_authenticated.difference_update(dead)


# Global app reference so voice thread can broadcast without passing app around
_app_ref = None

async def broadcast_vad_event(event: str, text: str):
    """Called from voice thread to push live transcription to dashboard."""
    if _app_ref is None:
        return
    await _broadcast_ws(_app_ref, {
        "type": "vad_event",
        "event": event,   # "recording" | "transcription"
        "text": text,
    })


async def websocket_handler(request):
    """WebSocket endpoint for real-time brain state updates."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    session_token = request.app.get("session_token", "")

    # ── Localhost = auto-trusted, no token needed ──
    # If the connection is from 127.0.0.1 or ::1 (your own machine), skip auth entirely.
    # Remote connections (someone else's IP) still require a valid token.
    client_ip = request.remote or ""
    is_local = client_ip in ("127.0.0.1", "::1", "localhost")

    ws_clients.add(ws)

    authenticated = False
    if is_local:
        # Auto-approve — you're on your own machine
        authenticated = True
        ws_authenticated.add(ws)
        await ws.send_json({"type": "auth_result", "success": True})
        logger.info(f"Dashboard client auto-authenticated (localhost, {len(ws_authenticated)} total)")
    else:
        logger.info(f"Dashboard client connected from {client_ip}, awaiting auth")
        try:
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
                    logger.warning(f"Dashboard auth failed from {client_ip}")
                    await ws.close()
                    return ws
            else:
                await ws.close()
                return ws
        except asyncio.TimeoutError:
            logger.warning(f"Dashboard client {client_ip} did not authenticate within timeout")
            await ws.send_json({"type": "auth_result", "success": False,
                                "message": "Auth timeout"})
            await ws.close()
            return ws
    try:
        pass  # auth block done, continue to message loop below
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
                # Guard against oversized messages
                if len(msg.data) > 10_000:
                    logger.warning(f"WebSocket message too large ({len(msg.data)} bytes), dropping")
                    await ws.send_json({"type": "error", "message": "Message too large (max 10KB)"})
                    continue
                try:
                    data = json.loads(msg.data)
                except (json.JSONDecodeError, ValueError):
                    logger.warning("Received malformed JSON on WebSocket")
                    continue
                if not isinstance(data, dict):
                    continue
                # Handle commands from dashboard
                if data.get("command") == "ping":
                    await ws.send_json({"type": "pong"})
                elif data.get("command") == "status":
                    if leon:
                        await ws.send_json(_build_state(leon))
                elif data.get("command") == "input":
                    user_msg = data.get("message", "")
                    if not isinstance(user_msg, str):
                        await ws.send_json({
                            "type": "input_response",
                            "message": "Invalid message format",
                            "timestamp": datetime.now().strftime("%H:%M"),
                        })
                        continue
                    user_msg = user_msg.strip()
                    if len(user_msg) > 4000:
                        await ws.send_json({
                            "type": "input_response",
                            "message": "Message too long (max 4000 characters)",
                            "timestamp": datetime.now().strftime("%H:%M"),
                        })
                        continue

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
                elif data.get("command") == "voice_wake":
                    vs = leon.hotkey_listener.voice_system if (leon and leon.hotkey_listener) else None
                    if vs and vs.is_listening:
                        vs.force_wake()
                        await ws.send_json({"type": "voice_wake_result", "success": True})
                    else:
                        await ws.send_json({"type": "voice_wake_result", "success": False,
                                            "message": "Voice system not active"})
                elif data.get("command") == "voice_mute":
                    vs = leon.hotkey_listener.voice_system if (leon and leon.hotkey_listener) else None
                    if vs:
                        vs.mute()
                        await ws.send_json({"type": "voice_mute_result", "success": True, "muted": True})
                elif data.get("command") == "voice_unmute":
                    vs = leon.hotkey_listener.voice_system if (leon and leon.hotkey_listener) else None
                    if vs:
                        vs.unmute()
                        await ws.send_json({"type": "voice_mute_result", "success": True, "muted": False})
                elif data.get("command") == "set_response_mode":
                    mode = data.get("mode", "both")
                    if mode in ("voice", "text", "both"):
                        request.app["response_mode"] = mode
                        await ws.send_json({"type": "settings_updated", "response_mode": mode})
                elif data.get("command") == "set_voice_volume":
                    pct = int(data.get("volume", 100))
                    vs = leon.hotkey_listener.voice_system if (leon and leon.hotkey_listener) else None
                    if vs:
                        vs.set_voice_volume(pct)
                        request.app["voice_volume"] = pct
                        await ws.send_json({"type": "settings_updated", "voice_volume": pct})
                elif data.get("command") == "get_settings":
                    vs = leon.hotkey_listener.voice_system if (leon and leon.hotkey_listener) else None
                    await ws.send_json({
                        "type": "settings",
                        "response_mode": request.app.get("response_mode", "both"),
                        "voice_volume": request.app.get("voice_volume", 100),
                        "muted": vs.is_muted if vs else False,
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
    leon = app.get("leon_core")
    while True:
        if ws_authenticated and leon:
            state = _build_state(leon)
            dead = set()
            for ws in set(ws_authenticated):  # snapshot to avoid RuntimeError during iteration
                try:
                    await ws.send_json(state)
                except Exception:
                    dead.add(ws)
            ws_authenticated.difference_update(dead)

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
                return (
                    "Usage:\n"
                    "- `/setkey <anthropic_key>` — Anthropic paid API\n"
                    "- `/setkey groq <key>` — Groq free tier (get key at console.groq.com)\n\n"
                    "**Free option:** Sign up at https://console.groq.com → API Keys → Create Key"
                )

            # Check for provider prefix: /setkey groq <key> | /setkey elevenlabs <key>
            parts = arg.split(None, 1)
            prefix = parts[0].lower()
            if prefix == "groq" and len(parts) == 2:
                vault_key = "GROQ_API_KEY"
                env_key = "GROQ_API_KEY"
                provider = "groq"
                display = "Groq"
                key_value = parts[1].strip()
            elif prefix in ("elevenlabs", "11labs", "el") and len(parts) == 2:
                vault_key = "ELEVENLABS_API_KEY"
                env_key = "ELEVENLABS_API_KEY"
                provider = "elevenlabs"
                display = "ElevenLabs"
                key_value = parts[1].strip()
            else:
                vault_key = "ANTHROPIC_API_KEY"
                env_key = "ANTHROPIC_API_KEY"
                provider = "anthropic"
                display = "Anthropic"
                key_value = arg.strip()

            # Store in vault and activate
            if leon.vault and leon.vault._unlocked:
                leon.vault.store(vault_key, key_value)
            os.environ[env_key] = key_value
            if provider == "elevenlabs":
                # Update voice system directly
                if leon.voice and hasattr(leon.voice, 'elevenlabs_api_key'):
                    leon.voice.elevenlabs_api_key = key_value
                    leon.voice.reset_elevenlabs()
            elif hasattr(leon, 'api') and hasattr(leon.api, 'set_api_key'):
                leon.api.set_api_key(key_value, provider=provider)
            if leon.audit_log:
                leon.audit_log.log("set_api_key", f"{display} key updated via dashboard", "info")
            persisted = " and saved to vault" if (leon.vault and leon.vault._unlocked) else " (vault locked — not persisted)"
            extra = " Voice will now use ElevenLabs TTS." if provider == "elevenlabs" else f" Leon is now using **{display}** for AI responses."
            return f"{display} API key activated{persisted}.{extra}"

        elif cmd == "/provider":
            if hasattr(leon, 'api') and hasattr(leon.api, 'get_provider_info'):
                info = leon.api.get_provider_info()
                name = info.get("name", "Unknown")
                model = info.get("model", "—")
                cost = info.get("cost", "—")
                auth = getattr(leon.api, '_auth_method', 'none')
                lines = [
                    f"**Active AI Provider:** {name}",
                    f"**Model:** {model}",
                    f"**Cost:** {cost}",
                    "",
                    "**Available options:**",
                    f"- Groq free: `/setkey groq <key>` (console.groq.com)",
                    f"- Anthropic paid: `/setkey <key>`",
                    f"- Ollama local: install ollama + run `ollama serve`",
                ]
                if auth == "none":
                    lines.insert(0, "⚠️ No AI provider configured — Leon can't respond to natural language.\n")
                return "\n".join(lines)
            return "API client not initialized."

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
            if leon and leon.hotkey_listener and leon.hotkey_listener.voice_system:
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

        elif cmd == "/openclaw":
            import subprocess, shutil
            from pathlib import Path as _Path
            gw = str(_Path.home() / ".openclaw" / "bin" / "openclaw")
            if not shutil.which(gw) and not os.path.exists(gw):
                return "OpenClaw not installed."
            # Check if already running
            check = subprocess.run(["pgrep", "-f", "openclaw-gatewa"], capture_output=True)
            if check.returncode != 0:
                subprocess.Popen(
                    [gw, "gateway", "--port", "18789"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            # Get the authed URL
            try:
                r = subprocess.run([gw, "dashboard"], capture_output=True, text=True, timeout=5)
                for line in r.stdout.splitlines():
                    if "Dashboard URL:" in line:
                        url = line.split("Dashboard URL:", 1)[1].strip()
                        return f"OpenClaw ready — {url}"
            except Exception:
                pass
            return "OpenClaw gateway started — opening at http://127.0.0.1:18789"

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
                "- `/setkey groq <key>` — set Groq free API key (console.groq.com)\n"
                "- `/setkey <key>` — set Anthropic API key\n"
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

        # Build active tasks from agent_manager.active_agents — the real source of truth.
        # task_queue.active_tasks can get out of sync with what's actually running.
        active_tasks = []
        if hasattr(leon, 'agent_manager') and leon.agent_manager:
            for agent_id, info in leon.agent_manager.active_agents.items():
                # Cross-reference with task_queue for description/project
                tq_entry = leon.task_queue.active_tasks.get(agent_id, {})
                night_task = next(
                    (t for t in leon.night_mode._backlog if t.get("agent_id") == agent_id),
                    {}
                )
                desc = (tq_entry.get("description")
                        or night_task.get("description")
                        or info.get("description", "agent"))
                proj = (tq_entry.get("project")
                        or night_task.get("project")
                        or info.get("project_name", ""))
                active_tasks.append({
                    "id": agent_id,
                    "description": desc,
                    "project_name": proj,
                    "startedAt": info.get("started_at", ""),
                })
        active_count = len(active_tasks)

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
            "updateAvailable": getattr(leon, '_update_available', False),
            "updateVersion": getattr(leon.update_checker, 'latest_version', '') if getattr(leon, 'update_checker', None) else '',
            "updateUrl": getattr(leon.update_checker, 'release_url', '') if getattr(leon, 'update_checker', None) else '',
            "aiProvider": status.get("ai_provider", "none"),
            "aiName": status.get("ai_name", "AI"),
            "openclawAvailable": status.get("openclaw_available", False),
            "claudeCliAvailable": status.get("claude_cli_available", False),
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


# ── Security Middleware ──────────────────────────────────

@web.middleware
async def security_headers_middleware(request, handler):
    """Add security headers to all responses."""
    response = await handler(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "connect-src 'self' ws: wss:; "
        "img-src 'self' data:; "
    )
    return response


@web.middleware
async def error_handling_middleware(request, handler):
    """Catch unhandled exceptions and return clean JSON errors."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise  # Let aiohttp handle HTTP errors normally
    except Exception as e:
        logger.error(f"Unhandled error on {request.method} {request.path}: {e}", exc_info=True)
        return web.json_response(
            {"error": "Internal server error"},
            status=500,
        )


async def api_agent_log(request: web.Request) -> web.Response:
    """GET /api/agent-log/{agent_id} — last N lines of agent's stdout log."""
    import re as _re
    agent_id = request.match_info.get("agent_id", "")
    # Basic sanitization — agent_id should only be alphanumeric + underscore
    if not _re.match(r'^agent_[a-f0-9]{8}$', agent_id):
        return web.json_response({"error": "invalid"}, status=400)
    log_path = Path("data/agent_outputs") / f"{agent_id}.log"
    if not log_path.exists():
        return web.json_response({"lines": [], "exists": False})
    try:
        # Read last 80 lines efficiently
        content = log_path.read_text(errors="replace")
        lines = content.splitlines()[-80:]
        return web.json_response({"lines": lines, "exists": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ── App Setup ────────────────────────────────────────────

def create_app(leon_core=None) -> web.Application:
    """Create the dashboard web application."""
    app = web.Application(middlewares=[security_headers_middleware, error_handling_middleware])
    app["leon_core"] = leon_core

    # Persistent session token (survives restarts via vault or env)
    token = None
    if leon_core and hasattr(leon_core, "vault") and leon_core.vault and leon_core.vault._unlocked:
        token = leon_core.vault.retrieve("LEON_SESSION_TOKEN")
        if not token:
            token = secrets.token_hex(16)
            leon_core.vault.store("LEON_SESSION_TOKEN", token)
    if not token:
        token = os.environ.get("LEON_SESSION_TOKEN", secrets.token_hex(16))
    app["session_token"] = token
    print(f"\n  Dashboard session token: ...{token[-6:]}\n", flush=True)
    logger.info(f"Dashboard session token: ...{token[-6:]}")

    # Persistent API token — always loaded from config/api_token.txt so it never changes across restarts
    _token_file = Path(__file__).parent.parent / "config" / "api_token.txt"
    api_token = None
    if _token_file.exists():
        api_token = _token_file.read_text().strip()
    if not api_token:
        api_token = secrets.token_hex(24)
        _token_file.write_text(api_token)
        logger.info("Generated new persistent API token and saved to config/api_token.txt")
    app["api_token"] = api_token
    os.environ["LEON_API_TOKEN"] = api_token  # ensure bridge subprocess picks up correct token
    app["response_mode"] = "both"   # voice, text, both
    app["voice_volume"] = 100       # 0-200
    print(f"  API token (for WhatsApp bridge): ...{api_token[-6:]}\n", flush=True)
    logger.info(f"API token: ...{api_token[-6:]}")

    # Routes
    app.router.add_get("/", index)
    app.router.add_get("/setup", setup_page)
    app.router.add_post("/api/setup", api_setup)
    app.router.add_get("/api/elevenlabs-voices", api_elevenlabs_voices)
    app.router.add_get("/health", health)
    app.router.add_get("/api/health", api_health)
    app.router.add_get("/api/openclaw-url", api_openclaw_url)
    app.router.add_post("/api/message", api_message)
    app.router.add_get("/api/agent-log/{agent_id}", api_agent_log)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_static("/static", STATIC_DIR)

    # Background broadcaster
    async def start_broadcaster(app):
        global _app_ref
        _app_ref = app
        app["broadcaster"] = asyncio.create_task(broadcast_state(app))

    async def stop_broadcaster(app):
        app["broadcaster"].cancel()

    async def cleanup_websockets(app):
        """Gracefully close all WebSocket connections on shutdown."""
        for ws in set(ws_authenticated):
            try:
                await ws.close(code=1001, message=b"Server shutting down")
            except Exception:
                pass
        ws_authenticated.clear()
        for ws in set(ws_clients):
            try:
                await ws.close(code=1001, message=b"Server shutting down")
            except Exception:
                pass
        ws_clients.clear()
        logger.info("All WebSocket connections closed")

    app.on_startup.append(start_broadcaster)
    app.on_cleanup.append(stop_broadcaster)
    app.on_cleanup.append(cleanup_websockets)

    return app


def run_standalone(host="127.0.0.1", port=3000):
    """Run dashboard in standalone/demo mode (no Leon core)."""
    logger.info(f"Starting Leon Brain Dashboard at http://localhost:{port}")
    app = create_app(leon_core=None)
    web.run_app(app, host=host, port=port, print=lambda _: None)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_standalone()
