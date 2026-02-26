# Tempest API Notes (quick reference)

## Endpoint used by this skill

- Current station observations:
  - `GET https://swd.weatherflow.com/swd/rest/observations/station/{station_id}?token={token}`
- Current device observations:
  - `GET https://swd.weatherflow.com/swd/rest/observations/device/{device_id}?token={token}`

## Typical response fields

- `obs`: array of observation arrays (most recent usually first)
- Some payloads may use `obs_st`; the script supports both

## Observation index mapping used in script

Given `obs = [ ... ]`:

- `obs[0]` timestamp epoch (seconds)
- `obs[1]` wind lull (m/s)
- `obs[2]` wind average (m/s)
- `obs[3]` wind gust (m/s)
- `obs[4]` wind direction (degrees)
- `obs[6]` station pressure (mb)
- `obs[7]` air temperature (°C)
- `obs[8]` relative humidity (%)
- `obs[9]` illuminance (lux)
- `obs[10]` UV index
- `obs[11]` solar radiation (W/m²)
- `obs[12]` rain accumulated in interval (mm)
- `obs[13]` precipitation type code
- `obs[14]` lightning average distance (km)
- `obs[15]` lightning strike count in interval
- `obs[16]` battery (V)
- `obs[17]` report interval (min)
- `obs[18]` local daily rain accumulation (mm)

## Configuration defaults

The skill expects these environment variables:
- You can set TEMPEST_STATION_ID, TEMPEST_DEVICE_ID, or both. If both are set, TEMPEST_DEVICE_ID is used.

- `TEMPEST_API_TOKEN`
- `TEMPEST_STATION_ID`
- `TEMPEST_DEVICE_ID`
- `TEMPEST_UNITS` (`us` or `metric`)

## Security

- Never hardcode token values in the skill files.
- Pass token via env var or CLI argument.
