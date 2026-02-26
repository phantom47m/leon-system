---
name: tempest-weather
description: Get current weather conditions from a WeatherFlow Tempest station using the Tempest REST API. Use when asked for backyard/home weather, Tempest station readings, wind/gust/rain/lightning from a specific station, or quick local weather summaries sourced from Tempest data.
version: 1.0.6
license: MIT
metadata:
  openclaw:
    requires:
      env:
        - TEMPEST_API_TOKEN
        - TEMPEST_STATION_ID
        - TEMPEST_DEVICE_ID
        - TEMPEST_UNITS
      anyBins:
        - python3
    primaryEnv: TEMPEST_API_TOKEN
    homepage: https://github.com/wranglerdriver/tempest-weather
---

# Tempest Weather

Use this skill to fetch current conditions from a Tempest station or device and summarize them clearly.

## Run the fetch script

Use:

```bash
python3 scripts/get_tempest_weather.py
```

The script reads configuration from environment variables by default, if both station and device id are set device_id is used by default:

- `TEMPEST_API_TOKEN` (required)
- `TEMPEST_STATION_ID` (optional if `TEMPEST_DEVICE_ID` is set)
- `TEMPEST_DEVICE_ID` (optional if `TEMPEST_STATION_ID` is set)
- `TEMPEST_UNITS` (optional: `metric` or `us`, default `us`)

## Useful command options

```bash
# Explicit station/token
python3 scripts/get_tempest_weather.py --station-id 12345 --token "$TEMPEST_API_TOKEN"

# Explicit device/token
python3 scripts/get_tempest_weather.py --device-id 67890 --token "$TEMPEST_API_TOKEN"

# Metric output
python3 scripts/get_tempest_weather.py --units metric

# JSON only (machine-friendly)
python3 scripts/get_tempest_weather.py --json
```

## Output behavior

- Emit concise JSON (always)
- Include a short human summary unless `--json` is used
- Include timestamp and source URL for traceability

## If data fetch fails

- Check token validity and station/device ID
- Retry once for transient network errors
- Return a short actionable error message

## Field mapping reference

For Tempest observation index mapping and response notes, read:

- `references/tempest-api.md`

## License

- `LICENSE` (MIT)

## Source

- https://github.com/wranglerdriver/tempest-weather
