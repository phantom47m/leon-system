"""
BrowserMixin — extracted from core/leon.py to keep that file manageable.

Contains: _web_search, _execute_browser_agent
All self.* references resolve through Leon's MRO at runtime.
"""

import asyncio
import logging

logger = logging.getLogger("leon")


class BrowserMixin:
    """Browser automation and web search methods."""

    async def _web_search(self, query: str) -> str:
        """
        Real web search via DuckDuckGo HTML — no API key, no browser, no Agent Zero.
        Falls back to Exa API if configured.
        """
        import re as _re
        import urllib.request, urllib.parse, html as _html

        # 1. Try Exa API if key configured
        try:
            from tools.web_search import search_and_summarize
            result = await search_and_summarize(query, api_client=self.api)
            if result and "not configured" not in result and "failed" not in result:
                logger.info("Web search: Exa hit for '%s'", query[:50])
                return result
        except Exception:
            pass

        # 2. DuckDuckGo HTML search — no auth, no rate limits for reasonable use
        logger.info("Web search: DDG HTML for '%s'", query[:60])
        try:
            q = urllib.parse.quote_plus(query)
            url = f"https://html.duckduckgo.com/html/?q={q}"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
                "Accept-Language": "en-US,en;q=0.9",
            })
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="ignore"),
            )

            # Extract result snippets with simple regex (no external parser needed)
            results = []
            # DDG HTML: <a class="result__a" href="...">Title</a> ... <a class="result__snippet">Snippet</a>
            titles   = _re.findall(r'class="result__a"[^>]*>([^<]{3,120})<', raw)
            snippets = _re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', raw, _re.DOTALL)
            urls_raw = _re.findall(r'uddg=([^&"]+)', raw)
            urls = [urllib.parse.unquote(u) for u in urls_raw if u.startswith("http")]

            for i, (title, snip) in enumerate(zip(titles, snippets)):
                title = _html.unescape(_re.sub(r'<[^>]+>', '', title)).strip()
                snip  = _html.unescape(_re.sub(r'<[^>]+>', '', snip)).strip()
                src   = urls[i] if i < len(urls) else ""
                if title and snip:
                    results.append(f"**{title}**\n{snip[:200]}\n{src}")
                if len(results) >= 4:
                    break

            if not results:
                return f"No results found for '{query}'."

            raw_results = "\n\n".join(results)

            # 3. Summarize with LLM so Leon gives a real answer, not a list of links
            try:
                summary = await self.api.quick_request(
                    f"The user asked: \"{query}\"\n\n"
                    f"Here are web search results:\n\n{raw_results}\n\n"
                    f"Give a concise 2-3 sentence answer in Leon's voice. "
                    f"If results don't contain the answer, say so directly. "
                    f"Do NOT say 'according to search results' or similar — just answer."
                )
                return summary
            except Exception:
                return raw_results  # fallback: raw results if LLM unavailable

        except Exception as e:
            logger.warning("DDG search failed: %s", e)
            return f"Search didn't work — {e}. Try asking me to open a browser instead."

    async def _execute_browser_agent(self, goal: str, start_url: str = None, max_steps: int = 8) -> str:
        """
        Agentic browser loop — reads the page, decides next action, executes, repeats.
        Gives Leon the full power of OpenClaw's browser control.
        """
        browser = self.openclaw.browser
        history = []

        logger.info(f"Browser agent: goal='{goal}' start_url={start_url}")

        # Ensure browser is running (sessions persist in ~/.openclaw/browser/openclaw/user-data/)
        browser.ensure_running()
        await asyncio.sleep(1.0)

        # Navigate to starting URL — reuse existing tab (navigate) instead of
        # open_url (which spawns a new 300MB renderer tab every time)
        if start_url:
            browser.navigate(start_url)
            logger.info(f"Browser agent: navigated to {start_url}")
            await asyncio.sleep(2.5)  # Let page load

        for step in range(max_steps):
            # Get current page state
            snapshot = browser.snapshot()
            if not snapshot:
                return "Browser isn't responding — is it running?"

            # Truncate snapshot to avoid token overflow
            snap_truncated = snapshot[:4000] if len(snapshot) > 4000 else snapshot

            history_summary = "\n".join(
                f"Step {h['step']}: {h['action']} — {h['reason']}"
                for h in history[-5:]  # Last 5 steps
            )

            prompt = f"""You are controlling a browser to accomplish this goal: "{goal}"

Current page (accessibility tree — element refs are numbers like [1], [23], etc.):
{snap_truncated}

Steps taken so far:
{history_summary or "None yet"}

What is the SINGLE best next action? Respond with ONLY valid JSON (no markdown):
{{
  "action": "click" | "type" | "press" | "navigate" | "scroll" | "fill" | "select" | "evaluate" | "wait" | "download" | "dialog" | "done",
  "ref": "element ref number — required for click/type/select/download/fill",
  "text": "text to type — required for type",
  "key": "key name — required for press (Enter, Tab, Escape, ArrowDown, etc.)",
  "url": "full URL — required for navigate; also used for wait",
  "fields": [{{"ref": "12", "value": "text"}}, ...],
  "values": ["option1", "option2"],
  "fn": "JS function string e.g. '() => document.title' — for evaluate",
  "wait_text": "text to wait for — for wait action",
  "wait_load": "load|domcontentloaded|networkidle — for wait action",
  "download_path": "/tmp/openclaw/downloads/filename.ext",
  "dialog_accept": true,
  "reason": "one sentence explaining this action",
  "done": true | false
}}

Action guide:
- click: click a button, link, or element
- type: type text into an input (use fill for multiple fields at once)
- fill: fill several form fields at once (more efficient than click+type per field)
- select: pick an option from a <select> dropdown
- press: keyboard shortcut (Enter to submit, Tab to advance, Escape to close)
- navigate: go to a new URL
- scroll: scroll down the page
- evaluate: run JS to read data from the page (e.g. get text that's not in snapshot)
- wait: wait for text/URL/load before next action (use after navigation)
- download: click a download link and save the file
- dialog: accept or dismiss a browser popup/alert
- done: goal fully accomplished

Rules:
- Use element refs from the page snapshot (numbers in brackets like [42])
- "done" = true only when the goal is fully accomplished
- For search boxes: type the search text, then press Enter
- After clicking links or buttons that load new pages, prefer wait action next
- If stuck after 3+ steps, try a different approach
- AUTH RULE: The browser is pre-authenticated with saved sessions for GitHub, Google, Railway, Discord, Reddit and more. NEVER enter passwords or credentials. If you see a login page, navigate directly to the dashboard/home URL instead (e.g. https://github.com, https://railway.app/dashboard).
- AUTH RULE: If a page asks to log in, assume you ARE logged in and navigate to the main app URL — the session cookie will kick in automatically.
- MEDIA RULE: For goals involving playing a song/video/audio — mark done=true immediately after clicking the video/song. Do NOT keep looping to verify it's playing.
- MEDIA RULE: If a video/song is already playing on the current page, immediately return done=true.
- Do NOT navigate away from a page if the goal has already been accomplished on that page."""

            result = await self.api.analyze_json(prompt, smart=True)
            if not result:
                logger.warning("Browser agent: AI returned no result")
                break

            action = result.get("action", "done")
            reason = result.get("reason", "")
            is_done = result.get("done", False)

            history.append({"step": step + 1, "action": action, "reason": reason})
            logger.info(f"Browser agent step {step+1}: {action} — {reason}")

            if action == "done" or is_done:
                # Return the reason if it's a natural sentence, otherwise generic done
                _action_words = ("click", "type", "navigate", "fill", "scroll",
                                 "press", "select", "wait", "step ")
                if reason and not any(reason.lower().startswith(w) for w in _action_words):
                    return reason
                return "Done."

            elif action == "click":
                ref = str(result.get("ref", ""))
                if ref:
                    browser.click(ref)
                    await asyncio.sleep(1.5)

            elif action == "type":
                ref = str(result.get("ref", ""))
                text = result.get("text", "")
                if ref and text:
                    browser.type_text(ref, text)
                    await asyncio.sleep(0.5)

            elif action == "press":
                key = result.get("key", "Enter")
                browser.press(key)
                await asyncio.sleep(1.5)

            elif action == "navigate":
                url = result.get("url", "")
                if url:
                    browser.navigate(url)
                    await asyncio.sleep(2.5)

            elif action == "scroll":
                browser.press("PageDown")
                await asyncio.sleep(0.8)

            elif action == "fill":
                fields = result.get("fields", [])
                if fields:
                    browser.fill(fields)
                    await asyncio.sleep(0.5)

            elif action == "select":
                ref = str(result.get("ref", ""))
                values = result.get("values", [])
                if ref and values:
                    browser.select(ref, *values)
                    await asyncio.sleep(0.5)

            elif action == "evaluate":
                fn = result.get("fn", "() => document.title")
                ref = result.get("ref")
                eval_result = browser.evaluate(fn, ref=ref)
                # Inject result into next step's history so AI can use it
                history[-1]["reason"] += f" | result: {eval_result[:200]}"
                await asyncio.sleep(0.3)

            elif action == "wait":
                wait_text = result.get("wait_text")
                wait_url = result.get("url")
                wait_load = result.get("wait_load")
                browser.wait(text=wait_text, url=wait_url, load=wait_load or ("load" if not wait_text and not wait_url else None))
                await asyncio.sleep(0.5)

            elif action == "download":
                ref = str(result.get("ref", ""))
                path = result.get("download_path", "/tmp/openclaw/downloads/download")
                if ref:
                    saved = browser.download(ref, path)
                    history[-1]["reason"] += f" | saved: {saved}"
                await asyncio.sleep(1.0)

            elif action == "dialog":
                accept = result.get("dialog_accept", True)
                browser.dialog(accept=accept)
                await asyncio.sleep(0.5)

        return "Done."
