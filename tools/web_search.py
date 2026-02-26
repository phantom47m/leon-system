"""
Leon Web Search — Exa API wrapper with 1-hour local cache.

Policy:
  - Use web only when time-sensitive or not found locally.
  - Cache aggressively (1 hour TTL) to avoid redundant API calls.
  - Always return concise citation summary.
  - Falls back gracefully if Exa API key not configured.

Add key: echo "exa_api_key: YOUR_KEY" >> config/user_config.yaml
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("leon.web_search")

EXA_API_BASE    = "https://api.exa.ai"
CACHE_DIR       = Path("data/web_cache")
CACHE_TTL       = 3600   # 1 hour


# ── Config / auth ─────────────────────────────────────────────────────────────

def _get_exa_key() -> str:
    key = os.environ.get("EXA_API_KEY", "")
    if key:
        return key
    try:
        import yaml
        cfg = yaml.safe_load(
            (Path(__file__).parent.parent / "config" / "user_config.yaml").read_text()
        )
        return cfg.get("exa_api_key", "") or ""
    except Exception:
        return ""


# ── Cache ─────────────────────────────────────────────────────────────────────

def _ck(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()[:16]


def _cache_get(key: str) -> Optional[dict]:
    f = CACHE_DIR / f"{key}.json"
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text())
        if time.time() - data.get("cached_at", 0) < CACHE_TTL:
            return data
    except Exception:
        pass
    return None


def _cache_set(key: str, data: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data["cached_at"] = time.time()
    (CACHE_DIR / f"{key}.json").write_text(json.dumps(data, indent=2))


# ── Formatters ────────────────────────────────────────────────────────────────

def _format_results(results: list) -> str:
    if not results:
        return "No web results found."
    lines = []
    for i, r in enumerate(results, 1):
        title   = r.get("title", "Untitled")
        url     = r.get("url", "")
        snippet = (r.get("text") or r.get("snippet") or "")[:250].strip()
        lines.append(f"{i}. **{title}**")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append(f"   Source: {url}")
    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

async def search(query: str, num_results: int = 5) -> str:
    """
    Search Exa for `query`. Cache-first.
    Returns formatted citation string.
    """
    key = _get_exa_key()
    if not key:
        return (
            "Exa API key not configured. "
            "Add `exa_api_key: YOUR_KEY` to config/user_config.yaml "
            "or get a key at exa.ai"
        )

    ck = _ck(f"search:{query}:{num_results}")
    cached = _cache_get(ck)
    if cached:
        logger.debug(f"Web search cache hit: {query[:50]}")
        return _format_results(cached.get("results", []))

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{EXA_API_BASE}/search",
                headers={"x-api-key": key, "Content-Type": "application/json"},
                json={
                    "query": query,
                    "num_results": num_results,
                    "use_autoprompt": True,
                    "contents": {"text": {"max_characters": 500}},
                },
            )
        if r.status_code == 200:
            results = r.json().get("results", [])
            _cache_set(ck, {"results": results})
            return _format_results(results)
        elif r.status_code == 401:
            return "Exa API key invalid — check config/user_config.yaml"
        else:
            logger.warning(f"Exa search {r.status_code}: {r.text[:150]}")
            return f"Web search failed (HTTP {r.status_code})"

    except httpx.TimeoutException:
        return "Web search timed out (15s)."
    except Exception as e:
        logger.error(f"Exa search error: {e}")
        return f"Web search error: {e}"


async def fetch(url: str) -> str:
    """
    Fetch and extract text from a URL via Exa. Cache-first.
    Returns up to 2000 chars of extracted text.
    """
    key = _get_exa_key()
    if not key:
        return "Exa API key not configured."

    ck = _ck(f"fetch:{url}")
    cached = _cache_get(ck)
    if cached:
        return cached.get("text", "")[:2000]

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"{EXA_API_BASE}/contents",
                headers={"x-api-key": key, "Content-Type": "application/json"},
                json={"ids": [url], "text": {"max_characters": 2000}},
            )
        if r.status_code == 200:
            results = r.json().get("results", [{}])
            text = results[0].get("text", "") if results else ""
            _cache_set(ck, {"text": text})
            return text or "No content extracted."
        return f"Fetch failed (HTTP {r.status_code})"

    except Exception as e:
        logger.error(f"Exa fetch error: {e}")
        return f"Fetch error: {e}"


async def search_and_summarize(query: str, api_client=None) -> str:
    """
    Search + optionally summarize results with LLM.
    If api_client provided, passes results to Leon's LLM for a concise summary.
    """
    raw = await search(query)
    if not api_client or "not configured" in raw or "failed" in raw:
        return raw

    try:
        summary = await api_client.quick_request(
            f"Summarize these search results for '{query}' in 2-3 sentences:\n\n{raw}"
        )
        return f"{summary}\n\nSources:\n{raw}"
    except Exception:
        return raw
