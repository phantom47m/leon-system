"""
Leon API Client - Anthropic API wrapper for conversational responses
"""

import logging
import json
from typing import Optional

logger = logging.getLogger("leon.api")


class AnthropicAPI:
    """Wrapper for Anthropic API calls used by Leon's brain"""

    def __init__(self, config: dict):
        import anthropic
        import os

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            self.client = anthropic.AsyncAnthropic(api_key=api_key)
        else:
            self.client = anthropic.AsyncAnthropic()
            logger.warning("ANTHROPIC_API_KEY not set — API calls will fail until configured")
        self.model = config.get("model", "claude-sonnet-4-5-20250929")
        self.max_tokens = config.get("max_tokens", 8000)
        self.temperature = config.get("temperature", 0.7)
        logger.info(f"API client initialized - model: {self.model}")

    async def create_message(self, system: str, messages: list) -> str:
        """Full conversation-style request"""
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

    async def quick_request(self, prompt: str) -> str:
        """Single-turn quick request (task analysis, brief generation, etc.)"""
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

    async def analyze_json(self, prompt: str) -> Optional[dict]:
        """Request that expects JSON back - parses it automatically"""
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
