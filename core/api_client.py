"""
Leon API Client - Multi-provider AI backend with automatic failover

Provider priority (first available wins):
1. ANTHROPIC_API_KEY env var / vault  → Anthropic SDK (paid)
2. GROQ_API_KEY env var / vault       → Groq free tier (llama-3.1-8b-instant)
3. Ollama running at localhost:11434  → Local free (llama3.2, mistral, etc.)
4. Claude CLI (`claude --print`)      → Subscription auth fallback

When the primary provider fails, requests automatically fall through to the
next available provider. This prevents user-visible errors when a single
provider has a transient outage, rate limit, or configuration issue.
"""

import asyncio
import json
import logging
import os
import re
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
    # JSON extraction (robust — handles messy LLM output)
    # ------------------------------------------------------------------

    # Regex to match ```json ... ``` or ``` ... ``` code blocks
    _CODE_FENCE_RE = re.compile(
        r"```(?:json|JSON)?\s*\n?(.*?)\n?\s*```",
        re.DOTALL,
    )

    # Trailing commas before } or ] (common LLM mistake)
    _TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

    @staticmethod
    def _find_json_substring(text: str) -> Optional[str]:
        """Find the outermost JSON object or array in *text* using bracket matching.

        Handles cases where the LLM wraps JSON in explanatory text, e.g.:
          "Here is the result: {\"type\": \"reply\"}"
        """
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            start = text.find(open_ch)
            if start == -1:
                continue
            depth = 0
            in_string = False
            escape = False
            for i in range(start, len(text)):
                ch = text[i]
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"' and not escape:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        return text[start:i + 1]
        return None

    def _extract_json(self, raw: str) -> Optional[dict]:
        """Try multiple strategies to extract valid JSON from LLM output.

        Strategy order (first success wins):
          1. Direct json.loads on stripped text
          2. Extract from markdown code fence (```json ... ```)
          3. Find JSON object/array substring via bracket matching
          4. Fix trailing commas and retry strategies 1-3
        """
        if not raw or self._is_provider_error(raw):
            return None

        text = raw.strip()

        # --- Strategy 1: direct parse ---
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # --- Strategy 2: extract from code fence ---
        fence_match = self._CODE_FENCE_RE.search(text)
        if fence_match:
            try:
                return json.loads(fence_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # --- Strategy 3: find JSON substring via bracket matching ---
        json_str = self._find_json_substring(text)
        if json_str:
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        # --- Strategy 4: fix trailing commas and retry ---
        fixed = self._TRAILING_COMMA_RE.sub(r"\1", text)
        if fixed != text:
            try:
                return json.loads(fixed)
            except json.JSONDecodeError:
                pass
            # Also try bracket matching on the fixed text
            json_str = self._find_json_substring(fixed)
            if json_str:
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    pass

        # Also try fixing commas inside a code fence
        if fence_match:
            fixed_fence = self._TRAILING_COMMA_RE.sub(r"\1", fence_match.group(1).strip())
            try:
                return json.loads(fixed_fence)
            except json.JSONDecodeError:
                pass

        logger.warning("Could not parse JSON from API response: %s", text[:200])
        return None

    # ------------------------------------------------------------------
    # Failover helpers
    # ------------------------------------------------------------------

    # Response prefixes that indicate a provider error (not a valid LLM reply)
    _ERROR_PREFIXES = (
        "API error:", "Groq error:", "Ollama error:", "Error:",
        "Groq API key is invalid", "One sec —", "Groq timed out",
        "Ollama isn't running", "Request timed out", "No AI provider",
    )

    def _is_provider_error(self, text: str) -> bool:
        """Return True if *text* looks like a provider error, not a real LLM response."""
        if not text:
            return True
        return any(text.startswith(p) for p in self._ERROR_PREFIXES)

    def _available_fallbacks(self) -> list[str]:
        """Return provider names available for fallback (excluding the current primary)."""
        candidates = []
        if self.client:
            candidates.append("api_key")
        if self._groq_key:
            candidates.append("groq")
        if self._ollama_model:
            candidates.append("ollama")
        if _has_claude_cli():
            candidates.append("claude_cli")
        return [p for p in candidates if p != self._auth_method]

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

    async def _claude_cli_request(self, prompt: str, model: str = "claude-sonnet-4-6") -> str:
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
            return f"Error: {err[:100]}"
        except asyncio.TimeoutError:
            logger.error("Claude CLI request timed out")
            return "Request timed out."
        except Exception as e:
            logger.error(f"Claude CLI error: {e}")
            return f"Error: {e}"

    # ------------------------------------------------------------------
    # Public API (provider-agnostic)
    # ------------------------------------------------------------------

    async def _create_message_with(self, provider: str, system: str, messages: list) -> str:
        """Try a single provider for create_message. Returns the response or an error string."""
        if provider == "api_key" and self.client:
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
        elif provider == "groq" and self._groq_key:
            return await self._groq_request(messages, system=system)
        elif provider == "ollama" and self._ollama_model:
            return await self._ollama_request(messages, system=system)
        elif provider == "claude_cli":
            parts = [f"System: {system}\n"]
            for m in messages[-10:]:
                role = m.get("role", "user").capitalize()
                parts.append(f"{role}: {m['content']}")
            return await self._claude_cli_request("\n\n".join(parts))
        return _no_provider_msg()

    async def create_message(self, system: str, messages: list) -> str:
        """Full conversation-style request. Routes to current provider with automatic failover."""
        result = await self._create_message_with(self._auth_method, system, messages)
        if not self._is_provider_error(result):
            return result

        # Primary failed — try fallback providers
        for fallback in self._available_fallbacks():
            logger.warning(f"Provider '{self._auth_method}' failed, trying fallback '{fallback}'")
            result = await self._create_message_with(fallback, system, messages)
            if not self._is_provider_error(result):
                logger.info(f"Failover to '{fallback}' succeeded")
                return result

        return result

    async def _quick_request_with(self, provider: str, prompt: str, image_b64: str = None) -> str:
        """Try a single provider for quick_request. Returns the response or an error string."""
        if provider == "api_key" and self.client:
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
        elif provider == "groq" and self._groq_key:
            if image_b64:
                logger.warning("Groq doesn't support image input — text only")
            return await self._groq_request([{"role": "user", "content": prompt}])
        elif provider == "ollama" and self._ollama_model:
            if image_b64:
                logger.warning("Ollama text-only (vision model needed for images)")
            return await self._ollama_request([{"role": "user", "content": prompt}])
        elif provider == "claude_cli":
            return await self._claude_cli_request(prompt)
        return _no_provider_msg()

    async def quick_request(self, prompt: str, image_b64: str = None) -> str:
        """Single-turn quick request with automatic failover. Image only supported with Anthropic."""
        result = await self._quick_request_with(self._auth_method, prompt, image_b64)
        if not self._is_provider_error(result):
            return result

        # Primary failed — try fallback providers (image_b64 dropped for non-Anthropic, already logged)
        for fallback in self._available_fallbacks():
            logger.warning(f"Provider '{self._auth_method}' failed, trying fallback '{fallback}'")
            result = await self._quick_request_with(fallback, prompt, image_b64)
            if not self._is_provider_error(result):
                logger.info(f"Failover to '{fallback}' succeeded")
                return result

        return result

    async def analyze_json(self, prompt: str, image_b64: str = None, smart: bool = False) -> Optional[dict]:
        """Request that expects JSON back — parses it automatically.

        Always uses Groq when available (< 1s vs 5s Claude CLI) — analysis/routing
        doesn't need Claude quality, just fast JSON classification.
        smart=True uses the larger 70b model.
        """
        if self._groq_key:
            # Groq is always faster for routing/analysis — use it regardless of primary auth
            raw = await self._groq_request(
                [{"role": "user", "content": prompt}],
                model=GROQ_AGENT_MODEL,  # 70b: reliable JSON output, still < 1s on Groq
            )
            # If Groq failed, fall through to quick_request (which has its own failover)
            if self._is_provider_error(raw):
                logger.warning("Groq failed for analyze_json, falling back to quick_request")
                raw = await self.quick_request(prompt, image_b64=image_b64)
        else:
            raw = await self.quick_request(prompt, image_b64=image_b64)

        return self._extract_json(raw)
