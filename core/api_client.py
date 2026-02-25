"""
Leon API Client - Multi-provider AI backend

Provider priority (first available wins):
1. ANTHROPIC_API_KEY env var / vault  → Anthropic SDK (paid)
2. GROQ_API_KEY env var / vault       → Groq free tier (llama-3.1-8b-instant)
3. Ollama running at localhost:11434  → Local free (llama3.2, mistral, etc.)
4. Claude CLI (`claude --print`)      → Subscription auth fallback
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("leon.api")

GROQ_API_BASE        = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL   = "llama-3.1-8b-instant"       # Fast: conversation, simple routing
GROQ_AGENT_MODEL     = "llama-3.3-70b-versatile"    # Smart: browser agent, complex reasoning
OLLAMA_API_BASE      = "http://localhost:11434"


def _no_provider_msg() -> str:
    return (
        "No AI provider configured.\n\n"
        "**Free options (pick one):**\n"
        "1. **Groq** — `/setkey groq <key>` → get free key at console.groq.com\n"
        "2. **Ollama** — install locally at ollama.ai, then `ollama pull llama3.2`\n\n"
        "**Paid option:**\n"
        "3. **Anthropic** — `/setkey <key>` → console.anthropic.com"
    )


def _has_claude_cli() -> bool:
    return shutil.which("claude") is not None


def _check_ollama() -> Optional[str]:
    """Check if Ollama is running and return the first available model name, or None."""
    try:
        r = httpx.get(f"{OLLAMA_API_BASE}/api/tags", timeout=2.0)
        if r.status_code == 200:
            models = r.json().get("models", [])
            if models:
                return models[0]["name"]
    except Exception:
        pass
    return None


class AnthropicAPI:
    """Multi-provider AI client. Falls back through providers automatically."""

    def __init__(self, config: dict, vault=None):
        self.model = config.get("model", "claude-sonnet-4-6")
        self.max_tokens = config.get("max_tokens", 8000)
        self.temperature = config.get("temperature", 0.7)

        self.client = None          # Anthropic async client
        self._auth_method = "none"
        self._groq_key: str = ""
        self._ollama_model: str = ""

        # --- 1. Anthropic API key ---
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key and vault and vault._unlocked:
            api_key = vault.retrieve("ANTHROPIC_API_KEY") or ""
            if api_key:
                os.environ["ANTHROPIC_API_KEY"] = api_key
                logger.info("Anthropic API key loaded from vault")

        if api_key and not api_key.startswith("sk-ant-oat"):
            # Valid API key (not an OAuth token which starts with sk-ant-oat)
            try:
                import anthropic
                self.client = anthropic.AsyncAnthropic(api_key=api_key)
                self._auth_method = "api_key"
                logger.info(f"API client: Anthropic SDK (model: {self.model})")
            except ImportError:
                logger.warning("anthropic package not installed — skipping Anthropic SDK")

        # --- 2. Claude CLI (subscription) — preferred over free Groq ---
        # Subprocess strips CLAUDECODE so it works even inside Claude Code.
        if self._auth_method == "none":
            if _has_claude_cli():
                self._auth_method = "claude_cli"
                logger.info("API client: Claude CLI (subscription auth)")

        # --- 3. Groq free tier (fallback if no Claude subscription) ---
        # Load the key regardless so it's available if needed later
        groq_key = os.environ.get("GROQ_API_KEY", "")
        if not groq_key and vault and vault._unlocked:
            groq_key = vault.retrieve("GROQ_API_KEY") or ""
            if groq_key:
                os.environ["GROQ_API_KEY"] = groq_key
        if groq_key:
            self._groq_key = groq_key
        if self._auth_method == "none" and groq_key:
            self._auth_method = "groq"
            logger.info(f"API client: Groq free tier ({GROQ_DEFAULT_MODEL})")

        # --- 4. Ollama (local, completely free) ---
        if self._auth_method == "none":
            ollama_model = _check_ollama()
            if ollama_model:
                self._ollama_model = ollama_model
                self._auth_method = "ollama"
                logger.info(f"API client: Ollama local ({ollama_model})")
                logger.warning(
                    "No AI provider configured. "
                    "Options:\n"
                    "  • Free: /setkey groq <key>  (get key at console.groq.com)\n"
                    "  • Free: install Ollama (ollama.ai)\n"
                    "  • Paid: /setkey <anthropic_key>"
                )

        logger.info(f"API auth method: {self._auth_method}")

    def set_api_key(self, key: str, provider: str = "anthropic"):
        """Update API key at runtime for the given provider."""
        provider = provider.lower()
        if provider == "groq":
            self._groq_key = key
            os.environ["GROQ_API_KEY"] = key
            self._auth_method = "groq"
            logger.info("Groq API key set at runtime")
        else:
            # Anthropic
            try:
                import anthropic
                self.client = anthropic.AsyncAnthropic(api_key=key)
                os.environ["ANTHROPIC_API_KEY"] = key
                self._auth_method = "api_key"
                logger.info("Anthropic API key updated at runtime")
            except ImportError:
                # Fallback: set env var only (used by spawned agents)
                os.environ["ANTHROPIC_API_KEY"] = key
                self._auth_method = "api_key"

    def get_provider_info(self) -> dict:
        """Return current provider info for status display."""
        providers = {
            "api_key": {"name": "Anthropic", "model": self.model, "cost": "paid"},
            "groq":    {"name": "Groq",      "model": GROQ_DEFAULT_MODEL, "cost": "free"},
            "ollama":  {"name": "Ollama",     "model": self._ollama_model, "cost": "free (local)"},
            "claude_cli": {"name": "Claude CLI", "model": "claude-3-5-sonnet", "cost": "subscription"},
            "none":    {"name": "None",       "model": "", "cost": "—"},
        }
        return providers.get(self._auth_method, providers["none"])

    # ------------------------------------------------------------------
    # Provider backends
    # ------------------------------------------------------------------

    async def _groq_request(self, messages: list, system: str = "", model: str = None) -> str:
        """Call Groq's OpenAI-compatible API (free tier)."""
        groq_messages = []
        if system:
            groq_messages.append({"role": "system", "content": system})
        # Groq has a small context limit — keep only the last 6 messages to avoid 413
        if len(messages) > 6:
            logger.debug(f"Groq: truncating conversation from {len(messages)} to 6 messages (context window limit)")
        groq_messages.extend(messages[-6:])

        payload = {
            "model": model or GROQ_DEFAULT_MODEL,
            "messages": groq_messages,
            "max_tokens": min(self.max_tokens, 8000),
            "temperature": self.temperature,
        }

        import asyncio as _asyncio
        retry_delays = [5, 15, 30]  # seconds to wait after each 429
        for attempt, delay in enumerate([0] + retry_delays):
            if delay:
                logger.info(f"Groq rate limit — retrying in {delay}s (attempt {attempt+1}/{len(retry_delays)+1})")
                await _asyncio.sleep(delay)
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    r = await client.post(
                        f"{GROQ_API_BASE}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self._groq_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    if r.status_code == 200:
                        return r.json()["choices"][0]["message"]["content"]
                    elif r.status_code == 401:
                        logger.error("Groq: invalid API key")
                        return "Groq API key is invalid. Run `/setkey groq <key>` to update it."
                    elif r.status_code == 429:
                        logger.warning(f"Groq: rate limited (attempt {attempt+1})")
                        if attempt < len(retry_delays):
                            continue  # retry after delay
                        return "One sec — Groq's rate limit is busy, try again shortly."
                    else:
                        err = r.text[:200]
                        logger.error(f"Groq error {r.status_code}: {err}")
                        return f"Groq error: {r.status_code}"
            except httpx.TimeoutException:
                logger.error("Groq request timed out")
                if attempt < len(retry_delays):
                    continue
                return "Groq timed out — try again."
            except Exception as e:
                logger.error(f"Groq request failed: {e}")
                return f"Groq error: {e}"

    async def _ollama_request(self, messages: list, system: str = "") -> str:
        """Call local Ollama instance (completely free)."""
        ollama_messages = []
        if system:
            ollama_messages.append({"role": "system", "content": system})
        ollama_messages.extend(messages)

        payload = {
            "model": self._ollama_model,
            "messages": ollama_messages,
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(
                    f"{OLLAMA_API_BASE}/api/chat",
                    json=payload,
                )
                if r.status_code == 200:
                    return r.json()["message"]["content"]
                else:
                    logger.error(f"Ollama error {r.status_code}: {r.text[:200]}")
                    return f"Ollama error: {r.status_code}"
        except httpx.ConnectError:
            logger.error("Ollama not reachable — is it running?")
            self._auth_method = "none"  # Mark as unavailable
            return "Ollama isn't running. Start it with `ollama serve`."
        except Exception as e:
            logger.error(f"Ollama request failed: {e}")
            return f"Ollama error: {e}"

    async def _claude_cli_request(self, prompt: str, model: str = "claude-haiku-4-5-20251001") -> str:
        """Send a prompt through claude --print using the subscription auth."""
        # Strip all Claude Code session env vars so the subprocess doesn't think it's nested
        _strip = {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
                  "CLAUDE_CODE_OTEL_EXPORTER", "CLAUDE_CODE_API_KEY_HELPER",
                  "PARENT_CLAUDE_SESSION", "CLAUDE_PARENT_SESSION"}
        env = {k: v for k, v in os.environ.items() if k not in _strip}
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "--print", "--model", model, "-p", prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
            if proc.returncode == 0 and stdout:
                return stdout.decode().strip()
            err = stderr.decode().strip() if stderr else f"exit code {proc.returncode}"
            logger.error(f"Claude CLI error: {err[:200]}")
            # Nested session / conflict — fall back to Groq for this request
            if any(k in err.lower() for k in ("nested", "cannot be launched", "already running")):
                logger.warning("Claude CLI conflict — falling back to Groq for this request")
                if self._groq_key:
                    return await self._groq_request([], prompt)
            return f"Error: {err[:100]}"
        except asyncio.TimeoutError:
            logger.error("Claude CLI request timed out")
            if self._groq_key:
                return await self._groq_request([], prompt)
            return "Request timed out."
        except Exception as e:
            logger.error(f"Claude CLI error: {e}")
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Public API (provider-agnostic)
    # ------------------------------------------------------------------

    async def create_message(self, system: str, messages: list) -> str:
        """Full conversation-style request. Routes to current provider."""
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
                logger.error(f"Anthropic API error: {e}")
                return f"API error: {e}"

        elif self._auth_method == "groq":
            return await self._groq_request(messages, system=system)

        elif self._auth_method == "ollama":
            return await self._ollama_request(messages, system=system)

        elif self._auth_method == "claude_cli":
            parts = [f"System: {system}\n"]
            for m in messages[-10:]:
                role = m.get("role", "user").capitalize()
                parts.append(f"{role}: {m['content']}")
            return await self._claude_cli_request("\n\n".join(parts))

        return _no_provider_msg()

    async def quick_request(self, prompt: str, image_b64: str = None) -> str:
        """Single-turn quick request. Image only supported with Anthropic."""
        if self._auth_method == "api_key" and self.client:
            try:
                if image_b64:
                    content = [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                        {"type": "text", "text": prompt},
                    ]
                else:
                    content = prompt
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[{"role": "user", "content": content}],
                )
                return response.content[0].text
            except Exception as e:
                logger.error(f"Anthropic API error: {e}")
                return f"Error: {e}"

        elif self._auth_method == "groq":
            if image_b64:
                logger.warning("Groq doesn't support image input — text only")
            return await self._groq_request([{"role": "user", "content": prompt}])

        elif self._auth_method == "ollama":
            if image_b64:
                logger.warning("Ollama text-only (vision model needed for images)")
            return await self._ollama_request([{"role": "user", "content": prompt}])

        elif self._auth_method == "claude_cli":
            return await self._claude_cli_request(prompt)

        return _no_provider_msg()

    async def analyze_json(self, prompt: str, image_b64: str = None, smart: bool = False) -> Optional[dict]:
        """Request that expects JSON back — parses it automatically.

        smart=True uses the larger, more capable model (for browser agent tasks).
        """
        if smart and self._auth_method == "groq":
            raw = await self._groq_request(
                [{"role": "user", "content": prompt}],
                model=GROQ_AGENT_MODEL,
            )
        else:
            raw = await self.quick_request(prompt, image_b64=image_b64)

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
