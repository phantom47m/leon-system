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
import logging
import os
import re
import subprocess
import sys
import tempfile

import aiohttp
import discord

logging.basicConfig(level=logging.INFO, format="%(asctime)s [discord] %(levelname)s: %(message)s")
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
    def __init__(self, leon_url: str, leon_token: str, allowed_user_ids: list[int]):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.leon_url = leon_url.rstrip("/")
        self.leon_token = leon_token
        self.allowed_user_ids = set(allowed_user_ids)   # empty = allow all DMs

    async def on_ready(self):
        logger.info("Discord bot online as %s (ID: %s)", self.user, self.user.id)
        logger.info("Allowed users: %s", self.allowed_user_ids or "all (DMs only)")
        await self.change_presence(activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="your commands"
        ))

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

        # Strip bot mention from message text
        text = message.content
        if self.user.mention in text:
            text = text.replace(self.user.mention, "").strip()
        if not text:
            return

        msg_lower = text.lower()

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
                await message.reply(f"{label}:", file=discord.File(path, filename="screenshot.png"))
                try:
                    os.unlink(path)
                except OSError:
                    pass
            else:
                await message.reply("Couldn't take a screenshot — scrot not installed.")
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
            else:
                await message.reply(chunk)

        if not chunks and wants_screenshot:
            path = await _take_screenshot(None)
            if path:
                await message.reply(file=discord.File(path, filename="screenshot.png"))
                try:
                    os.unlink(path)
                except OSError:
                    pass

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
    Uses scrot with geometry for per-monitor crops; falls back to ImageMagick import.
    """
    path = os.path.join(tempfile.gettempdir(), "leon_discord_screenshot.png")
    env  = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}
    loop = asyncio.get_event_loop()

    def _run():
        if monitor:
            x, y, w, h = monitor["x"], monitor["y"], monitor["width"], monitor["height"]
            cmds = [
                # scrot: -a x,y,w,h
                ["scrot", "-a", f"{x},{y},{w},{h}", "-q", "85", path],
                # ImageMagick import with crop
                ["import", "-window", "root",
                 "-crop", f"{w}x{h}+{x}+{y}", "+repage", path],
            ]
        else:
            cmds = [
                ["scrot", "-q", "85", path],
                ["gnome-screenshot", "-f", path],
                ["import", "-window", "root", path],
            ]

        for cmd in cmds:
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=10, env=env)
                if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 0:
                    return path
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return None

    try:
        return await loop.run_in_executor(None, _run)
    except Exception as e:
        logger.error("Screenshot error: %s", e)
        return None


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
    parser.add_argument("--allowed-users", default="",                         help="Comma-separated Discord user IDs")
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
    )

    logger.info("Starting Discord bot...")
    bot.run(args.token)


if __name__ == "__main__":
    main()
