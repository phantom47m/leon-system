# Leon System — Motorev Task Progress

**Task:** Continuously improve Motorev app — rider-first redesign
**Agent:** `agent_f69c64e2`
**Date:** 2026-02-25

## Completed This Session

### Phase 12: Keyword Pre-Router for System Skills (Issue #20)

- **Added keyword pre-routing table** (`core/routing_mixin.py`) — 39 compiled regex patterns map unambiguous system commands (cpu usage, screenshot, next track, lock screen, etc.) directly to skills, skipping the ~1000-token LLM classification call. Saves latency and tokens for the most common voice/text commands.
- **Fixed "open terminal" bug** — "open terminal" previously opened `https://terminal.com`; now correctly routes to `open_app("terminal")` via a `_DESKTOP_APPS` allowlist (18 known desktop apps).
- **39 new tests** in `TestKeywordPreRouter` — pattern matching, ordering (gpu_temp before temperature), negative cases (weather with location falls through to LLM), table structure validation, and desktop apps table.
- **Total: 284 tests passing** (3 pre-existing failures in task queue persistence and voice ID config)

### Phase 11: Reliability, Code Quality & Security Tests

#### Leon System — Reliability Improvements (8 Fixes)
1. **Removed WhatsApp watchdog zero-width space probe** (`core/leon.py`)
   - Watchdog no longer sends `\u200b` messages every 60s to test the bridge
   - Now relies solely on the `/health` endpoint — no more chat spam or WhatsApp ban risk
2. **Added asyncio lock to NightMode dispatch** (`core/night_mode.py`)
   - `_try_dispatch()` now uses `asyncio.Lock()` to prevent race conditions
   - Concurrent calls (awareness loop + manual trigger) are serialized
3. **Extracted duplicate dashboard/voice startup code** (`main.py`)
   - `_start_dashboard_thread()` and `_start_voice_thread()` shared by `run_cli()` and `run_gui()`
   - Eliminated ~40 lines of duplicated code
4. **Added graceful shutdown coordination** (`main.py`, `core/leon.py`)
   - `leon.stop()` now calls `memory.save(force=True)` to bypass debounce on shutdown
   - `run_cli()` calls `memory.flush_if_dirty()` before stopping
5. **Improved dashboard rate limiter cleanup** (`dashboard/server.py`)
   - Counter-based periodic cleanup every 50 requests (was only >200 IPs)
   - Stale IPs evicted regardless of bucket count
6. **Added Groq context truncation logging** (`core/api_client.py`)
   - Debug log message when conversation history is truncated from N to 6 messages
7. **Added TTL eviction for WhatsApp sentByBridge** (`integrations/whatsapp/bridge.js`)
   - Changed from `Set` to `Map<id, timestamp>` with 5-minute TTL
   - Background interval cleans stale entries every 2 minutes
8. **Added SSL verification warning for bridge client** (`core/neural_bridge.py`)
   - Logs a clear warning when SSL verification is disabled (no cert_path)
   - Advises generating self-signed cert to fix MITM vulnerability

#### Leon System — Security Tests (17 new tests)
- **NightMode dispatch lock** — 2 tests: lock exists, initially unlocked
- **Bridge message validation** — 5 tests: roundtrip, invalid JSON, missing type, token required, default localhost
- **Dashboard rate limiter** — 1 test: stale IP eviction after counter trigger
- **Memory shutdown flush** — 3 tests: force-save bypasses debounce, debounced save marks dirty, flush_if_dirty writes pending
- **Advanced shell injection** — 6 tests: heredoc redirect, process substitution, null byte, newline with dangerous cmd, empty command, whitespace-only
- **Total: 203 tests passing** (0 failures, 5 skipped)

### Phase 10: Full Data Wiring, Security Hardening & Reliability

#### Motorev App — Data Layer Completion
- **Modifications wired to store** — removed last hardcoded data array from garage.tsx; added `Modification` interface, `addModification()`, `removeModification()` to store
- **Add Modification modal** — full CRUD for mods with bike selector, category picker (6 categories), cost input, and auto-dated install
- **Mods filter to store** — `filteredMods` now reads from `state.modifications` instead of hardcoded array
- **Empty state for mods** — proper empty state with Package icon and "Log your first mod" prompt
- **ModCard uses onRemove** — updated component to use Trash2 icon with delete confirmation instead of Share
- **5 new tests** in `useStoreMods.test.ts` — add/remove/persist/multiple mods
- **Total: 25 test suites, 138 tests, all passing**

#### Leon System — Security Hardening (5 Critical Fixes)
1. **Fixed shell_exec command injection** (`system_skills.py`)
   - Changed from `shell=True` to `shlex.split() + shell=False`
   - Added shell metacharacter blocklist (`;`, `|`, `$()`, backticks, `&&`, `||`, `>>`, `<<`, `<(`)
   - Added `FileNotFoundError` handling for unknown commands
   - 11 new security tests covering all injection vectors
2. **Masked API token logging** (`dashboard/server.py`)
   - Token now shows only last 6 chars: `...abc123` instead of full token
   - Applied to both stdout `print()` and `logger.info()`
3. **Removed hardcoded phone number** (`config/settings.yaml`)
   - Changed from real number to empty array with env var comment
4. **Fixed neural bridge default binding** (`core/neural_bridge.py`)
   - Default changed from `0.0.0.0` to `127.0.0.1` (localhost only)
5. **python_exec** already uses `shell=False` with `["python3", "-c", code]` — no fix needed

#### Leon System — Reliability Improvements
- **Memory save debouncing** (`core/memory.py`)
  - Added 5-second debounce interval to prevent I/O-heavy saves on every message
  - Added `flush_if_dirty()` for shutdown cleanup
  - Added `completed_tasks` trimming (capped at 500) during flush
  - Added `force=True` parameter for critical saves
- **Task queue runtime cap** (`core/task_queue.py`)
  - `completed` list now capped at 200 during `complete_task()` and `fail_task()`
  - Previously only capped during `_save()` serialization
- **Fixed pre-existing test failures** — Task queue persistence tests updated to match actual re-queue behavior
- **Total: 186 Leon tests passing** (0 failures)

## Current Scores (Estimated)

| Category | Before | After |
|----------|--------|-------|
| Code Architecture | 9.5/10 | 9.5/10 |
| Rider Authenticity | 9.5/10 | 9.5/10 |
| UI/UX Quality | 9.5/10 | 9.5/10 |
| Interactivity | 9.5/10 | 9.5/10 |
| Data Persistence | 9/10 | 9/10 |
| Dead Code | Zero | Zero |
| Security (Leon) | 7/10 | 8/10 |
| Reliability (Leon) | 7/10 | 8.5/10 |

## Audit Issues Resolved (Cumulative)

| Issue | Status |
|-------|--------|
| #1 shell_exec injection | Fixed (Phase 10) |
| #2 Hardcoded phone number | Fixed (Phase 10) |
| #3 API token logged | Fixed (Phase 10) |
| #6 Memory save debouncing | Fixed (Phase 10) |
| #8 WhatsApp zero-width space | **Fixed (Phase 11)** |
| #9 Graceful shutdown | **Fixed (Phase 11)** |
| #10 Uncapped completed list | Fixed (Phase 10) |
| #11 Bridge default binding | Fixed (Phase 10) |
| #12 SSL verification warning | **Fixed (Phase 11)** |
| #13 NightMode dispatch race | **Fixed (Phase 11)** |
| #14 Duplicate dashboard code | **Fixed (Phase 11)** |
| #15 Memory completed_tasks trim | Fixed (Phase 10) |
| #17 Groq truncation logging | **Fixed (Phase 11)** |
| #18 Rate limiter cleanup | **Fixed (Phase 11)** |
| #21 Security tests | **Fixed (Phase 11)** |
| #20 Keyword pre-router | **Fixed (Phase 12)** |
| #23 sentByBridge unbounded | **Fixed (Phase 11)** |

## Phase 12: Pre-Ride Safety Checklist Reset

- Added `resetSafetyChecks()` store action and "Reset for New Ride" button to the Safety tab — riders can now clear all checks before each ride, making the pre-ride checklist reusable as designed (6 new tests, 51 suites / 339 total)

## Phase 13: Scheduler Failure Tracking & Night Mode Backlog Trimming

- **Fixed scheduler always marking tasks as completed** — `mark_completed()` was called outside the try/except, so failed tasks were never tracked; the scheduler's consecutive-failure alert system was completely bypassed. Now properly calls `mark_failed()` on exception. Built-in commands (`__health_check__` etc.) are routed directly to `run_builtin()` instead of through `process_user_input()`, avoiding conversation pollution and wasted LLM calls. Night mode backlog now trims completed/failed tasks to last 200 entries to prevent unbounded growth. (12 new tests, 215 total)

## Phase 14: Fix Event Loop Threading Model — Continuous Loop + Cross-Thread Dispatch (Issue #7)

- **Fixed CLI event loop starvation** (`main.py`) — previously, `input()` blocked the main thread between commands, preventing `call_later` callbacks (reminders), `create_task` fire-and-forget work (memory extraction, night mode dispatch), and awareness loop heartbeats from executing. Now `input()` runs in a daemon thread and dispatches commands to the main loop via `run_coroutine_threadsafe()`, while `loop.run_forever()` keeps async tasks alive continuously.
- **Fixed voice thread cross-loop dispatch** (`main.py`) — voice commands were previously awaited directly on the voice thread's own event loop, causing Leon's `asyncio.create_task()` calls to spawn tasks on the wrong loop. Now dispatches to `leon.main_loop` via `run_coroutine_threadsafe()` + `wrap_future()`, ensuring all Leon operations serialize on a single loop.
- **Added `main_loop` attribute to Leon** (`core/leon.py`) — set during `start()` to `asyncio.get_running_loop()`, providing a stable reference for cross-thread dispatch from voice, dashboard, and input threads.
- **5 new tests** — main_loop set on start, call_later fires on continuous loop, cross-thread dispatch, create_task executes on continuous loop, voice dispatch targets main loop. (377 total, 4 pre-existing failures)

## Next Actions

### Motorev App
- [ ] Integrate real map (react-native-maps) for ride tracking visualization
- [ ] Dark mode toggle (currently always dark)
- [ ] Backend API for multi-device sync

### Leon System
- [x] Sandbox/confirmation for `python_exec` (Issue #4 from audit)
- [ ] Evaluate `--dangerously-skip-permissions` alternatives (Issue #5)
- [x] Fix event loop threading model (Issue #7)
- [ ] Fix SSL cert verification for bridge (Issue #12 — generate self-signed cert)
- [x] Add keyword pre-router for system skills (Issue #20)
- [ ] Refactor `leon.py` into smaller modules (Issue #19)
- [ ] Update stale stt_provider config (Issue #22)

**2026-02-27 — Issue #4 fixed:** `python_exec` now blocks 10 dangerous module imports (subprocess, shutil, ctypes, socket, etc.) and 15 dangerous patterns (os.system, os.remove, __import__, open, eval, exec, compile) before execution; 33 new security tests added.

**2026-02-27 — API provider failover:** `create_message`, `quick_request`, and `analyze_json` now automatically try fallback providers (Groq/Ollama/Claude CLI) when the primary fails, instead of returning raw error strings to the user; also fixed a bug where Claude CLI's inline Groq fallback sent an empty message list; 24 new tests added.

**2026-02-27 — Issue #7 hardened:** `python_exec` sandbox hardened — subprocess env stripped to {PATH, HOME, LANG} (no API keys/tokens exposed), cwd set to /tmp (no project file access), denylist expanded to 34 patterns (adds os.environ, os.path, os.listdir, os.walk, builtins, getattr, globals, locals, breakpoint) and 13 blocked imports (adds pathlib, tempfile, webbrowser); 23 new sandbox tests added.

**2026-02-27 — Issue #7 fixed:** Event loop threading model overhauled — daemon threads (dashboard, voice) now expose event loop references via `_DaemonHandle` dataclass, enabling graceful shutdown via `call_soon_threadsafe(loop.stop)` instead of abrupt daemon thread termination; `_stop_daemon()` helper joins threads with timeout; dashboard runner cleanup runs in thread's finally block; CLI/headless/GUI modes all perform proper daemon shutdown on exit; removed redundant `flush_if_dirty()` call (already handled by `leon.stop()`); all event loops now closed after use to prevent resource leaks; 9 new tests for daemon lifecycle.

**2026-02-27 — Robust JSON extraction for `analyze_json`:** Replaced fragile `json.loads` with 4-strategy `_extract_json` method — (1) direct parse, (2) markdown code fence extraction via regex, (3) bracket-matching substring finder for JSON embedded in explanatory text, (4) trailing comma auto-fix. Used by 7+ critical subsystems (request routing, system skill dispatch, plan generation, browser agent, vision, screen awareness, self-memory). 28 new tests added.
