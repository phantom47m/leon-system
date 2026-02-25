# Leon System — Active Blockers

**Updated:** 2026-02-25

## Motorev App Blockers

### High
1. **No map integration** — "View Map" buttons are no-ops. No `react-native-maps` installed.

### Medium
2. **No backend API** — Data persistence is local-only (AsyncStorage). No multi-device sync.
3. **No dark mode toggle** — App is always dark; no light mode option.

### Low
4. **No crash detection** — SOS sends alerts manually, no accelerometer-based detection.
5. **No Bluetooth intercom** — Voice channel buttons show "coming soon" alerts.
6. **No push notifications** — No notification delivery system.

### Resolved This Session (Phase 10)
- ~~Hardcoded modifications data~~ → Modifications fully wired to store with add/remove/persist
- ~~shell_exec command injection~~ → Uses shlex.split + shell=False with metachar blocklist
- ~~API token logged in plaintext~~ → Masked to last 6 characters
- ~~Phone number in version control~~ → Moved to env var
- ~~Bridge binds to 0.0.0.0~~ → Defaults to 127.0.0.1
- ~~Unbounded memory saves~~ → Debounced (5s interval) with flush_if_dirty()
- ~~Uncapped completed tasks~~ → Capped at 200 during runtime + 500 on flush

### Resolved Earlier (Phase 2–9)
- ~~33% unused dependencies~~ → Removed 10 packages
- ~~No design tokens~~ → Full dark theme token system
- ~~Zero button handlers~~ → Every button responds with feedback
- ~~No data persistence~~ → AsyncStorage store with profile/settings/safety
- ~~Hardcoded user name~~ → Onboarding flow with name entry
- ~~Stock photos reused~~ → Diversified images
- ~~No GPS/ride tracking~~ → Full GPS tracking with Haversine calc
- ~~No real weather API~~ → Open-Meteo integration
