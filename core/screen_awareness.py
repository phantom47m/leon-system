"""
Leon Screen Awareness — See what's on screen and offer proactive help

Periodically captures the screen, analyzes with AI, and maintains context
about what the user is working on. Can detect patterns like:
- User stuck on an error in terminal
- User browsing documentation (offer to summarize)
- User writing code (offer suggestions)
- Idle screen (suppress notifications)
"""

import asyncio
import base64
import io
import logging
import subprocess
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger("leon.screen")

# How often to capture (seconds)
DEFAULT_INTERVAL = 30
# Max history entries
MAX_HISTORY = 50
# Minimum time between AI analyses (avoid spamming API)
MIN_ANALYSIS_GAP = 60


class ScreenAwareness:
    """
    Monitors the user's screen to provide contextual awareness.

    Captures screenshots at intervals, sends to AI for analysis,
    and maintains a rolling context of what the user is doing.
    """

    def __init__(
        self,
        api_client=None,
        on_insight: Optional[Callable] = None,
        interval: float = DEFAULT_INTERVAL,
    ):
        """
        Args:
            api_client: AnthropicAPI instance for image analysis
            on_insight: Callback when Leon has a proactive suggestion
                        Signature: async def on_insight(insight: str)
            interval: Seconds between screen captures
        """
        self.api = api_client
        self.on_insight = on_insight
        self.interval = max(10, interval)
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Context tracking
        self.history: deque = deque(maxlen=MAX_HISTORY)
        self.current_context: dict = {
            "active_app": "unknown",
            "activity": "unknown",
            "last_update": None,
        }
        self._last_analysis_time = 0
        self._consecutive_idle = 0
        self._last_screenshot_hash = ""

        logger.info(f"Screen awareness initialized (interval={self.interval}s)")

    async def start(self):
        """Start the screen monitoring loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Screen awareness started")

    async def stop(self):
        """Stop screen monitoring."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Screen awareness stopped")

    def get_context(self) -> dict:
        """Return current screen context for Leon's brain."""
        return {
            **self.current_context,
            "history_count": len(self.history),
            "monitoring": self._running,
        }

    def get_recent_activity(self, n: int = 5) -> list:
        """Return the last N activity snapshots."""
        return list(self.history)[-n:]

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    async def _monitor_loop(self):
        """Main monitoring loop — capture, analyze, act."""
        while self._running:
            try:
                # Capture screenshot
                screenshot_b64 = await self._capture_screen()
                if not screenshot_b64:
                    await asyncio.sleep(self.interval)
                    continue

                # Quick hash check — skip analysis if screen hasn't changed
                quick_hash = hash(screenshot_b64[:1000] + screenshot_b64[-1000:])
                if quick_hash == self._last_screenshot_hash:
                    self._consecutive_idle += 1
                    # Back off if idle (double interval, max 5 min)
                    if self._consecutive_idle > 3:
                        await asyncio.sleep(min(self.interval * 4, 300))
                        continue
                else:
                    self._consecutive_idle = 0
                    self._last_screenshot_hash = quick_hash

                # Analyze if enough time has passed
                now = time.time()
                if now - self._last_analysis_time >= MIN_ANALYSIS_GAP and self.api:
                    analysis = await self._analyze_screen(screenshot_b64)
                    self._last_analysis_time = now

                    if analysis:
                        self._update_context(analysis)

                        # Check if we should proactively help
                        if analysis.get("should_help") and self.on_insight:
                            await self.on_insight(analysis.get("suggestion", ""))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Screen awareness error: {e}")

            await asyncio.sleep(self.interval)

    # ------------------------------------------------------------------
    # Screen capture
    # ------------------------------------------------------------------

    async def _capture_screen(self) -> Optional[str]:
        """Capture the screen and return base64-encoded JPEG."""
        try:
            # Use grim for Wayland, scrot for X11
            tmp_path = "/tmp/leon_screen_capture.jpg"

            # Try grim first (Wayland)
            proc = await asyncio.create_subprocess_exec(
                "grim", "-t", "jpeg", "-q", "50", "-s", "0.5", tmp_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                # Fallback to scrot (X11)
                proc = await asyncio.create_subprocess_exec(
                    "scrot", "-q", "50", "-o", tmp_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()

            if proc.returncode != 0:
                # Final fallback: gnome-screenshot
                proc = await asyncio.create_subprocess_exec(
                    "gnome-screenshot", "-f", tmp_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()

            path = Path(tmp_path)
            if path.exists() and path.stat().st_size > 0:
                with open(path, "rb") as f:
                    return base64.b64encode(f.read()).decode()
                # Don't delete — overwrite next time

            return None

        except Exception as e:
            logger.debug(f"Screen capture failed: {e}")
            return None

    # ------------------------------------------------------------------
    # AI analysis
    # ------------------------------------------------------------------

    async def _analyze_screen(self, screenshot_b64: str) -> Optional[dict]:
        """Send screenshot to AI for analysis."""
        if not self.api:
            return None

        # Build context from recent history
        recent = [h.get("activity", "") for h in list(self.history)[-3:]]
        recent_str = ", ".join(recent) if recent else "none"

        prompt = f"""Analyze this screenshot and respond with ONLY valid JSON (no markdown fences):

{{
  "active_app": "name of the foreground application",
  "activity": "brief description of what the user is doing (max 20 words)",
  "category": "coding|browsing|terminal|writing|media|communication|idle|other",
  "error_visible": false,
  "error_text": "",
  "should_help": false,
  "suggestion": "",
  "mood": "focused|struggling|browsing|idle"
}}

Rules:
- "should_help" = true ONLY if user appears stuck (error visible, same screen for long time, etc.)
- "suggestion" should be actionable and specific if should_help is true
- Recent activity: {recent_str}
- Keep descriptions very concise
- If screen is a lock screen or screensaver, set category to "idle"
"""

        try:
            result = await self.api.analyze_json(prompt, image_b64=screenshot_b64)
            return result
        except TypeError:
            # API client doesn't support image_b64 param yet — use text-only fallback
            return await self._analyze_screen_text_only()
        except Exception as e:
            logger.debug(f"Screen analysis failed: {e}")
            return None

    async def _analyze_screen_text_only(self) -> Optional[dict]:
        """Fallback: get active window info without screenshot analysis."""
        try:
            # Get active window title
            proc = await asyncio.create_subprocess_exec(
                "xdotool", "getactivewindow", "getwindowname",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            window_title = stdout.decode().strip() if stdout else "unknown"

            # Classify based on window title
            title_lower = window_title.lower()
            category = "other"
            activity = f"Using {window_title[:40]}"

            if any(t in title_lower for t in ["code", "vim", "neovim", "emacs", "jetbrains"]):
                category = "coding"
                activity = f"Writing code in {window_title[:30]}"
            elif any(t in title_lower for t in ["firefox", "chrome", "chromium", "brave"]):
                category = "browsing"
                activity = f"Browsing: {window_title[:40]}"
            elif any(t in title_lower for t in ["terminal", "alacritty", "kitty", "konsole", "gnome-terminal"]):
                category = "terminal"
                activity = f"Terminal: {window_title[:40]}"
            elif any(t in title_lower for t in ["discord", "slack", "telegram", "signal"]):
                category = "communication"
                activity = f"Chatting in {window_title[:30]}"
            elif any(t in title_lower for t in ["spotify", "vlc", "mpv"]):
                category = "media"
                activity = f"Media: {window_title[:30]}"

            return {
                "active_app": window_title[:50],
                "activity": activity,
                "category": category,
                "error_visible": False,
                "error_text": "",
                "should_help": False,
                "suggestion": "",
                "mood": "focused",
            }
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Context management
    # ------------------------------------------------------------------

    def _update_context(self, analysis: dict):
        """Update the rolling context with new analysis."""
        timestamp = datetime.now().isoformat()

        self.current_context = {
            "active_app": analysis.get("active_app", "unknown"),
            "activity": analysis.get("activity", "unknown"),
            "category": analysis.get("category", "other"),
            "mood": analysis.get("mood", "focused"),
            "last_update": timestamp,
        }

        self.history.append({
            **self.current_context,
            "timestamp": timestamp,
            "error_visible": analysis.get("error_visible", False),
            "error_text": analysis.get("error_text", ""),
        })

        logger.debug(
            f"Screen: {self.current_context['activity']} "
            f"[{self.current_context['category']}]"
        )
