"""
Leon Hotkey Listener — Global keyboard shortcuts

Push-to-talk, voice toggle, and other hotkeys using pynput.
Runs as a daemon thread alongside the main event loop.
"""

import asyncio
import logging
import threading
from typing import Optional

logger = logging.getLogger("leon.hotkeys")

# Key name mapping for config
KEY_MAP = {
    "scroll_lock": "scroll_lock",
    "f9": "f9",
    "f10": "f10",
    "f11": "f11",
    "f12": "f12",
    "pause": "pause",
    "insert": "insert",
}


class HotkeyListener:
    """
    Global keyboard listener for Leon.

    - Push-to-talk: Hold configured key to capture voice, release to process
    - Super+L: Toggle voice system on/off
    """

    def __init__(self, voice_system=None, ptt_key: str = "scroll_lock"):
        self.voice_system = voice_system
        self.ptt_key_name = ptt_key.lower().replace(" ", "_")
        self._ptt_active = False
        self._voice_enabled = True
        self._listener = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False

        logger.info(f"Hotkey listener initialized — push-to-talk: {self.ptt_key_name}")

    def start(self, loop: asyncio.AbstractEventLoop):
        """Start the global keyboard listener as a daemon thread."""
        self._loop = loop
        self._running = True

        thread = threading.Thread(target=self._run_listener, daemon=True, name="hotkey-listener")
        thread.start()
        logger.info("Hotkey listener started")

    def stop(self):
        """Stop the keyboard listener."""
        self._running = False
        if self._listener:
            self._listener.stop()
        logger.info("Hotkey listener stopped")

    def _run_listener(self):
        """Run pynput keyboard listener in background thread."""
        try:
            from pynput import keyboard

            # Resolve the push-to-talk key
            self._ptt_key = self._resolve_key(keyboard, self.ptt_key_name)

            def on_press(key):
                if not self._running:
                    return False

                # Push-to-talk: key down → start listening
                if self._matches_key(key, self._ptt_key):
                    if not self._ptt_active:
                        self._ptt_active = True
                        self._on_ptt_start()

            def on_release(key):
                if not self._running:
                    return False

                # Push-to-talk: key up → process command
                if self._matches_key(key, self._ptt_key):
                    if self._ptt_active:
                        self._ptt_active = False
                        self._on_ptt_stop()

            self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self._listener.start()
            self._listener.join()

        except ImportError:
            logger.warning("pynput not available — hotkeys disabled. Install: pip install pynput")
        except Exception as e:
            logger.error(f"Hotkey listener error: {e}")

    def _resolve_key(self, keyboard_module, key_name: str):
        """Resolve a key name string to a pynput Key object."""
        key_name = key_name.lower().replace(" ", "_")

        # Check special keys
        special = {
            "scroll_lock": keyboard_module.Key.scroll_lock,
            "f1": keyboard_module.Key.f1,
            "f2": keyboard_module.Key.f2,
            "f3": keyboard_module.Key.f3,
            "f4": keyboard_module.Key.f4,
            "f5": keyboard_module.Key.f5,
            "f6": keyboard_module.Key.f6,
            "f7": keyboard_module.Key.f7,
            "f8": keyboard_module.Key.f8,
            "f9": keyboard_module.Key.f9,
            "f10": keyboard_module.Key.f10,
            "f11": keyboard_module.Key.f11,
            "f12": keyboard_module.Key.f12,
            "pause": keyboard_module.Key.pause,
            "insert": keyboard_module.Key.insert,
            "home": keyboard_module.Key.home,
            "end": keyboard_module.Key.end,
            "page_up": keyboard_module.Key.page_up,
            "page_down": keyboard_module.Key.page_down,
        }

        return special.get(key_name, keyboard_module.Key.scroll_lock)

    def _matches_key(self, pressed, target) -> bool:
        """Check if a pressed key matches the target."""
        try:
            return pressed == target
        except Exception:
            return False

    def _on_ptt_start(self):
        """Push-to-talk key pressed — activate voice listening."""
        if not self.voice_system:
            return

        logger.info("Push-to-talk: ACTIVE")
        self.voice_system.is_awake = True

        # Send notification
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._notify_ptt("Listening..."),
                self._loop,
            )

    def _on_ptt_stop(self):
        """Push-to-talk key released — stop and process."""
        if not self.voice_system:
            return

        logger.info("Push-to-talk: RELEASED")
        # The voice system will process whatever was captured while awake
        # The sleep timer in voice.py handles going back to sleep

    async def _notify_ptt(self, msg: str):
        """Show a notification for push-to-talk state changes."""
        import subprocess
        subprocess.run(
            ["notify-send", "-t", "2000", "Leon", msg],
            capture_output=True,
        )

    def toggle_voice(self):
        """Toggle voice system on/off."""
        if not self.voice_system:
            logger.warning("No voice system to toggle")
            return

        self._voice_enabled = not self._voice_enabled
        if self._voice_enabled:
            self.voice_system.is_listening = True
            logger.info("Voice system enabled")
        else:
            self.voice_system.is_listening = False
            self.voice_system.is_awake = False
            logger.info("Voice system disabled")
