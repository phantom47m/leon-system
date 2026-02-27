#!/usr/bin/env python3
"""
Leon - AI Orchestration System
Entry point: launches core system, voice, dashboard, and UI

Modes:
    --cli           Terminal only (type commands)
    --voice         Terminal + voice (Hey Leon wake word)
    --dashboard     Terminal + brain dashboard (localhost:3000)
    --full          Everything: voice + dashboard + GTK overlay
    --gui           GTK4 desktop overlay only
    --left-brain    Left Brain mode (main PC — awareness + dispatch)
    --right-brain   Right Brain mode (homelab PC — agent execution)
"""

import sys
import logging
import asyncio
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Ensure we're running from the project root
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── Load .env file (API keys, DISPLAY, etc.) ─────────────
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed — env vars must be set externally

# ── Logging ──────────────────────────────────────────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "leon_system.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("leon")

# ── Ensure data dirs ─────────────────────────────────────
(ROOT / "data" / "task_briefs").mkdir(parents=True, exist_ok=True)
(ROOT / "data" / "agent_outputs").mkdir(parents=True, exist_ok=True)


# ═════════════════════════════════════════════════════════
# SHARED HELPERS — daemon thread lifecycle
# ═════════════════════════════════════════════════════════

@dataclass
class _DaemonHandle:
    """Bundles a daemon thread with its event loop for graceful shutdown."""
    thread: threading.Thread
    loop: Optional[asyncio.AbstractEventLoop] = field(default=None)
    _loop_ready: threading.Event = field(default_factory=threading.Event)

    def wait_loop_ready(self, timeout: float = 10.0):
        """Block until the daemon thread has set its event loop."""
        self._loop_ready.wait(timeout)

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self._loop_ready.set()


def _stop_daemon(handle: Optional[_DaemonHandle], timeout: float = 5.0):
    """Gracefully stop a daemon thread's event loop, then join the thread.

    Uses call_soon_threadsafe to safely stop the loop from the main thread.
    Falls back to a timed join if the loop is already closed or None.
    """
    if handle is None:
        return
    if handle.loop is not None and handle.loop.is_running():
        try:
            handle.loop.call_soon_threadsafe(handle.loop.stop)
        except RuntimeError:
            pass  # loop already closed
    handle.thread.join(timeout=timeout)
    if handle.thread.is_alive():
        logger.warning(f"Daemon thread {handle.thread.name} did not stop within {timeout}s")


def _start_dashboard_thread(leon) -> _DaemonHandle:
    """Start the dashboard server in a background daemon thread.

    Shared by run_cli() and run_gui() to avoid code duplication.
    Returns a _DaemonHandle for graceful shutdown.
    """
    handle = _DaemonHandle(thread=threading.Thread(name="dashboard", daemon=True, target=lambda: None))

    def _run():
        from dashboard.server import create_app
        from aiohttp import web
        dash_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(dash_loop)
        handle.set_loop(dash_loop)
        app = create_app(leon_core=leon)
        runner = web.AppRunner(app)
        dash_loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 3000)
        dash_loop.run_until_complete(site.start())
        try:
            dash_loop.run_forever()
        finally:
            dash_loop.run_until_complete(runner.cleanup())
            dash_loop.close()

    handle.thread = threading.Thread(target=_run, name="dashboard", daemon=True)
    handle.thread.start()
    logger.info("🧠 Brain Dashboard: http://localhost:3000")
    return handle


def _start_voice_thread(leon) -> _DaemonHandle:
    """Start the voice system in a background daemon thread.

    Shared by run_cli() and run_gui() to avoid code duplication.
    Returns a _DaemonHandle for graceful shutdown.
    """
    handle = _DaemonHandle(thread=threading.Thread(name="voice", daemon=True, target=lambda: None))

    def _run():
        from core.voice import VoiceSystem
        from dashboard.server import broadcast_vad_event

        main_loop = leon.main_loop  # Set by leon.start()

        async def voice_command_handler(text: str) -> str:
            # Dispatch to Leon's main event loop for thread safety —
            # ensures fire-and-forget tasks (reminders, memory extraction)
            # run on the correct loop and aren't lost.
            if main_loop and not main_loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(
                    leon.process_user_input(text), main_loop
                )
                return await asyncio.wrap_future(future)
            # Fallback: run on voice thread's own loop (degraded mode)
            return await leon.process_user_input(text)

        async def vad_event_handler(event: str, text: str):
            await broadcast_vad_event(event, text)

        vloop = asyncio.new_event_loop()
        asyncio.set_event_loop(vloop)
        handle.set_loop(vloop)
        voice_cfg = leon.get_voice_config()
        voice = VoiceSystem(on_command=voice_command_handler, config=voice_cfg, name=getattr(leon, 'ai_name', 'leon'))
        voice.on_vad_event = vad_event_handler
        leon.set_voice_system(voice)
        try:
            vloop.run_until_complete(voice.start())
        finally:
            vloop.close()

    handle.thread = threading.Thread(target=_run, name="voice", daemon=True)
    handle.thread.start()
    ai_name = getattr(leon, 'ai_name', 'Leon')
    logger.info(f"🎤 Voice system active — say 'Hey {ai_name}'")
    return handle


# ═════════════════════════════════════════════════════════
# CLI MODE
# ═════════════════════════════════════════════════════════

def run_cli(enable_voice=False, enable_dashboard=False):
    """Terminal mode with optional voice and dashboard.

    The event loop runs continuously in the main thread so that
    fire-and-forget tasks (reminders, memory extraction, night mode
    dispatch, awareness loop) execute promptly.  The blocking input()
    call is moved to a background thread and dispatches commands to
    the main loop via run_coroutine_threadsafe().
    """
    from core.leon import Leon

    leon = Leon(str(ROOT / "config" / "settings.yaml"))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(leon.start())

    # ── Start daemon threads (keep handles for graceful shutdown) ──
    dash_handle: Optional[_DaemonHandle] = None
    voice_handle: Optional[_DaemonHandle] = None

    if enable_dashboard:
        dash_handle = _start_dashboard_thread(leon)

    if enable_voice:
        voice_handle = _start_voice_thread(leon)

    # ── Print banner ──
    print()
    print("╔════════════════════════════════════════════╗")
    print("║       🤖 Leon — AI Orchestrator v2.0       ║")
    if enable_voice:
        print("║       🎤 Voice: ACTIVE (Hey Leon)          ║")
    if enable_dashboard:
        print("║       🧠 Brain: http://localhost:3000       ║")
    print("║       Type your request. 'quit' to exit.   ║")
    print("╚════════════════════════════════════════════╝")
    print()

    # ── Input loop runs in a background thread ──
    # This keeps the main event loop free to process async tasks
    # (reminders, night mode dispatch, awareness, memory extraction)
    # between user inputs.

    def _input_loop():
        """Blocking input loop — runs in a daemon thread."""
        try:
            while True:
                try:
                    user_input = input("You > ").strip()
                except EOFError:
                    break

                if not user_input:
                    continue
                if user_input.lower() in ("quit", "exit", "q"):
                    break

                if user_input.lower() == "status":
                    # get_status() is a synchronous read-only method — safe from any thread
                    status = leon.get_status()
                    print(f"\n  Active agents: {status['active_agents']}")
                    tasks = status["tasks"]
                    print(f"  Active tasks:  {tasks['active']}")
                    print(f"  Queued tasks:  {tasks['queued']}")
                    print(f"  Completed:     {tasks['completed']}")
                    for t in tasks.get("active_tasks", []):
                        print(f"    • {t['description'][:60]} ({t['project']})")
                    v = status.get("vision", {})
                    if v.get("active"):
                        print(f"  Vision:        ACTIVE — {v.get('scene', 'N/A')}")
                        print(f"                 People: {v.get('people_count', 0)} | Objects: {len(v.get('objects', []))}")
                    else:
                        print(f"  Vision:        INACTIVE")
                    sec = status.get("security", {})
                    print(f"  Vault:         {'UNLOCKED' if sec.get('vault_unlocked') else 'LOCKED'}")
                    print(f"  Auth:          {'OK' if sec.get('authenticated') else 'REQUIRED'}")
                    print(f"  3D Printer:    {'CONNECTED' if status.get('printer') else 'NOT CONFIGURED'}")
                    print()
                    continue

                # Dispatch async command to the main event loop
                future = asyncio.run_coroutine_threadsafe(
                    leon.process_user_input(user_input), loop
                )
                try:
                    response = future.result(timeout=300)  # 5 min timeout
                    print(f"\nLeon > {response}\n")
                except Exception as e:
                    print(f"\nLeon > Error: {e}\n")

        except KeyboardInterrupt:
            pass
        finally:
            # Signal the main event loop to stop
            loop.call_soon_threadsafe(loop.stop)

    input_thread = threading.Thread(target=_input_loop, name="cli-input", daemon=True)
    input_thread.start()

    # ── Main event loop runs continuously in the main thread ──
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("\n")

    # Graceful shutdown — stop daemon threads first, then Leon core
    # (leon.stop() already calls memory.save(force=True) internally)
    _stop_daemon(voice_handle)
    _stop_daemon(dash_handle)
    loop.run_until_complete(leon.stop())
    loop.close()
    print("Leon stopped. See you next time!")


# ═════════════════════════════════════════════════════════
# GUI MODE
# ═════════════════════════════════════════════════════════

def run_gui():
    """GTK4 GUI mode with dashboard and voice."""
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk, Gio, Adw
    except (ImportError, ValueError) as e:
        logger.warning(f"GTK4 not available ({e}), falling back to CLI")
        run_cli(enable_voice=True, enable_dashboard=True)
        return

    from core.leon import Leon
    from ui.main_window import LeonWindow

    class LeonApp(Adw.Application):
        def __init__(self):
            super().__init__(
                application_id="com.leon.orchestrator",
                flags=Gio.ApplicationFlags.FLAGS_NONE,
            )
            self.leon_core = None
            self.win = None
            self._leon_loop: Optional[asyncio.AbstractEventLoop] = None
            self._dash_handle: Optional[_DaemonHandle] = None
            self._voice_handle: Optional[_DaemonHandle] = None

        def do_activate(self):
            if not self.win:
                self.leon_core = Leon(str(ROOT / "config" / "settings.yaml"))

                # Start Leon core in a daemon thread with its own event loop
                leon_ready = threading.Event()
                def start_leon():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    self._leon_loop = loop
                    loop.run_until_complete(self.leon_core.start())
                    leon_ready.set()
                    loop.run_forever()
                threading.Thread(target=start_leon, name="leon-core", daemon=True).start()
                leon_ready.wait(timeout=15)

                # Start dashboard and voice (shared helpers)
                self._dash_handle = _start_dashboard_thread(self.leon_core)
                self._voice_handle = _start_voice_thread(self.leon_core)

                self.win = LeonWindow(self, self.leon_core)
            self.win.present()

        def do_shutdown(self):
            """Clean up daemon threads and Leon core when GTK app exits."""
            _stop_daemon(self._voice_handle)
            _stop_daemon(self._dash_handle)
            if self._leon_loop and self.leon_core:
                import concurrent.futures
                future = asyncio.run_coroutine_threadsafe(
                    self.leon_core.stop(), self._leon_loop
                )
                try:
                    future.result(timeout=10)
                except (concurrent.futures.TimeoutError, Exception) as e:
                    logger.warning(f"Leon core shutdown error: {e}")
                self._leon_loop.call_soon_threadsafe(self._leon_loop.stop)
            super().do_shutdown()

    app = LeonApp()
    app.run(sys.argv)


# ═════════════════════════════════════════════════════════
# HEADLESS MODE (Left Brain daemon — no REPL)
# ═════════════════════════════════════════════════════════

def run_headless(enable_voice: bool = True):
    """Left Brain headless mode — bridge + dashboard, no terminal input."""
    import os
    os.environ["LEON_BRAIN_ROLE"] = "unified"

    from core.leon import Leon
    from dashboard.server import create_app
    from aiohttp import web

    leon = Leon(str(ROOT / "config" / "settings.yaml"))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(leon.start())

    print()
    print("╔════════════════════════════════════════════╗")
    print("║    Leon Left Brain — Headless Daemon       ║")
    print("║    Bridge: wss://127.0.0.1:9100/bridge      ║")
    print("║    Dashboard: http://localhost:3000         ║")
    print("║    Ctrl+C to stop                          ║")
    print("╚════════════════════════════════════════════╝")
    print()

    # Run dashboard in the main event loop (avoids set_wakeup_fd thread error)
    app = create_app(leon_core=leon)
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "127.0.0.1", 3000)
    loop.run_until_complete(site.start())
    logger.info("Dashboard running on http://localhost:3000")

    # Start voice system in background thread (skip when Discord handles voice)
    voice_handle: Optional[_DaemonHandle] = None
    if enable_voice:
        voice_handle = _start_voice_thread(leon)
        logger.info("Voice system starting in background...")
    else:
        logger.info("Voice system disabled (--no-voice) — Discord bridge handles voice")

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("\n")
    finally:
        _stop_daemon(voice_handle)
        loop.run_until_complete(runner.cleanup())
        loop.run_until_complete(leon.stop())
        loop.close()
        print("Left Brain stopped.")


# ═════════════════════════════════════════════════════════
# RIGHT BRAIN MODE
# ═════════════════════════════════════════════════════════

def run_right_brain():
    """Right Brain mode — headless worker that connects to Left Brain."""
    import os
    os.environ["LEON_BRAIN_ROLE"] = "right"

    from core.right_brain import RightBrain

    right = RightBrain(str(ROOT / "config" / "settings.yaml"))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    print()
    print("╔════════════════════════════════════════════╗")
    print("║    Leon Right Brain — Homelab Worker       ║")
    print("║    Connecting to Left Brain...             ║")
    print("╚════════════════════════════════════════════╝")
    print()

    try:
        loop.run_until_complete(right.start())
        # Keep running until interrupted
        loop.run_forever()
    except KeyboardInterrupt:
        print("\n")
    finally:
        loop.run_until_complete(right.stop())
        loop.close()
        print("Right Brain stopped.")


# ═════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Leon AI Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 main.py --cli                  # Terminal only
  python3 main.py --cli --voice          # Terminal + voice
  python3 main.py --cli --dashboard      # Terminal + brain viz
  python3 main.py --full                 # Everything
  python3 main.py --gui                  # GTK4 overlay + voice + brain
  python3 main.py --left-brain           # Left Brain (main PC)
  python3 main.py --right-brain          # Right Brain (homelab)
        """,
    )
    parser.add_argument("--cli", action="store_true", help="Terminal mode")
    parser.add_argument("--gui", action="store_true", help="GTK4 desktop overlay")
    parser.add_argument("--voice", action="store_true", help="Enable voice (Hey Leon)")
    parser.add_argument("--dashboard", action="store_true", help="Enable brain dashboard (localhost:3000)")
    parser.add_argument("--full", action="store_true", help="Everything: CLI + voice + dashboard")
    parser.add_argument("--left-brain", action="store_true", dest="left_brain", help="Left Brain mode (main PC)")
    parser.add_argument("--right-brain", action="store_true", dest="right_brain", help="Right Brain mode (homelab)")
    parser.add_argument("--no-voice", action="store_true", dest="no_voice", help="Disable local mic (use when Discord bridge handles voice)")
    args = parser.parse_args()

    if args.right_brain:
        run_right_brain()
    elif args.left_brain:
        run_headless(enable_voice=not args.no_voice)
    elif args.full:
        run_cli(enable_voice=True, enable_dashboard=True)
    elif args.gui:
        run_gui()
    elif args.cli:
        run_cli(enable_voice=args.voice, enable_dashboard=args.dashboard)
    else:
        # Default: CLI + dashboard
        run_cli(enable_voice=False, enable_dashboard=True)


if __name__ == "__main__":
    main()
