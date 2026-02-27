# Leon System — Active Blockers

**Updated:** 2026-02-25

## Motorev App Blockers

### Medium
1. **No backend API** — Data persistence is local-only (AsyncStorage). No multi-device sync.
2. **No dark mode toggle** — App is always dark; no light mode option.
3. **No push notifications** — No notification delivery system.

### Low
4. **No crash detection** — SOS sends alerts manually, no accelerometer-based detection.
5. **No Bluetooth intercom** — Voice channel buttons show "coming soon" alerts.

## Leon System Blockers

### Medium
7. ~~**python_exec runs unsandboxed**~~ — **Fixed:** env stripped, cwd=/tmp, expanded denylist (34 patterns, 13 imports).
8. **--dangerously-skip-permissions** — All spawned agents run with full permissions.
9. **Event loop threading** — Multiple event loops in threads (not thread-safe).

### Low
10. **leon.py is 1400+ lines** — Too many responsibilities in one class.
11. **System skill routing costs tokens** — ~1000-token AI prompt per PC control command.
12. **Stale stt_provider config** — Config says "deepgram" but code uses Groq Whisper.

### Resolved This Session (Phase 12)
- ~~No map integration~~ → `react-native-maps` installed, RideMap component with live tracking + history route replay
- ~~No camera avatar picker~~ → `expo-image-picker` installed, camera + gallery picker on profile

### Resolved Earlier (Phase 11)
- ~~WhatsApp watchdog zero-width space~~ → Uses /health endpoint only
- ~~NightMode dispatch race condition~~ → asyncio.Lock guards _try_dispatch
- ~~Duplicate dashboard startup code~~ → Shared _start_dashboard_thread() helper
- ~~No graceful shutdown~~ → force-flush memory + coordinated stop
- ~~Rate limiter unbounded dict~~ → Counter-based periodic cleanup
- ~~Groq context silently truncated~~ → Debug log on truncation
- ~~sentByBridge grows without bound~~ → Map with 5-minute TTL eviction
- ~~SSL verification disabled silently~~ → Security warning logged

### Resolved Earlier (Phase 2–10)
- ~~33% unused dependencies~~ → Removed 10 packages
- ~~No design tokens~~ → Full dark theme token system
- ~~Zero button handlers~~ → Every button responds with feedback
- ~~No data persistence~~ → AsyncStorage store with profile/settings/safety
- ~~Hardcoded user name~~ → Onboarding flow with name entry
- ~~Stock photos reused~~ → Diversified images
- ~~No GPS/ride tracking~~ → Full GPS tracking with Haversine calc
- ~~No real weather API~~ → Open-Meteo integration
- ~~Hardcoded modifications data~~ → Modifications fully wired to store with add/remove/persist
- ~~shell_exec command injection~~ → Uses shlex.split + shell=False with metachar blocklist
- ~~API token logged in plaintext~~ → Masked to last 6 characters
- ~~Phone number in version control~~ → Moved to env var
- ~~Bridge binds to 0.0.0.0~~ → Defaults to 127.0.0.1
- ~~Unbounded memory saves~~ → Debounced (5s interval) with flush_if_dirty()
- ~~Uncapped completed tasks~~ → Capped at 200 during runtime + 500 on flush
