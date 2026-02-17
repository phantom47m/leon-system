"""
Leon API Client - Anthropic API wrapper for conversational responses

Auth methods (priority order):
1. ANTHROPIC_API_KEY env var or vault → direct SDK
2. Claude CLI subscription → shells out to `claude --print`
3. No auth (will fail)
"""

import asyncio
import json
import logging
import shutil
import subprocess
from typing import Optional

logger = logging.getLogger("leon.api")


def _has_claude_cli() -> bool:
    """Check if claude CLI is installed and authenticated."""
    return shutil.which("claude") is not None


class AnthropicAPI:
    """Wrapper for Anthropic API calls used by Leon's brain."""

    def __init__(self, config: dict, vault=None):
        import os

        api_key = os.environ.get("ANTHROPIC_API_KEY")

        # Try loading from vault if env var is empty
        if not api_key and vault and vault._unlocked:
            api_key = vault.retrieve("ANTHROPIC_API_KEY")
            if api_key:
                os.environ["ANTHROPIC_API_KEY"] = api_key
                logger.info("API key loaded from vault")

        self.client = None
        self._auth_method = "none"

        if api_key:
            import anthropic
            self.client = anthropic.AsyncAnthropic(api_key=api_key)
            self._auth_method = "api_key"
        elif _has_claude_cli():
            self._auth_method = "claude_cli"
            logger.info("Using Claude CLI for API calls (subscription auth)")
        else:
            logger.warning("No API auth configured — use /setkey or install Claude CLI")

        self.model = config.get("model", "claude-sonnet-4-5-20250929")
        self.max_tokens = config.get("max_tokens", 8000)
        self.temperature = config.get("temperature", 0.7)
        logger.info(f"API client initialized - model: {self.model}, auth: {self._auth_method}")

    def set_api_key(self, key: str):
        """Update the API key at runtime."""
        import anthropic
        import os
        os.environ["ANTHROPIC_API_KEY"] = key
        self.client = anthropic.AsyncAnthropic(api_key=key)
        self._auth_method = "api_key"
        logger.info("API key updated at runtime")

    # ------------------------------------------------------------------
    # Claude CLI backend (for subscription users without API key)
    # ------------------------------------------------------------------

    async def _claude_cli_request(self, prompt: str) -> str:
        """Send a prompt through claude --print and return the response."""
        try:
            import os
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            proc = await asyncio.create_subprocess_exec(
                "claude", "--print", "-p", prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0 and stdout:
                return stdout.decode().strip()
            err = stderr.decode().strip() if stderr else f"exit code {proc.returncode}"
            logger.error(f"Claude CLI error: {err}")
            return f"Error: {err}"
        except asyncio.TimeoutError:
            logger.error("Claude CLI request timed out (120s)")
            return "Error: request timed out"
        except Exception as e:
            logger.error(f"Claude CLI error: {e}")
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_message(self, system: str, messages: list) -> str:
        """Full conversation-style request."""
        if self._auth_method == "api_key" and self.client:
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    system=system,
                    messages=messages,
                )
                return response.content[0].text
            except Exception as e:
                logger.error(f"API error: {e}")
                return f"API error: {e}"

        elif self._auth_method == "claude_cli":
            # Build a single prompt with system + conversation context
            parts = [f"System: {system}\n"]
            for m in messages[-10:]:  # last 10 messages for context
                role = m.get("role", "user").capitalize()
                parts.append(f"{role}: {m['content']}")
            combined = "\n\n".join(parts)
            return await self._claude_cli_request(combined)

        return "No API authentication configured. Use /setkey or log in to Claude CLI."

    async def quick_request(self, prompt: str) -> str:
        """Single-turn quick request."""
        if self._auth_method == "api_key" and self.client:
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                return response.content[0].text
            except Exception as e:
                logger.error(f"API error: {e}")
                return f"Error: {e}"

        elif self._auth_method == "claude_cli":
            return await self._claude_cli_request(prompt)

        return "No API authentication configured. Use /setkey or log in to Claude CLI."

    async def analyze_json(self, prompt: str) -> Optional[dict]:
        """Request that expects JSON back — parses it automatically."""
        raw = await self.quick_request(prompt)
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"Could not parse JSON from API response: {text[:200]}")
            return None
