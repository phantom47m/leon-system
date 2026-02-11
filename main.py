#!/usr/bin/env python3
"""
Leon - AI Orchestration System
Entry point: launches core system and GTK4 UI
"""

import sys
import logging
import asyncio
from pathlib import Path

# Ensure we're running from the project root
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ---- Logging ----
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

# ---- Ensure data dirs ----
(ROOT / "data" / "task_briefs").mkdir(parents=True, exist_ok=True)
(ROOT / "data" / "agent_outputs").mkdir(parents=True, exist_ok=True)


def run_cli():
    """Terminal-only mode (no GTK) — works over SSH or in a basic terminal."""
    from core.leon import Leon

    leon = Leon(str(ROOT / "config" / "settings.yaml"))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(leon.start())

    print("╔════════════════════════════════════════╗")
    print("║     🤖 Leon — AI Orchestrator v1.0     ║")
    print("║  Type your request. 'quit' to exit.    ║")
    print("╚════════════════════════════════════════╝")
    print()

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
                print(f"\nActive agents: {status['active_agents']}")
                tasks = status["tasks"]
                print(f"Active tasks:  {tasks['active']}")
                print(f"Queued tasks:  {tasks['queued']}")
                print(f"Completed:     {tasks['completed']}")
                for t in tasks.get("active_tasks", []):
                    print(f"  • {t['description'][:60]} ({t['project']})")
                print()
                continue

            response = loop.run_until_complete(leon.process_user_input(user_input))
            print(f"\nLeon > {response}\n")

    except KeyboardInterrupt:
        print("\n")

    loop.run_until_complete(leon.stop())
    print("Leon stopped. See you next time!")


def run_gui():
    """GTK4 GUI mode."""
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk, Gio, Adw
    except (ImportError, ValueError) as e:
        logger.warning(f"GTK4 not available ({e}), falling back to CLI mode")
        run_cli()
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
                # Start Leon core in background
                import threading

                def start_leon():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self.leon_core.start())

                threading.Thread(target=start_leon, daemon=True).start()

                self.win = LeonWindow(self, self.leon_core)
            self.win.present()

    app = LeonApp()
    app.run(sys.argv)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Leon AI Orchestrator")
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in terminal mode (no GUI)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Run with GTK4 GUI (default if available)",
    )
    args = parser.parse_args()

    if args.cli:
        run_cli()
    else:
        run_gui()


if __name__ == "__main__":
    main()
