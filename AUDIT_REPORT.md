# Leon AI System — Self-Improvement Audit Report

**Date:** 2026-02-25
**Auditor:** Leon v1.0 (Self-Audit)
**Codebase:** Python / aiohttp / JavaScript / Node.js / Three.js

---

## Executive Summary

The Leon system is a well-architected AI orchestration platform with solid fundamentals: multi-provider API fallback, encrypted vault for secrets, hash-chained audit logging, task persistence across restarts, and clean separation of concerns. However, the audit identified **23 issues** across security, reliability, performance, and code quality, including **5 critical** items that should be addressed immediately.

---

## Critical Issues (Priority 1 — Fix Immediately)

### 1. CRITICAL: `shell_exec` uses `shell=True` — Command Injection Vector
**File:** `core/system_skills.py:1301`
**Risk:** HIGH — An AI-classified user message is passed to `subprocess.run(..., shell=True)`. The blocklist at line 1287 is trivially bypassable (e.g., `$(rm -rf /)`, backtick injection, pipes to `curl | bash`, `; malicious_command`, etc.).

**Current code:**
```python
result = subprocess.run(command, shell=True, ...)
```

**Recommended fix:** Replace with `shlex.split()` + `shell=False`, or use a proper sandboxing approach:
```python
import shlex
result = subprocess.run(shlex.split(command), shell=False, ...)
```
Also expand the blocklist to cover shell metacharacters: `;`, `|`, `$()`, backticks, `&&`, `||`, `>`, `>>`, `<`.

### 2. CRITICAL: Phone Number Hardcoded in Version-Controlled Config
**File:** `config/settings.yaml:58`
**Risk:** HIGH — A real phone number (`17275427167`) is committed to git in `whatsapp.allowed_numbers`.

**Fix:** Move to environment variable or vault:
```yaml
whatsapp:
  allowed_numbers: []  # Set via LEON_WHATSAPP_ALLOWED env var
```

### 3. CRITICAL: API Token Logged to stdout and log files
**File:** `dashboard/server.py:1230-1231`
**Risk:** HIGH — The API bearer token is printed to stdout and logged at INFO level. Anyone with access to `logs/leon_system.log` or `logs/leon_startup.log` can extract it.

**Fix:** Remove the print and log statements, or mask the token:
```python
logger.info(f"API token: ...{api_token[-6:]}")
```

### 4. CRITICAL: `python_exec` Runs Arbitrary Code from AI Classification
**File:** `core/system_skills.py:1439` (referenced in routing)
**Risk:** HIGH — The `python_exec` skill runs arbitrary Python code based on AI classification of user input. A malicious or confused classification could execute destructive code. There is no sandbox.

**Fix:** Run `python_exec` in a restricted subprocess with `--jail` or use `RestrictedPython`. At minimum, add a user confirmation step before executing.

### 5. CRITICAL: `--dangerously-skip-permissions` Used for All Agent Spawns
**File:** `core/agent_manager.py:80`
**Risk:** HIGH — Every spawned Claude Code agent runs with full permissions skipped. A malformed task brief could cause an agent to delete files, push code, or make network requests without any guardrails.

**Fix:** Remove `--dangerously-skip-permissions` and use a proper permissions configuration file for the agent. Alternatively, use `--allowedTools` to restrict to specific tools per task type.

---

## High Priority Issues (Priority 2 — Fix Soon)

### 6. Conversation History Unbounded per Save Cycle
**File:** `core/memory.py:78-81`
**Risk:** MEDIUM — `add_conversation` calls `self.save()` on every single message (line 81). With high message frequency, this writes the full memory JSON to disk on every message. The save includes an atomic write (tmp -> rename), but it's still I/O-heavy.

**Fix:** Debounce saves — save at most once every 5 seconds, or batch saves in the event loop:
```python
self._dirty = True
# In a background task: if self._dirty: self.save(); self._dirty = False
```

### 7. Event Loop Threading Anti-pattern in `main.py`
**File:** `main.py:61-104`
**Risk:** MEDIUM — `run_cli()` creates a new event loop, starts Leon, then creates additional event loops in threads for dashboard and voice. This means `leon.process_user_input()` runs in the main loop, but voice commands call it from a *different* loop/thread. The `Leon` class is not thread-safe — `self.memory`, `self.task_queue`, and other shared state are accessed without locks.

**Fix:** Use a single event loop with `asyncio.run()` and run the CLI input in a thread instead. All components should share the same loop:
```python
async def main():
    leon = Leon(...)
    await leon.start()
    # Run dashboard and voice as tasks on the same loop
    # Use loop.run_in_executor() for blocking CLI input
```

### 8. WhatsApp Watchdog Sends Zero-Width Space Messages
**File:** `core/leon.py:521-522`
**Risk:** MEDIUM — The WhatsApp watchdog sends a zero-width space (`\u200b`) to the user's own number every 60 seconds as a health probe. This pollutes the chat with invisible messages and could trigger WhatsApp spam detection over time.

**Fix:** Use the `/health` endpoint on port 3001 as the primary health check, and only fall back to message-based probing on explicit failure. Remove the zero-width space probe entirely.

### 9. No Graceful Shutdown for `run_cli` Mode
**File:** `main.py:160-164`
**Risk:** MEDIUM — In CLI mode, `KeyboardInterrupt` triggers `leon.stop()`, but the dashboard and voice threads (started as daemon threads) are abruptly killed without cleanup. The voice system may leave audio streams open, and the dashboard may not flush its state.

**Fix:** Signal the daemon threads to stop gracefully before exiting, or use `asyncio.Event` for coordinated shutdown.

### 10. Uncapped `completed` List in TaskQueue
**File:** `core/task_queue.py:76`
**Risk:** LOW-MEDIUM — The completed task list is capped at 200 during save, but not during runtime. A burst of task completions between saves could accumulate unbounded entries in memory.

**Fix:** Also cap during `complete_task()`:
```python
self.completed = self.completed[-200:]
```

### 11. BridgeServer Listens on `0.0.0.0` by Default
**File:** `core/neural_bridge.py:68`
**Risk:** MEDIUM — The bridge server defaults to listening on all interfaces (`0.0.0.0:9100`). This exposes the WebSocket bridge to the LAN/internet. The config overrides this to `127.0.0.1` in `settings.yaml`, but the default is dangerous.

**Fix:** Change the default to `127.0.0.1`:
```python
self.host = config.get("host", "127.0.0.1")
```

---

## Medium Priority Issues (Priority 3 — Address When Convenient)

### 12. SSL Certificate Verification Disabled for Bridge Client
**File:** `core/neural_bridge.py:347-348`
**Risk:** MEDIUM — When no cert path is provided, the bridge client disables all SSL verification (`CERT_NONE`). This allows MITM attacks on the Left-Right Brain connection.

**Fix:** Generate and distribute a self-signed cert at setup time rather than disabling verification.

### 13. Race Condition in NightMode Dispatch
**File:** `core/night_mode.py:191-207`
**Risk:** LOW — `_try_dispatch()` reads `active_agents` count and calculates capacity, but between the check and the actual spawn, another task could complete or start, causing over- or under-dispatching. Not a data corruption risk, but could exceed the concurrency limit.

**Fix:** Use an asyncio lock around the dispatch logic.

### 14. Duplicate Dashboard Startup Code
**File:** `main.py:67-78` and `main.py:207-218`
**Risk:** LOW — The dashboard startup code is duplicated between `run_cli()` and `run_gui()`. Any fix to one needs to be manually applied to the other.

**Fix:** Extract to a shared `start_dashboard(leon)` function.

### 15. Memory System Doesn't Trim `completed_tasks`
**File:** `core/memory.py:169-191`
**Risk:** LOW — `completed_tasks` grows without bound in the memory JSON file. Over months of operation, this could make the file very large and slow to load.

**Fix:** Add a trim in `complete_task()`:
```python
self.memory["completed_tasks"] = self.memory["completed_tasks"][-500:]
```

### 16. `_strip_sir` Called on Every Response
**File:** `core/leon.py:585`
**Risk:** NEGLIGIBLE — Every response runs through `_strip_sir()` as a hard filter. This is a regex operation on every response, which is fine but indicates the system prompt isn't fully effective at preventing "sir" usage.

**Observation:** This is working as intended — belt-and-suspenders approach. No change needed.

### 17. Groq Context Window Limitation Not Communicated
**File:** `core/api_client.py:173`
**Risk:** LOW — When using Groq, conversation history is silently truncated to the last 6 messages. The user has no indication that context was lost.

**Fix:** Log a debug message when truncation happens, or inform the user when they switch to Groq that context window is limited.

### 18. Dashboard Rate Limiter Uses Module-Level Mutable Dict
**File:** `dashboard/server.py:30`
**Risk:** LOW — `_rate_limit_buckets` is a module-level `defaultdict` that grows without bound. Old timestamps are cleaned per-request but IPs never get removed entirely.

**Fix:** Add periodic cleanup or use a TTL cache like `cachetools.TTLCache`.

---

## Code Quality & Architecture Improvements (Priority 4)

### 19. `leon.py` is 1400+ Lines — Too Many Responsibilities
**Observation:** The main `Leon` class handles request routing, special commands, browser agent orchestration, system skill routing, task brief creation, memory management, bridge communication, WhatsApp management, and more. This makes it hard to maintain and test.

**Recommendation:** Extract into focused modules:
- `core/router.py` — request analysis and routing
- `core/browser_agent.py` — browser automation logic
- `core/whatsapp_manager.py` — WhatsApp bridge lifecycle

### 20. System Skill Routing Prompt is ~1000 Tokens Per Request
**File:** `core/leon.py:936-999`
**Impact:** Every system skill request sends a 1000+ token prompt to the AI for classification. This costs money/tokens on every "open youtube" or "volume up" command.

**Recommendation:** Add a fast keyword-based pre-router for unambiguous commands (volume, brightness, screenshot, etc.) and only fall through to AI classification for ambiguous ones.

### 21. No Test Coverage for Security-Critical Paths
**File:** `tests/test_all.py`
**Observation:** The test suite covers memory, task queue, scheduler, and other modules well, but has no tests for:
- `shell_exec` blocklist bypass attempts
- API token authentication in dashboard
- WebSocket auth flow
- Vault encryption/decryption edge cases
- Permission system edge cases

**Recommendation:** Add security-focused tests.

### 22. Stale `stt_provider` in Config
**File:** `config/settings.yaml:63`
**Observation:** Config says `stt_provider: "deepgram"` but the voice system docstring and code show it uses Groq Whisper as the primary STT provider. The config value appears unused.

**Fix:** Update config to match reality or wire the config value into the voice system.

### 23. `sentByBridge` Set in WhatsApp Bridge Grows Without Bound
**File:** `integrations/whatsapp/bridge.js:54`
**Risk:** LOW — `sentByBridge` is a `Set` that tracks message IDs to prevent echo loops. IDs are deleted after use, but if `message_create` events arrive out of order, IDs accumulate.

**Fix:** Implement a TTL-based eviction (e.g., delete entries older than 5 minutes).

---

## Positive Observations

The codebase demonstrates several strong engineering practices:

1. **Atomic writes everywhere** — `task_queue.py`, `memory.py`, `night_mode.py`, and `scheduler.py` all use tmp-file-then-rename for crash safety.
2. **Multi-provider API fallback** — Anthropic -> Groq -> Ollama -> Claude CLI is well-implemented.
3. **Hash-chained audit log** — Tamper-evident logging in `security/vault.py`.
4. **Auto-retry with backoff** — Agent manager retries failed agents automatically.
5. **Task persistence** — Interrupted tasks are recovered on restart.
6. **File handle tracking** — Agent manager properly tracks and closes stdout/stderr handles.
7. **Credential auto-refresh** — Agent spawning auto-copies fresh credentials.
8. **Clean process lifecycle** — WhatsApp bridge has proper watchdog, reconnection, and graceful shutdown.
9. **Smart screen awareness** — Hash-based deduplication and adaptive polling interval.
10. **Comprehensive personality system** — Well-structured YAML with error translation maps.

---

## Recommended Action Plan

### Phase 1 — Immediate (This Week)
1. Fix `shell_exec` command injection (Issue #1)
2. Remove hardcoded phone number from settings.yaml (Issue #2)
3. Stop logging API token (Issue #3)
4. Add sandbox/confirmation for `python_exec` (Issue #4)
5. Evaluate removing `--dangerously-skip-permissions` (Issue #5)

### Phase 2 — Short Term (Next 2 Weeks)
6. Fix event loop threading model (Issue #7)
7. Remove WhatsApp zero-width space probe (Issue #8)
8. Change bridge server default to `127.0.0.1` (Issue #11)
9. Fix SSL verification for bridge (Issue #12)
10. Add security tests (Issue #21)

### Phase 3 — Medium Term (Next Month)
11. Debounce memory saves (Issue #6)
12. Add graceful shutdown coordination (Issue #9)
13. Cap completed_tasks in memory (Issue #15)
14. Add keyword pre-router for system skills (Issue #20)
15. Refactor `leon.py` into smaller modules (Issue #19)

---

*End of audit report.*
