#!/usr/bin/env python3
"""
Launch Leon core with the brain dashboard in headless mode.
No CLI input required — interact via the dashboard at http://localhost:3000
"""

import sys
import logging
import asyncio
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Logging
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

# Data dirs
(ROOT / "data" / "task_briefs").mkdir(parents=True, exist_ok=True)
(ROOT / "data" / "agent_outputs").mkdir(parents=True, exist_ok=True)


async def run():
    from core.leon import Leon
    from dashboard.server import create_app
    from aiohttp import web

    leon = Leon(str(ROOT / "config" / "settings.yaml"))
    await leon.start()

    app = create_app(leon_core=leon)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 3000)
    await site.start()

    logger.info("Leon Brain Dashboard running at http://localhost:3000")
    print("\n╔════════════════════════════════════════════╗")
    print("║  Leon Brain Dashboard: http://localhost:3000  ║")
    print("║  Press Ctrl+C to stop                         ║")
    print("╚════════════════════════════════════════════╝\n")

    # Keep running until interrupted
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await leon.stop()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nLeon stopped.")
