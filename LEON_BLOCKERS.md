# Leon System — Active Blockers

**Updated:** 2026-02-25

## Motorev App Blockers

### High
1. **No GPS / ride tracking** — The Rides tab is cosmetic. "Start Ride" button exists but does nothing.
2. **No real weather API** — Weather card shows hardcoded "22°C, Perfect for riding".
3. **No map integration** — "View Map" buttons are no-ops. No `react-native-maps` installed.

### Medium
4. **Stock photos reused** — Same 2 Pexels images used across all screens (2393816, 1413412).
5. **No backend API** — Data persistence is local-only (AsyncStorage). No multi-device sync.

### Low
6. **No crash detection** — SOS sends alerts manually, no accelerometer-based detection.
7. **No Bluetooth intercom** — Voice channel buttons show "coming soon" alerts.
8. **No push notifications** — No notification delivery system.

### Resolved This Session (Phase 6)
- ~~Zero button handlers~~ → Every button across all 6 tabs responds with appropriate feedback
- ~~No user authentication~~ → Onboarding flow with name entry, store-driven routing
- ~~No data persistence~~ → AsyncStorage store persists profile, settings, safety checks
- ~~Hardcoded user name~~ → Profile reads from persistent store, set during onboarding
- ~~Large monolith files~~ → connect.tsx decomposed from 363 → 193 LOC (4 extracted components)

### Resolved Earlier (Phase 2–5)
- ~~33% unused dependencies~~ → Removed 10 packages
- ~~No design tokens~~ → Full dark theme token system
- ~~Dashboard has no clear primary action~~ → "Start Ride" is now the hero CTA
- ~~Garage Overview always visible~~ → Only shows on bikes tab
- ~~Profile has 9 sections~~ → Consolidated to 5
- ~~Connect rider cards too dense~~ → Simplified to essentials
- ~~StatusBadge fully saturated~~ → Semitransparent backgrounds
- ~~EmergencySOS misleading text~~ → Corrected subtitle
- ~~Safety tab missing from navigation~~ → Added Shield icon tab
