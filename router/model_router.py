"""
Leon Model Router — Routes tasks to the cheapest/fastest capable model.

Route rules:
  heartbeat / health check  → Ollama llama3.2:3b  ($0, local, <5s)
  trivial classification    → Ollama or Groq       ($0)
  code / refactor / build   → Claude (subscription, existing api_client)
  deep architecture         → Claude (same — Opus only if explicitly requested)

All routing decisions are logged to logs_structured/router.jsonl.
Never blocks; falls back gracefully if Ollama is unavailable.
"""

import json
import logging
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("leon.router")

OLLAMA_BASE = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2:3b"
STRUCTURED_LOG = Path("logs_structured/router.jsonl")

# ── Task classification signals ──────────────────────────────────────────────

_HEARTBEAT_SIGNALS = {
    "health", "heartbeat", "ping", "alive", "status check",
    "system check", "are you running", "uptime", "is running",
    "check if", "verify service",
}

_TRIVIAL_SIGNALS = {
    "format", "classify", "categorize", "sort", "rename",
    "summarize log", "count lines", "list files", "check syntax",
    "validate yaml", "validate json", "trim whitespace",
}

_CODE_SIGNALS = {
    "implement", "refactor", "fix bug", "add feature", "write test",
    "update", "build", "create component", "migrate", "optimize",
    "debug", "install", "configure", "deploy",
}


class TaskTier(str, Enum):
    HEARTBEAT = "heartbeat"   # Health checks → Ollama only ($0)
    TRIVIAL   = "trivial"     # Formatting/classification → Ollama/Groq ($0)
    STANDARD  = "standard"    # Code tasks → Claude (subscription)
    COMPLEX   = "complex"     # Architecture → Claude Sonnet (not auto-Opus)


def classify_task(description: str) -> TaskTier:
    """Classify a task description into a routing tier. O(n) string scan, no LLM."""
    d = description.lower()
    if any(sig in d for sig in _HEARTBEAT_SIGNALS):
        return TaskTier.HEARTBEAT
    if any(sig in d for sig in _TRIVIAL_SIGNALS):
        return TaskTier.TRIVIAL
    if any(sig in d for sig in _CODE_SIGNALS):
        return TaskTier.STANDARD
    # Default: standard (safe — won't route code tasks to local model)
    return TaskTier.STANDARD


# ── Structured logging ────────────────────────────────────────────────────────

def _log_decision(tier: TaskTier, model: str, reason: str, latency_ms: float,
                  task_preview: str = ""):
    STRUCTURED_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now().isoformat(),
        "event": "routing_decision",
        "tier": tier.value,
        "model": model,
        "reason": reason,
        "latency_ms": round(latency_ms, 1),
        "task": task_preview[:80],
    }
    try:
        with open(STRUCTURED_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# ── Ollama helpers ────────────────────────────────────────────────────────────

async def is_ollama_available(model: str = OLLAMA_MODEL) -> bool:
    """Non-blocking check: is Ollama running with the target model?"""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            if r.status_code != 200:
                return False
            models = [m["name"] for m in r.json().get("models", [])]
            # Accept "llama3.2:3b" or "llama3.2" (without tag)
            return any(model.split(":")[0] in m for m in models)
    except Exception:
        return False


async def ollama_call(prompt: str, model: str = OLLAMA_MODEL,
                      timeout: float = 30.0) -> Optional[str]:
    """
    Direct Ollama REST call. Returns response text or None on any error.
    Never raises — safe to call in background loops.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{OLLAMA_BASE}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
            )
            if r.status_code == 200:
                return r.json().get("response", "").strip()
            logger.debug(f"Ollama {r.status_code}: {r.text[:100]}")
            return None
    except httpx.ConnectError:
        logger.debug("Ollama not reachable")
        return None
    except Exception as e:
        logger.debug(f"Ollama call failed: {e}")
        return None


# ── Main routing function ─────────────────────────────────────────────────────

async def route(
    task_description: str,
    prompt: str,
    api_client=None,      # Leon's existing AnthropicAPI instance
    model: str = OLLAMA_MODEL,
) -> tuple[str, str]:
    """
    Route a prompt to the cheapest model that can handle it.

    Returns: (response_text, model_used)

    Rules:
      HEARTBEAT → Ollama only, never paid API
      TRIVIAL   → Ollama first, fall back to api_client if unavailable
      STANDARD  → api_client (Claude/Groq/Ollama per existing config)
    """
    t0 = time.monotonic()
    tier = classify_task(task_description)

    # ── Heartbeat: Ollama only, never paid API ────────────────────────────────
    if tier == TaskTier.HEARTBEAT:
        result = await ollama_call(prompt, model=model)
        if result:
            ms = (time.monotonic() - t0) * 1000
            _log_decision(tier, model, "heartbeat → local model (free)", ms, task_description)
            return result, model
        # Ollama down → canned response, still $0
        ms = (time.monotonic() - t0) * 1000
        _log_decision(tier, "canned", "ollama unavailable, canned heartbeat", ms, task_description)
        return "System operational. (Ollama offline — install with upgrade script)", "canned"

    # ── Trivial: try Ollama first ─────────────────────────────────────────────
    if tier == TaskTier.TRIVIAL:
        result = await ollama_call(prompt, model=model, timeout=20.0)
        if result:
            ms = (time.monotonic() - t0) * 1000
            _log_decision(tier, model, "trivial → local model (free)", ms, task_description)
            return result, model
        # Fall through to paid provider below

    # ── Standard/Complex (or Ollama unavailable for trivial) → api_client ─────
    if api_client:
        response = await api_client.quick_request(prompt)
        used_model = getattr(api_client, "model", None) or api_client._auth_method
        ms = (time.monotonic() - t0) * 1000
        _log_decision(tier, used_model, f"tier={tier.value} → configured provider", ms, task_description)
        return response, used_model

    ms = (time.monotonic() - t0) * 1000
    _log_decision(tier, "none", "no provider available", ms, task_description)
    return "No AI provider available.", "none"


# ── Batch health check (for scheduler) ───────────────────────────────────────

async def run_health_checks(checks: dict[str, str]) -> dict[str, str]:
    """
    Run a set of named health check prompts against Ollama ($0).
    Used by the autonomous scheduler for periodic system assessment.

    Args:
        checks: {check_name: prompt_text}

    Returns:
        {check_name: response_text}
    """
    results = {}
    for name, prompt in checks.items():
        short_prompt = f"Answer in one sentence only, no preamble. {prompt}"
        response = await ollama_call(short_prompt, timeout=20.0)
        results[name] = response or "Ollama unavailable — check `ollama serve`"
        logger.debug(f"Health check '{name}': {(response or '')[:60]}")
    return results


# ── Token usage estimation ────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def routing_stats(log_path: Path = STRUCTURED_LOG) -> dict:
    """Read router log and return routing distribution stats."""
    if not log_path.exists():
        return {}
    stats: dict[str, int] = {}
    try:
        for line in log_path.read_text().split("\n"):
            if not line:
                continue
            try:
                e = json.loads(line)
                model = e.get("model", "unknown")
                stats[model] = stats.get(model, 0) + 1
            except Exception:
                pass
    except Exception:
        pass
    return stats
