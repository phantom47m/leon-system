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
from pathlib import Path

# Ensure we're running from the project root
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

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
# CLI MODE
# ═════════════════════════════════════════════════════════

def run_cli(enable_voice=False, enable_dashboard=False):
    """Terminal mode with optional voice and dashboard."""
    from core.leon import Leon

    leon = Leon(str(ROOT / "config" / "settings.yaml"))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(leon.start())

    # ── Start dashboard server in background ──
    if enable_dashboard:
        def start_dashboard():
            from dashboard.server import create_app
            from aiohttp import web
            app = create_app(leon_core=leon)
            web.run_app(app, host="0.0.0.0", port=3000, print=lambda _: None)

        dash_thread = threading.Thread(target=start_dashboard, daemon=True)
        dash_thread.start()
        logger.info("🧠 Brain Dashboard: http://localhost:3000")

    # ── Start voice system in background ──
    if enable_voice:
        async def voice_command_handler(text: str) -> str:
            return await leon.process_user_input(text)

        def start_voice():
            from core.voice import VoiceSystem
            vloop = asyncio.new_event_loop()
            asyncio.set_event_loop(vloop)
            voice = VoiceSystem(on_command=voice_command_handler)
            vloop.run_until_complete(voice.start())

        voice_thread = threading.Thread(target=start_voice, daemon=True)
        voice_thread.start()
        logger.info("🎤 Voice system active — say 'Hey Leon'")

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

    # ── Main input loop ──
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
                status = leon.get_status()
                print(f"\n  Active agents: {status['active_agents']}")
                tasks = status["tasks"]
                print(f"  Active tasks:  {tasks['active']}")
                print(f"  Queued tasks:  {tasks['queued']}")
                print(f"  Completed:     {tasks['completed']}")
                for t in tasks.get("active_tasks", []):
                    print(f"    • {t['description'][:60]} ({t['project']})")
                # Vision
                v = status.get("vision", {})
                if v.get("active"):
                    print(f"  Vision:        ACTIVE — {v.get('scene', 'N/A')}")
                    print(f"                 People: {v.get('people_count', 0)} | Objects: {len(v.get('objects', []))}")
                else:
                    print(f"  Vision:        INACTIVE")
                # Security
                sec = status.get("security", {})
                print(f"  Vault:         {'UNLOCKED' if sec.get('vault_unlocked') else 'LOCKED'}")
                print(f"  Auth:          {'OK' if sec.get('authenticated') else 'REQUIRED'}")
                # Printer
                print(f"  3D Printer:    {'CONNECTED' if status.get('printer') else 'NOT CONFIGURED'}")
                print()
                continue

            response = loop.run_until_complete(leon.process_user_input(user_input))
            print(f"\nLeon > {response}\n")

    except KeyboardInterrupt:
        print("\n")

    loop.run_until_complete(leon.stop())
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

        def do_activate(self):
            if not self.win:
                self.leon_core = Leon(str(ROOT / "config" / "settings.yaml"))

                # Start Leon core
                def start_leon():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.leon_core.start())
                threading.Thread(target=start_leon, daemon=True).start()

                # Start dashboard
                def start_dashboard():
                    from dashboard.server import create_app
                    from aiohttp import web
                    app = create_app(leon_core=self.leon_core)
                    web.run_app(app, host="0.0.0.0", port=3000, print=lambda _: None)
                threading.Thread(target=start_dashboard, daemon=True).start()
                logger.info("🧠 Brain Dashboard: http://localhost:3000")

                # Start voice
                def start_voice():
                    from core.voice import VoiceSystem
                    async def handler(text):
                        return await self.leon_core.process_user_input(text)
                    vloop = asyncio.new_event_loop()
                    asyncio.set_event_loop(vloop)
                    voice = VoiceSystem(on_command=handler)
                    vloop.run_until_complete(voice.start())
                threading.Thread(target=start_voice, daemon=True).start()
                logger.info("🎤 Voice active — say 'Hey Leon'")

                self.win = LeonWindow(self, self.leon_core)
            self.win.present()

    app = LeonApp()
    app.run(sys.argv)


# ═════════════════════════════════════════════════════════
# HEADLESS MODE (Left Brain daemon — no REPL)
# ═════════════════════════════════════════════════════════

def run_headless():
    """Left Brain headless mode — bridge + dashboard, no terminal input."""
    import os
    os.environ["LEON_BRAIN_ROLE"] = "left"

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
    print("║    Bridge: wss://0.0.0.0:9100/bridge       ║")
    print("║    Dashboard: http://localhost:3000         ║")
    print("║    Ctrl+C to stop                          ║")
    print("╚════════════════════════════════════════════╝")
    print()

    # Run dashboard in the main event loop (avoids set_wakeup_fd thread error)
    app = create_app(leon_core=leon)
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "0.0.0.0", 3000)
    loop.run_until_complete(site.start())
    logger.info("Dashboard running on http://localhost:3000")

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        print("\n")
    finally:
        loop.run_until_complete(runner.cleanup())
        loop.run_until_complete(leon.stop())
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
    args = parser.parse_args()

    if args.right_brain:
        run_right_brain()
    elif args.left_brain:
        run_headless()
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
