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
"""

import argparse
import asyncio
import logging
import os
import sys

import aiohttp
import discord

logging.basicConfig(level=logging.INFO, format="%(asctime)s [discord] %(levelname)s: %(message)s")
logger = logging.getLogger("leon.discord")

MAX_DISCORD_LENGTH = 1900   # Discord limit is 2000 — leave headroom for splitting
RESPONSE_TIMEOUT   = 120    # seconds to wait for Leon response


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
            # Unknown user — only reply to reject if they DM'd or @mentioned directly
            if is_dm or is_mention:
                await message.reply("You're not authorised to use this bot.")
            return
        # Allowed user — respond to everything (no @ required)

        # Strip bot mention from message text
        text = message.content
        if self.user.mention in text:
            text = text.replace(self.user.mention, "").strip()
        if not text:
            return

        # Show typing indicator while waiting for Leon
        async with message.channel.typing():
            response = await self._ask_leon(text, str(message.author))

        # Split long responses so they fit Discord's 2000-char limit
        for chunk in _split_message(response):
            await message.reply(chunk)

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


def _split_message(text: str) -> list[str]:
    """Split a long message into Discord-safe chunks."""
    if len(text) <= MAX_DISCORD_LENGTH:
        return [text]
    chunks = []
    while text:
        if len(text) <= MAX_DISCORD_LENGTH:
            chunks.append(text)
            break
        # Try to split on newline boundary
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


def main():
    parser = argparse.ArgumentParser(description="Discord bridge for Leon AI")
    parser.add_argument("--token",        required=True, help="Discord bot token")
    parser.add_argument("--leon-url",     default="http://localhost:3000", help="Leon dashboard URL")
    parser.add_argument("--leon-token",   default="", help="Leon API bearer token")
    parser.add_argument("--config-dir",   default="config", help="Path to Leon config dir")
    parser.add_argument("--allowed-users", default="", help="Comma-separated Discord user IDs allowed to use bot")
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
