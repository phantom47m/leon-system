#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time
from urllib import error, parse, request

API_BASE = "https://swd.weatherflow.com/swd/rest"


def c_to_f(c):
    return (c * 9.0 / 5.0) + 32.0


def ms_to_mph(ms):
    return ms * 2.2369362921


def mb_to_inhg(mb):
    return mb * 0.0295299830714


def mm_to_in(mm):
    return mm * 0.03937007874


def read_version_from_skill_md():
    try:
        skill_md = pathlib.Path(__file__).resolve().parent.parent / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
    except Exception:
        return None

    if not text.startswith("---"):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter = parts[1]
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.startswith("version:"):
            value = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def detect_version():
    # Read SKILL.md frontmatter version when available
    return read_version_from_skill_md()


def build_user_agent():
    version = detect_version()
    if version:
        return f"openclaw-tempest-skill/{version}"
    return "openclaw-tempest-skill"


def get_json(url, retries=1, timeout=20):
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = request.Request(url, headers={"User-Agent": build_user_agent()})
            with request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.8)
    raise last_err


def parse_obs(obs):
    # Tempest obs index mapping (obs array)
    return {
        "timestamp_epoch": obs[0] if len(obs) > 0 else None,
        "wind_lull_mps": obs[1] if len(obs) > 1 else None,
        "wind_avg_mps": obs[2] if len(obs) > 2 else None,
        "wind_gust_mps": obs[3] if len(obs) > 3 else None,
        "wind_direction_deg": obs[4] if len(obs) > 4 else None,
        "station_pressure_mb": obs[6] if len(obs) > 6 else None,
        "air_temp_c": obs[7] if len(obs) > 7 else None,
        "relative_humidity_pct": obs[8] if len(obs) > 8 else None,
        "illuminance_lux": obs[9] if len(obs) > 9 else None,
        "uv_index": obs[10] if len(obs) > 10 else None,
        "solar_radiation_wpm2": obs[11] if len(obs) > 11 else None,
        "rain_accumulated_mm_last_interval": obs[12] if len(obs) > 12 else None,
        "precip_type": obs[13] if len(obs) > 13 else None,
        "lightning_avg_distance_km": obs[14] if len(obs) > 14 else None,
        "lightning_strike_count_last_interval": obs[15] if len(obs) > 15 else None,
        "battery_v": obs[16] if len(obs) > 16 else None,
        "report_interval_min": obs[17] if len(obs) > 17 else None,
        "local_daily_rain_mm": obs[18] if len(obs) > 18 else None,
    }


def convert_units(parsed, units):
    if units == "metric":
        out = dict(parsed)
        out["units"] = {
            "temperature": "C",
            "wind": "m/s",
            "pressure": "mb",
            "rain": "mm",
            "lightning_distance": "km",
        }
        return out

    out = dict(parsed)
    if out.get("air_temp_c") is not None:
        out["air_temp_f"] = round(c_to_f(out["air_temp_c"]), 1)
    if out.get("wind_lull_mps") is not None:
        out["wind_lull_mph"] = round(ms_to_mph(out["wind_lull_mps"]), 1)
    if out.get("wind_avg_mps") is not None:
        out["wind_avg_mph"] = round(ms_to_mph(out["wind_avg_mps"]), 1)
    if out.get("wind_gust_mps") is not None:
        out["wind_gust_mph"] = round(ms_to_mph(out["wind_gust_mps"]), 1)
    if out.get("station_pressure_mb") is not None:
        out["station_pressure_inhg"] = round(mb_to_inhg(out["station_pressure_mb"]), 3)
    if out.get("rain_accumulated_mm_last_interval") is not None:
        out["rain_accumulated_in_last_interval"] = round(mm_to_in(out["rain_accumulated_mm_last_interval"]), 3)
    if out.get("local_daily_rain_mm") is not None:
        out["local_daily_rain_in"] = round(mm_to_in(out["local_daily_rain_mm"]), 3)

    out["units"] = {
        "temperature": "F",
        "wind": "mph",
        "pressure": "inHg",
        "rain": "in",
        "lightning_distance": "km",
    }
    return out


def estimate_sky_condition(data):
    """
    Approximate sky condition from station light/radiation readings.
    This is a heuristic estimate, not measured cloud cover.
    """
    ts = data.get("timestamp_epoch")
    if ts:
        hour = dt.datetime.fromtimestamp(ts).astimezone().hour
        if hour < 6 or hour > 20:
            return "Night"

    lux = data.get("illuminance_lux")
    solar = data.get("solar_radiation_wpm2")

    if lux is None and solar is None:
        return None

    score = 0.0
    if lux is not None:
        score += min(max(lux / 50000.0, 0.0), 1.0)
    if solar is not None:
        score += min(max(solar / 800.0, 0.0), 1.0)
    score /= 2.0

    if score >= 0.65:
        return "Sunny"
    if score >= 0.45:
        return "Mostly sunny"
    if score >= 0.25:
        return "Partly cloudy"
    if score >= 0.10:
        return "Mostly cloudy"
    return "Overcast"


def build_event_phrases(data, units):
    phrases = []

    rain_interval_mm = data.get("rain_accumulated_mm_last_interval")
    if rain_interval_mm is not None and rain_interval_mm > 0:
        if units == "us":
            phrases.append(f"rain now ({data.get('rain_accumulated_in_last_interval')} in/interval)")
        else:
            phrases.append(f"rain now ({rain_interval_mm} mm/interval)")

    strikes = data.get("lightning_strike_count_last_interval")
    if strikes is not None and strikes > 0:
        dist = data.get("lightning_avg_distance_km")
        if dist is not None:
            phrases.append(f"lightning activity ({strikes} strikes, avg {dist} km)")
        else:
            phrases.append(f"lightning activity ({strikes} strikes)")

    return phrases


def make_summary(data, units):
    ts = data.get("timestamp_epoch")
    local_ts = (
        dt.datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        if ts
        else "unknown time"
    )

    sky = estimate_sky_condition(data)
    sky_part = f"sky est. {sky}, " if sky else ""

    if units == "us":
        t = data.get("air_temp_f")
        w = data.get("wind_avg_mph")
        g = data.get("wind_gust_mph")
        p = data.get("station_pressure_inhg")
        r = data.get("local_daily_rain_in")
        base = (
            f"Tempest @ {local_ts}: "
            f"{sky_part}temp {t}°F, wind {w} mph (gust {g}), "
            f"humidity {data.get('relative_humidity_pct')}%, pressure {p} inHg, "
            f"rain today {r} in"
        )
    else:
        t = data.get("air_temp_c")
        w = data.get("wind_avg_mps")
        g = data.get("wind_gust_mps")
        p = data.get("station_pressure_mb")
        r = data.get("local_daily_rain_mm")
        base = (
            f"Tempest @ {local_ts}: "
            f"{sky_part}temp {t}°C, wind {w} m/s (gust {g}), "
            f"humidity {data.get('relative_humidity_pct')}%, pressure {p} mb, "
            f"rain today {r} mm"
        )

    events = build_event_phrases(data, units)
    if events:
        return base + "; " + "; ".join(events) + "."
    return base + "."


def build_observations_url(token, station_id=None, device_id=None):
    q = parse.urlencode({"token": token})
    if device_id:
        return f"{API_BASE}/observations/device/{device_id}?{q}"
    return f"{API_BASE}/observations/station/{station_id}?{q}"


def extract_obs_list(payload):
    return payload.get("obs") or payload.get("obs_st")


def main():
    ap = argparse.ArgumentParser(description="Fetch current weather from a Tempest station/device")
    ap.add_argument("--station-id", default=os.getenv("TEMPEST_STATION_ID"))
    ap.add_argument("--device-id", default=os.getenv("TEMPEST_DEVICE_ID"))
    ap.add_argument("--token", default=os.getenv("TEMPEST_API_TOKEN"))
    ap.add_argument("--units", default=os.getenv("TEMPEST_UNITS", "us"), choices=["us", "metric"])
    ap.add_argument("--json", action="store_true", help="Print JSON only")
    args = ap.parse_args()

    if not args.station_id and not args.device_id:
        print(
            "ERROR: Missing station/device id. Set TEMPEST_STATION_ID or TEMPEST_DEVICE_ID (or pass --station-id/--device-id)",
            file=sys.stderr,
        )
        sys.exit(2)
    if not args.token:
        print("ERROR: Missing API token. Set TEMPEST_API_TOKEN or pass --token", file=sys.stderr)
        sys.exit(2)

    url = build_observations_url(args.token, station_id=args.station_id, device_id=args.device_id)

    try:
        payload = get_json(url, retries=1)
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        print(f"ERROR: Tempest API HTTP {e.code}. {body[:300]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to fetch Tempest data: {e}", file=sys.stderr)
        sys.exit(1)

    obs_list = extract_obs_list(payload)
    if not obs_list:
        print("ERROR: Tempest response did not include observations (obs/obs_st)", file=sys.stderr)
        print(json.dumps(payload, indent=2)[:1200], file=sys.stderr)
        sys.exit(1)

    parsed = parse_obs(obs_list[0])
    converted = convert_units(parsed, args.units)

    result = {
        "source": "WeatherFlow Tempest REST API",
        "station_id": str(args.station_id) if args.station_id else None,
        "device_id": str(args.device_id) if args.device_id else None,
        "request_url": url.replace(args.token, "***redacted***"),
        "observed": converted,
    }

    print(json.dumps(result, indent=2, sort_keys=True))
    if not args.json:
        print()
        print(make_summary(converted, args.units))


if __name__ == "__main__":
    main()
