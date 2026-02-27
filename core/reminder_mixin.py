"""
ReminderMixin — extracted from core/leon.py to keep that file manageable.

Contains: _fire_reminder, _save_reminders, _load_reminders
All self.* references resolve through Leon's MRO at runtime.
"""

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger("leon")


class ReminderMixin:
    """Persistent reminder fire/load/save methods."""

    async def _fire_reminder(self, task: str, reminder_id: str = "") -> None:
        """Deliver a user-set reminder: Discord #chat message + TTS if in voice channel."""
        text = f"⏰ **Reminder:** {task}"
        logger.info("Reminder fired: %s (id=%s)", task, reminder_id)
        # Remove from persistent store
        self._pending_reminders.pop(reminder_id, None)
        self._save_reminders()
        # Discord #chat notification
        await self._send_discord_message(text, channel="chat")
        # TTS in voice channel if active
        try:
            from integrations.discord.voice_handler import get_voice_manager
            vm = get_voice_manager()
            if vm and vm._vc and vm._vc.is_connected():
                await vm._play_tts(f"Reminder: {task}")
        except Exception as e:
            logger.debug("Reminder TTS error: %s", e)

    def _save_reminders(self) -> None:
        """Write pending reminders to data/reminders.json."""
        try:
            data = {
                "reminders": [
                    {"id": rid, "task": info["task"], "fire_at": info["fire_at"]}
                    for rid, info in self._pending_reminders.items()
                ]
            }
            Path("data/reminders.json").write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.warning("Could not save reminders: %s", e)

    def _load_reminders(self) -> None:
        """Load reminders from disk and reschedule any that haven't fired yet."""
        import time as _time
        path = Path("data/reminders.json")
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            reminders = data.get("reminders", [])
            now = _time.time()
            loop = asyncio.get_event_loop()
            rescheduled = 0
            for r in reminders:
                rid  = r.get("id", "")
                task = r.get("task", "")
                fire_at = float(r.get("fire_at", 0))
                if not task:
                    continue
                delay = max(0, fire_at - now)
                self._pending_reminders[rid] = {"task": task, "fire_at": fire_at}
                loop.call_later(delay, lambda t=task, i=rid: asyncio.ensure_future(self._fire_reminder(t, i)))
                rescheduled += 1
                if delay == 0:
                    logger.info("Reminder past-due, firing immediately: %s", task)
                else:
                    mins = int(delay) // 60
                    secs = int(delay) % 60
                    logger.info(
                        "Reminder rescheduled: '%s' fires in %dm %ds (id=%s)",
                        task, mins, secs, rid,
                    )
            if rescheduled:
                logger.info("Restored %d pending reminder(s) from disk", rescheduled)
        except Exception as e:
            logger.warning("Could not load reminders: %s", e)
