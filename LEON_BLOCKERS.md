# Leon System — Active Blockers

**Updated:** 2026-02-25

## Motorev App Blockers

### Critical
1. **Zero button handlers** — Every `TouchableOpacity` in the app either has no `onPress` or an empty `() => {}`. Users cannot interact with any feature.
2. **No user authentication** — No login/signup flow. Profile data is hardcoded.
3. **No data persistence** — All ride data, user stats, and settings are inline mock data. Nothing persists between sessions.

### High
4. **No GPS / ride tracking** — The Rides tab is cosmetic. "Start Ride" button exists but does nothing.
5. **No real weather API** — Weather card shows hardcoded "22°C, Perfect for riding".
6. **No map integration** — "View Map" buttons are no-ops. No `react-native-maps` installed.

### Medium
7. **Stock photos reused** — Same 2 Pexels images used across all screens (2393816, 1413412).
8. **Large monolith files** — `connect.tsx` (340 LOC), `rides.tsx` (233 LOC) could benefit from further decomposition.
9. **Hardcoded user name** — Profile shows "Alex Rodriguez" with no way to change it.

### Resolved This Session
- ~~33% unused dependencies~~ → Removed 10 packages
- ~~No design tokens~~ → Full dark theme token system
- ~~Dashboard has no clear primary action~~ → "Start Ride" is now the hero CTA
- ~~Garage Overview always visible~~ → Only shows on bikes tab
- ~~Profile has 9 sections~~ → Consolidated to 5
- ~~Connect rider cards too dense~~ → Simplified to essentials
