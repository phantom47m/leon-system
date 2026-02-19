"""
Leon Notification System — Proactive desktop alerts

Unified notification queue for agent completions, scheduled tasks,
screen awareness insights, timer alerts, and system events.
Supports priority levels and rate limiting to avoid notification spam.
"""

import asyncio
import logging
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.notify")


class Priority(IntEnum):
    LOW = 0       # Informational (agent completed normally)
    NORMAL = 1    # Standard notifications (scheduled task results)
    HIGH = 2      # Important (agent failed, screen insight)
    URGENT = 3    # Critical (security alert, system error)


@dataclass
class Notification:
    title: str
    message: str
    priority: Priority = Priority.NORMAL
    source: str = "leon"          # agent, scheduler, screen, timer, system
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    delivered: bool = False
    sound: bool = False           # Play alert sound


# Rate limiting
MAX_PER_MINUTE = 5
COOLDOWN_SECONDS = {
    Priority.LOW: 30,
    Priority.NORMAL: 10,
    Priority.HIGH: 3,
    Priority.URGENT: 0,
}

# Notification sounds
SOUNDS = [
    "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "/usr/share/sounds/freedesktop/stereo/message.oga",
    "/usr/share/sounds/gnome/default/alerts/glass.ogg",
]


class NotificationManager:
    """
    Manages Leon's desktop notifications with priority and rate limiting.

    Usage:
        notifier = NotificationManager()
        await notifier.start()
        notifier.push("Agent Done", "Fixed the login bug", Priority.NORMAL, "agent")
    """

    def __init__(self):
        self.queue: deque = deque(maxlen=200)
        self.history: deque = deque(maxlen=500)
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_notify_time: dict = {}  # source -> timestamp
        self._notify_count_window: list = []  # timestamps of recent notifications
        self._recent_hashes: deque = deque(maxlen=50)  # dedup: (hash, timestamp) pairs
        self._sound_path: Optional[str] = None

        # Find a working sound file
        for s in SOUNDS:
            if Path(s).exists():
                self._sound_path = s
                break

        logger.info("Notification manager initialized")

    async def start(self):
        """Start the notification delivery loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._delivery_loop())
        logger.info("Notification manager started")

    async def stop(self):
        """Stop the notification manager."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Notification manager stopped")

    def push(
        self,
        title: str,
        message: str,
        priority: Priority = Priority.NORMAL,
        source: str = "leon",
        sound: bool = False,
    ):
        """Add a notification to the queue."""
        notif = Notification(
            title=title,
            message=message,
            priority=priority,
            source=source,
            sound=sound or priority >= Priority.HIGH,
        )
        self.queue.append(notif)
        logger.debug(f"Notification queued: [{priority.name}] {title}")

    def push_agent_completed(self, agent_id: str, summary: str):
        """Convenience: push notification for agent completion."""
        self.push(
            title=f"Agent #{agent_id[-8:]} Done",
            message=summary[:200],
            priority=Priority.LOW,
            source="agent",
        )

    def push_agent_failed(self, agent_id: str, error: str):
        """Convenience: push notification for agent failure."""
        self.push(
            title=f"Agent #{agent_id[-8:]} Failed",
            message=error[:200],
            priority=Priority.HIGH,
            source="agent",
            sound=True,
        )

    def push_screen_insight(self, insight: str):
        """Convenience: push a screen awareness suggestion."""
        self.push(
            title="Leon noticed something",
            message=insight[:200],
            priority=Priority.NORMAL,
            source="screen",
        )

    def push_scheduled(self, task_name: str, result: str):
        """Convenience: push scheduled task result."""
        self.push(
            title=f"Scheduled: {task_name}",
            message=result[:200],
            priority=Priority.LOW,
            source="scheduler",
        )

    def push_system(self, title: str, message: str, urgent: bool = False):
        """Convenience: push system notification."""
        self.push(
            title=title,
            message=message,
            priority=Priority.URGENT if urgent else Priority.NORMAL,
            source="system",
            sound=urgent,
        )

    def get_recent(self, n: int = 20) -> list:
        """Return recent notification history."""
        items = list(self.history)[-n:]
        return [
            {
                "title": notif.title,
                "message": notif.message,
                "priority": notif.priority.name,
                "source": notif.source,
                "timestamp": notif.timestamp,
                "delivered": notif.delivered,
            }
            for notif in items
        ]

    def get_stats(self) -> dict:
        """Return notification statistics."""
        total = len(self.history)
        by_source = {}
        by_priority = {}
        for n in self.history:
            by_source[n.source] = by_source.get(n.source, 0) + 1
            by_priority[n.priority.name] = by_priority.get(n.priority.name, 0) + 1

        return {
            "total": total,
            "pending": len(self.queue),
            "by_source": by_source,
            "by_priority": by_priority,
        }

    # ------------------------------------------------------------------
    # Delivery loop
    # ------------------------------------------------------------------

    async def _delivery_loop(self):
        """Process notification queue and deliver to desktop."""
        while self._running:
            try:
                while self.queue:
                    notif = self.queue.popleft()

                    # Rate limit check
                    if not self._should_deliver(notif):
                        notif.delivered = False
                        self.history.append(notif)
                        continue

                    # Deliver
                    await self._deliver(notif)
                    notif.delivered = True
                    self.history.append(notif)

                    # Track for rate limiting
                    now = time.time()
                    self._notify_count_window.append(now)
                    self._last_notify_time[notif.source] = now

                    # Small gap between notifications
                    await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Notification delivery error: {e}")

            await asyncio.sleep(1)

    def _should_deliver(self, notif: Notification) -> bool:
        """Check rate limits before delivering."""
        now = time.time()

        # Global rate: max N per minute
        self._notify_count_window = [
            t for t in self._notify_count_window if now - t < 60
        ]
        if len(self._notify_count_window) >= MAX_PER_MINUTE:
            if notif.priority < Priority.URGENT:
                logger.debug(f"Rate limited: {notif.title}")
                return False

        # Per-source cooldown
        cooldown = COOLDOWN_SECONDS.get(notif.priority, 10)
        last = self._last_notify_time.get(notif.source, 0)
        if now - last < cooldown:
            if notif.priority < Priority.HIGH:
                return False

        # Dedup: skip identical title+message within 60s
        content_hash = hash((notif.title, notif.message))
        for h, t in self._recent_hashes:
            if h == content_hash and now - t < 60:
                logger.debug(f"Deduplicated: {notif.title}")
                return False
        self._recent_hashes.append((content_hash, now))

        return True

    async def _deliver(self, notif: Notification):
        """Send a desktop notification via notify-send."""
        urgency = {
            Priority.LOW: "low",
            Priority.NORMAL: "normal",
            Priority.HIGH: "normal",
            Priority.URGENT: "critical",
        }.get(notif.priority, "normal")

        # Timeout in ms (urgent stays longer)
        timeout = "10000" if notif.priority >= Priority.HIGH else "5000"

        try:
            proc = await asyncio.create_subprocess_exec(
                "notify-send",
                "-u", urgency,
                "-t", timeout,
                "-a", "Leon AI",
                notif.title,
                notif.message,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
        except Exception as e:
            logger.debug(f"notify-send failed: {e}")

        # Play sound if requested
        if notif.sound and self._sound_path:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "paplay", self._sound_path,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                # Don't await — fire and forget
            except Exception:
                pass

        logger.info(
            f"[{notif.priority.name}] {notif.title}: {notif.message[:60]}"
        )
