"""
Discord bridge for the AI system.

Allows the owner to talk to their AI via Discord DMs or a private server channel.
Forwards messages to Leon's /api/message endpoint and posts responses back.

Setup:
  1. Go to https://discord.com/developers/applications
  2. Create a new application, add a Bot
  3. Enable "Message Content Intent" under Bot > Privileged Gateway Intents
  4. Copy the bot token into the setup wizard
  5. Invite the bot to your server with: Manage Messages + Send Messages + Read Messages

Usage:
  - DM the bot directly, or
  - Mention @BotName in any channel it has access to
  - "screenshot" → all monitors combined
  - "screenshot monitor 2" / "screenshot left monitor" → specific monitor
"""

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Ensure the project root (leon-system/) is on sys.path so that
# "from integrations.discord.dashboard import ..." works when bot.py
# is run directly (Python adds the script dir, not the project root).
_PROJECT_ROOT = str(Path(__file__).parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import aiohttp
import discord

CHANNEL_FILE = "/tmp/leon_discord_channel.json"
TOKEN_FILE   = "/tmp/leon_discord_bot_token.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [discord] %(levelname)s: %(message)s")

# Filter out RTCP / low-value library noise from our log handler
class _DropRTCP(logging.Filter):
    _DROP = ("rtcp packet", "heartbeat", "voice handshake", "timed out connecting to voice")
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage().lower()
        return not any(kw in msg for kw in self._DROP)

for _h in logging.getLogger().handlers:
    _h.addFilter(_DropRTCP())

# Suppress these noisy library loggers entirely
for _noisy in (
    "discord.ext.voice_recv.reader",
    "discord.ext.voice_recv",
    "discord.gateway",
):
    _l = logging.getLogger(_noisy)
    _l.setLevel(logging.WARNING)
    _l.propagate = False
logger = logging.getLogger("leon.discord")

MAX_DISCORD_LENGTH = 1900   # Discord limit is 2000 — leave headroom for splitting
RESPONSE_TIMEOUT   = 120    # seconds to wait for Leon response

SCREENSHOT_KEYWORDS = [
    "screenshot", "screen shot", "screengrab",
    "show me your screen", "show me the screen", "what's on your screen",
    "what's on the screen", "show me what's on", "show me what you see",
    "show me the monitor", "show me what's happening",
]


class AIDiscordBot(discord.Client):
    def __init__(
        self,
        leon_url: str,
        leon_token: str,
        allowed_user_ids: list[int],
        bot_token: str = "",
        config_root: str = "config",
        guild_id: int = 0,
    ):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.leon_url = leon_url.rstrip("/")
        self.leon_token = leon_token
        self.allowed_user_ids = set(allowed_user_ids)   # empty = allow all DMs
        self._bot_token = bot_token
        self._config_root = config_root
        self._guild_id = guild_id

    async def on_ready(self):
        logger.info("Discord bot online as %s (ID: %s)", self.user, self.user.id)
        logger.info("Allowed users: %s", self.allowed_user_ids or "all (DMs only)")
        # Save bot token so Leon can send proactive messages
        if self._bot_token:
            try:
                Path(TOKEN_FILE).write_text(self._bot_token)
            except Exception:
                pass
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="your commands"
        ))
        # Initialise the Discord dashboard (server layout + live stats)
        try:
            from integrations.discord.dashboard import init_dashboard
            db = init_dashboard(self, self._config_root)
            guild = (
                self.get_guild(self._guild_id) if self._guild_id else None
            ) or (self.guilds[0] if self.guilds else None)
            if guild:
                await db.setup(guild)
                await db.start_updater()
                # Point the proactive channel file at #chat so Leon messages land there
                chat_ch = db._channels.get("chat")
                if chat_ch:
                    Path(CHANNEL_FILE).write_text(
                        json.dumps({"channel_id": str(chat_ch.id), "is_dm": False})
                    )
        except Exception as e:
            logger.error("Dashboard setup failed (non-fatal): %s", e)

        # Initialise voice handler (join/listen/TTS pipeline)
        try:
            from integrations.discord.voice_handler import init_voice_manager
            init_voice_manager(
                bot=self,
                config_root=self._config_root,
                leon_url=self.leon_url,
                leon_token=self.leon_token,
                allowed_user_ids=self.allowed_user_ids,
            )
            logger.info("Voice: voice manager initialized")
        except Exception as e:
            logger.warning("Voice manager init failed (non-fatal): %s", e)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        try:
            from integrations.discord.voice_handler import get_voice_manager
            vm = get_voice_manager()
            if vm:
                await vm.on_voice_state_update(member, before, after)
        except Exception as e:
            logger.debug("Voice state update error: %s", e)

    async def on_message(self, message: discord.Message):
        # Ignore own messages
        if message.author == self.user:
            return

        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mention = self.user in message.mentions
        is_allowed = not self.allowed_user_ids or message.author.id in self.allowed_user_ids

        if not is_allowed:
            if is_dm or is_mention:
                await message.reply("You're not authorised to use this bot.")
            return

        # Check if this is the #chat channel
        is_chat_channel = False
        try:
            from integrations.discord.dashboard import get_dashboard
            db = get_dashboard()
            if db:
                chat_ch = db._channels.get("chat")
                if chat_ch and message.channel.id == chat_ch.id:
                    is_chat_channel = True
        except Exception:
            pass

        if not (is_dm or is_mention or is_chat_channel):
            return

        # Strip bot mention from message text
        text = message.content
        if self.user.mention in text:
            text = text.replace(self.user.mention, "").strip()
        if not text:
            return

        msg_lower = text.lower()

        # Save channel so Leon can send proactive updates (skip if #chat already set on_ready)
        if not is_chat_channel:
            try:
                Path(CHANNEL_FILE).write_text(json.dumps({
                    "channel_id": str(message.channel.id),
                    "is_dm": isinstance(message.channel, discord.DMChannel),
                }))
            except Exception:
                pass

        # Screenshot request
        if any(kw in msg_lower for kw in SCREENSHOT_KEYWORDS):
            async with message.channel.typing():
                loop = asyncio.get_event_loop()
                monitors = await loop.run_in_executor(None, _get_monitors)
                monitor = _pick_monitor(monitors, msg_lower)
                path = await _take_screenshot(monitor)

            if monitor:
                idx = monitors.index(monitor) + 1
                label = f"Monitor {idx} ({monitor['name']}, {monitor['width']}×{monitor['height']})"
            else:
                label = f"All {len(monitors)} monitors" if monitors else "Screen"

            if path:
                try:
                    from integrations.discord.dashboard import get_dashboard as _gd
                    _db = _gd()
                    if _db:
                        await _db.post_screenshot(path, f"Requested by {message.author.display_name}")
                        await message.reply("Posted to #screenshots.")
                    else:
                        await message.reply(f"{label}:", file=discord.File(path, filename="screenshot.png"))
                except Exception:
                    await message.reply(f"{label}:", file=discord.File(path, filename="screenshot.png"))
                try:
                    os.unlink(path)
                except OSError:
                    pass
            else:
                # Don't just apologize — trigger self-repair automatically
                await message.reply(
                    "Screenshot failed. Dispatching Agent Zero to diagnose and fix the screenshot method now..."
                )
                session_type = os.environ.get("XDG_SESSION_TYPE", "unknown")
                wayland = os.environ.get("WAYLAND_DISPLAY", "")
                repair_msg = (
                    f"SELF REPAIR NEEDED: _take_screenshot() just returned None when the user requested a screenshot. "
                    f"System info: XDG_SESSION_TYPE={session_type}, WAYLAND_DISPLAY={wayland}. "
                    f"The broken function is in integrations/discord/bot.py. "
                    f"Check logs/discord_bridge.log for the specific error, then fix _take_screenshot() "
                    f"so it works on this Wayland/COSMIC system. "
                    f"After fixing, apply the patch and restart the discord bridge."
                )
                asyncio.create_task(self._ask_leon(repair_msg, str(message.author)))
            return

        # Show typing indicator while waiting for Leon
        async with message.channel.typing():
            response = await self._ask_leon(text, str(message.author))

        # Check if Leon flagged a screenshot should be sent alongside the response
        wants_screenshot = "[SCREENSHOT]" in response
        clean_response = response.replace("[SCREENSHOT]", "").strip()

        chunks = _split_message(clean_response) if clean_response else []
        for i, chunk in enumerate(chunks):
            if i == 0 and wants_screenshot:
                path = await _take_screenshot(None)
                if path:
                    await message.reply(chunk, file=discord.File(path, filename="screenshot.png"))
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
                else:
                    await message.reply(chunk)
                    self._trigger_screenshot_repair(str(message.author))
            else:
                await message.reply(chunk)

        # Play TTS in voice channel if Leon is connected there
        if clean_response:
            try:
                from integrations.discord.voice_handler import get_voice_manager
                vm = get_voice_manager()
                if vm:
                    asyncio.create_task(vm._play_tts(clean_response))
            except Exception:
                pass

        if not chunks and wants_screenshot:
            path = await _take_screenshot(None)
            if path:
                await message.reply(file=discord.File(path, filename="screenshot.png"))
                try:
                    os.unlink(path)
                except OSError:
                    pass
            else:
                self._trigger_screenshot_repair(str(message.author))

    def _trigger_screenshot_repair(self, author: str) -> None:
        """Whenever a screenshot silently fails, automatically ask Leon to fix it via Agent Zero."""
        session_type = os.environ.get("XDG_SESSION_TYPE", "unknown")
        wayland = os.environ.get("WAYLAND_DISPLAY", "")
        repair_msg = (
            f"SELF REPAIR NEEDED: _take_screenshot() returned None — screenshot capability is broken. "
            f"System: XDG_SESSION_TYPE={session_type}, WAYLAND_DISPLAY={wayland}. "
            f"File to fix: integrations/discord/bot.py (_take_screenshot function). "
            f"Check logs/discord_bridge.log for the error, fix the function so it works on "
            f"this Wayland/COSMIC desktop, then apply the patch and restart the Discord bridge."
        )
        asyncio.create_task(self._ask_leon(repair_msg, author))
        logger.warning("Screenshot failed — self-repair dispatched to Leon/Agent Zero")

    async def _ask_leon(self, text: str, author: str) -> str:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.leon_url}/api/message",
                    headers={"Authorization": f"Bearer {self.leon_token}"},
                    json={"message": text, "source": f"discord:{author}"},
                    timeout=aiohttp.ClientTimeout(total=RESPONSE_TIMEOUT),
                ) as resp:
                    if resp.status == 401:
                        return "Auth error — check the bot token in your Leon setup."
                    if resp.status == 429:
                        return "I'm being rate limited. Try again in a moment."
                    if resp.status != 200:
                        return f"Error {resp.status} from Leon. Is it running?"
                    data = await resp.json()
                    return data.get("response") or "No response."
        except asyncio.TimeoutError:
            return "Leon took too long to respond. It might be busy."
        except aiohttp.ClientConnectorError:
            return "Can't reach Leon. Make sure it's running on your machine."
        except Exception as e:
            logger.error("Unexpected error: %s", e)
            return f"Something went wrong: {e}"


# ── Screenshot helpers ────────────────────────────────────────────────────────

def _get_monitors() -> list[dict]:
    """Parse xrandr output and return connected monitors sorted left → right."""
    try:
        env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
        result = subprocess.run(["xrandr"], capture_output=True, text=True, timeout=5, env=env)
        monitors = []
        for line in result.stdout.splitlines():
            m = re.match(r"(\S+) connected (primary )?(\d+)x(\d+)\+(\d+)\+(\d+)", line)
            if m:
                monitors.append({
                    "name":    m.group(1),
                    "primary": bool(m.group(2)),
                    "width":   int(m.group(3)),
                    "height":  int(m.group(4)),
                    "x":       int(m.group(5)),
                    "y":       int(m.group(6)),
                })
        monitors.sort(key=lambda mon: mon["x"])
        logger.info("Detected %d monitor(s): %s", len(monitors),
                    [(m["name"], f"{m['width']}x{m['height']}+{m['x']}+{m['y']}") for m in monitors])
        return monitors
    except Exception as e:
        logger.warning("xrandr failed: %s", e)
        return []


def _pick_monitor(monitors: list[dict], hint: str) -> dict | None:
    """
    Pick a monitor from natural language. Returns None → capture all.

    Examples:
      "screenshot monitor 2"     → monitors[1]
      "screenshot left monitor"  → monitors[0]
      "screenshot right screen"  → monitors[-1]
      "screenshot middle"        → monitors[middle index]
      "screenshot main"          → whichever is primary
      "screenshot"               → None (all)
    """
    if not monitors:
        return None

    h = hint.lower()

    # Primary / main
    if any(w in h for w in ["primary", "main", "default"]):
        for mon in monitors:
            if mon["primary"]:
                return mon
        return monitors[0]

    # Numbered: "monitor 1", "screen 2", "display 3", "#2"
    num_match = re.search(r"(?:monitor|screen|display|#)\s*(\d+)", h)
    if num_match:
        n = int(num_match.group(1))
        if 1 <= n <= len(monitors):
            return monitors[n - 1]

    # Position keywords
    if any(w in h for w in ["left", "first", "1st"]):
        return monitors[0]
    if any(w in h for w in ["right", "last", "third", "3rd"]):
        return monitors[-1]
    if any(w in h for w in ["center", "centre", "middle", "second", "2nd"]):
        return monitors[len(monitors) // 2]

    # Named monitor (e.g. "show me DP-1")
    for mon in monitors:
        if mon["name"].lower() in h:
            return mon

    return None  # no hint → capture everything


async def _take_screenshot(monitor: dict | None = None) -> str | None:
    """
    Capture the screen (or a single monitor) and return the temp file path.

    On Wayland (Pop!_OS default), scrot/import capture a black XWayland root window.
    The fix: capture the full desktop via the freedesktop D-Bus screenshot portal,
    then crop to the requested monitor geometry using Pillow.

    Falls back through multiple methods so it always degrades gracefully.
    """
    path = os.path.join(tempfile.gettempdir(), "leon_discord_screenshot.png")
    full_path = os.path.join(tempfile.gettempdir(), "leon_discord_full.png")
    loop = asyncio.get_event_loop()

    session_type = os.environ.get("XDG_SESSION_TYPE", "x11").lower()
    wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":1")}

    def _run() -> str | None:
        # ── Method 1: pyscreenshot via subprocess (Wayland-native, own D-Bus mainloop) ──
        # freedesktop_dbus backend FAILS when called inside run_in_executor (thread pool)
        # because the D-Bus portal is async and needs its own process/mainloop.
        # Spawning a subprocess gives it a clean environment with its own event loop.
        if session_type == "wayland" or wayland_display:
            try:
                # bot.py lives at integrations/discord/bot.py → need 3 dirnames to reach project root
                _bot_dir = os.path.dirname(os.path.abspath(__file__))          # .../integrations/discord
                _integ_dir = os.path.dirname(_bot_dir)                          # .../integrations
                _project_root = os.path.dirname(_integ_dir)                     # .../leon-system
                venv_python = os.path.join(_project_root, "venv", "bin", "python3")
                if not os.path.exists(venv_python):
                    venv_python = os.path.join(_project_root, "venv", "bin", "python")
                if not os.path.exists(venv_python):
                    venv_python = "python3"
                logger.warning("pyscreenshot subprocess: using python=%s", venv_python)
                script = (
                    "import pyscreenshot as I; "
                    f"img=I.grab(backend='freedesktop_dbus'); "
                    f"img.save({full_path!r}); "
                    f"print('ok', img.size)"
                )
                r = subprocess.run(
                    [venv_python, "-c", script],
                    capture_output=True, timeout=20, env=env
                )
                logger.warning("pyscreenshot subprocess rc=%s stdout=%s stderr=%s",
                               r.returncode, r.stdout[:300], r.stderr[:300])
                if r.returncode == 0 and _file_ok(full_path):
                    if monitor:
                        _crop_and_save(full_path, path, monitor)
                    else:
                        import shutil; shutil.copy2(full_path, path)
                    if _file_ok(path):
                        return path
            except Exception as e:
                logger.warning("pyscreenshot subprocess exception: %s", e)

        # ── Method 2: gnome-screenshot (works on X11 and Wayland via portal) ──
        try:
            r = subprocess.run(
                ["gnome-screenshot", "-f", full_path],
                capture_output=True, timeout=10, env=env
            )
            if r.returncode == 0 and _file_ok(full_path):
                if monitor:
                    _crop_and_save(full_path, path, monitor)
                else:
                    import shutil; shutil.copy2(full_path, path)
                if _file_ok(path):
                    return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # ── Method 3: grim (Wayland-native, needs to be installed) ──
        if session_type == "wayland" or wayland_display:
            try:
                grim_cmd = ["grim"]
                if monitor:
                    x, y, w, h = monitor["x"], monitor["y"], monitor["width"], monitor["height"]
                    grim_cmd += ["-g", f"{x},{y} {w}x{h}"]
                grim_cmd.append(path)
                r = subprocess.run(grim_cmd, capture_output=True, timeout=10, env=env)
                if r.returncode == 0 and _file_ok(path):
                    return path
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

        # ── Method 4: scrot / ImageMagick (X11 only — black on Wayland, last resort) ──
        if monitor:
            x, y, w, h = monitor["x"], monitor["y"], monitor["width"], monitor["height"]
            x11_cmds = [
                ["scrot", "-a", f"{x},{y},{w},{h}", "-q", "85", path],
                ["import", "-window", "root", "-crop", f"{w}x{h}+{x}+{y}", "+repage", path],
            ]
        else:
            x11_cmds = [
                ["scrot", "-q", "85", path],
                ["import", "-window", "root", path],
            ]
        for cmd in x11_cmds:
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=10, env=env)
                if r.returncode == 0 and _file_ok(path) and not _is_black_image(path):
                    return path
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

        logger.error("All screenshot methods failed for session_type=%s", session_type)
        return None

    try:
        return await loop.run_in_executor(None, _run)
    except Exception as e:
        logger.error("Screenshot error: %s", e)
        return None


def _file_ok(path: str, min_bytes: int = 1000) -> bool:
    return os.path.exists(path) and os.path.getsize(path) > min_bytes


def _is_real_image(img) -> bool:
    """Return True if the image has actual content (not all black)."""
    try:
        # Sample a few pixels across the image
        w, h = img.size
        samples = [img.getpixel((w * i // 8, h // 2)) for i in range(1, 8)]
        return any(sum(p[:3]) > 30 for p in samples)
    except Exception:
        return True  # assume real if we can't check


def _is_black_image(path: str) -> bool:
    """Return True if a saved image file is all-black (captures failed silently)."""
    try:
        from PIL import Image
        img = Image.open(path)
        w, h = img.size
        samples = [img.getpixel((w * i // 8, h // 2)) for i in range(1, 8)]
        return all(sum(p[:3]) <= 30 for p in samples)
    except Exception:
        return False


def _crop_and_save(src_path: str, dst_path: str, monitor: dict):
    """Crop a full-desktop screenshot to a specific monitor and save."""
    try:
        from PIL import Image
        img = Image.open(src_path)
        x, y, w, h = monitor["x"], monitor["y"], monitor["width"], monitor["height"]
        img.crop((x, y, x + w, y + h)).save(dst_path, "PNG", optimize=True, quality=85)
    except Exception as e:
        logger.warning("Crop failed: %s", e)
        import shutil; shutil.copy2(src_path, dst_path)


# ── Message helpers ───────────────────────────────────────────────────────────

def _split_message(text: str) -> list[str]:
    """Split a long message into Discord-safe chunks."""
    if len(text) <= MAX_DISCORD_LENGTH:
        return [text]
    chunks = []
    while text:
        if len(text) <= MAX_DISCORD_LENGTH:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, MAX_DISCORD_LENGTH)
        if split_at == -1:
            split_at = MAX_DISCORD_LENGTH
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def _load_token_from_config(config_dir: str) -> str:
    """Read api_token.txt generated at Leon startup."""
    token_file = os.path.join(config_dir, "api_token.txt")
    if os.path.exists(token_file):
        return open(token_file).read().strip()
    return ""


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Discord bridge for Leon AI")
    parser.add_argument("--token",         required=True,                      help="Discord bot token")
    parser.add_argument("--leon-url",      default="http://localhost:3000",    help="Leon dashboard URL")
    parser.add_argument("--leon-token",    default="",                         help="Leon API bearer token")
    parser.add_argument("--config-dir",    default="config",                   help="Path to Leon config dir")
    parser.add_argument("--config-root",   default="config",                   help="Path to Leon config root (for dashboard)")
    parser.add_argument("--allowed-users", default="",                         help="Comma-separated Discord user IDs")
    parser.add_argument("--guild-id",      default=0, type=int,                help="Discord guild ID (auto-detects if omitted)")
    args = parser.parse_args()

    leon_token = args.leon_token or _load_token_from_config(args.config_dir)
    if not leon_token:
        logger.error("No Leon API token found. Pass --leon-token or ensure config/api_token.txt exists.")
        sys.exit(1)

    allowed_ids = []
    if args.allowed_users:
        try:
            allowed_ids = [int(uid.strip()) for uid in args.allowed_users.split(",") if uid.strip()]
        except ValueError:
            logger.error("--allowed-users must be comma-separated Discord user IDs (numbers)")
            sys.exit(1)

    bot = AIDiscordBot(
        leon_url=args.leon_url,
        leon_token=leon_token,
        allowed_user_ids=allowed_ids,
        bot_token=args.token,
        config_root=args.config_root,
        guild_id=args.guild_id,
    )

    logger.info("Starting Discord bot...")
    bot.run(args.token)


if __name__ == "__main__":
    main()
