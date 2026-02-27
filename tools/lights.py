"""
Leon Light Controller — unified interface for Tuya/Geeni lights.

Control method: Tuya Cloud API (tinytuya.Cloud)
  - Controls lab ceiling light (and other Tuya/Geeni devices) via Tuya cloud MQTT.
  - Requires: config/lights.yaml and tuya_api_key/tuya_api_secret in config/user_config.yaml.
  - This is NOT Home Assistant — it talks directly to the Tuya cloud.

See also: core/system_skills.py (ha_set/ha_get skills) — separate protocol, separate hardware.
  The HA skills use the Home Assistant REST API and require HA_URL + HA_TOKEN env vars.
  These two systems are intentional parallel backends for different hardware, not duplicates.
"""

import asyncio
import colorsys
import difflib
import json
import logging
import re
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("leon.lights")

_CONFIG_PATH   = Path(__file__).parent.parent / "config" / "lights.yaml"
_USER_CFG_PATH = Path(__file__).parent.parent / "config" / "user_config.yaml"
_LAST_LIGHT_FILE = Path(__file__).parent.parent / "data" / "last_light.txt"
_lights_cache: Optional[list] = None
_cloud_cache = None

# Persist last-used light across restarts
def _load_last_light() -> Optional[str]:
    try:
        return _LAST_LIGHT_FILE.read_text().strip() or None
    except Exception:
        return None

def _save_last_light(name: str) -> None:
    try:
        _LAST_LIGHT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LAST_LIGHT_FILE.write_text(name)
    except Exception:
        pass

_last_light: Optional[str] = _load_last_light()


# ── Config loading ─────────────────────────────────────────────────────────────

def _load_lights() -> list:
    global _lights_cache
    if _lights_cache is not None:
        return _lights_cache
    try:
        data = yaml.safe_load(_CONFIG_PATH.read_text())
        _lights_cache = data.get("lights", [])
        return _lights_cache
    except Exception as e:
        logger.error("Failed to load lights config: %s", e)
        return []


def _get_tuya_cloud():
    global _cloud_cache
    if _cloud_cache is not None:
        return _cloud_cache
    try:
        cfg = yaml.safe_load(_USER_CFG_PATH.read_text())
        key    = cfg.get("tuya_api_key", "")
        secret = cfg.get("tuya_api_secret", "")
        region = cfg.get("tuya_api_region", "us")
        if not key or not secret:
            logger.error("tuya_api_key/tuya_api_secret not set in user_config.yaml")
            return None
        import tinytuya
        _cloud_cache = tinytuya.Cloud(
            apiRegion=region,
            apiKey=key,
            apiSecret=secret,
            apiDeviceID="",
        )
        return _cloud_cache
    except Exception as e:
        logger.error("Failed to init Tuya cloud: %s", e)
        return None


# ── Light lookup ───────────────────────────────────────────────────────────────

def _find_light(name: str) -> Optional[dict]:
    name_lower = name.lower().strip()
    for light in _load_lights():
        if light["name"].lower() == name_lower:
            return light
        if name_lower in [a.lower() for a in light.get("aliases", [])]:
            return light
    return None


def _find_light_from_message(message: str) -> Optional[dict]:
    msg = message.lower()
    candidates = []
    msg_words = msg.split()
    for light in _load_lights():
        names = [light["name"]] + light.get("aliases", [])
        for alias in names:
            alias_l = alias.lower()
            # Exact substring (fast path — zero change for typed commands)
            if alias_l in msg:
                candidates.append((len(alias), 1.0, light))
                continue
            # Fuzzy: check if any N-gram of words from msg is close to alias
            alias_words = alias_l.split()
            n = len(alias_words)
            for i in range(len(msg_words) - n + 1):
                chunk = " ".join(msg_words[i:i+n])
                ratio = difflib.SequenceMatcher(None, chunk, alias_l).ratio()
                if ratio >= 0.80:   # 80% similarity catches "sealing"→"ceiling"
                    candidates.append((len(alias), ratio, light))
    if candidates:
        candidates.sort(key=lambda x: (x[1], x[0]), reverse=True)
        return candidates[0][2]
    return None


# ── Cloud command helpers ──────────────────────────────────────────────────────

def _send(device_id: str, commands: list) -> bool:
    cloud = _get_tuya_cloud()
    if not cloud:
        return False
    try:
        r = cloud.sendcommand(device_id, {"commands": commands})
        ok = r.get("success", False)
        if not ok:
            logger.warning("Tuya cloud command failed for %s: %s", device_id, r)
        return ok
    except Exception as e:
        logger.warning("Tuya cloud exception for %s: %s", device_id, e)
        return False


def _rgb_to_hsv_tuya(r: int, g: int, b: int) -> dict:
    """Convert RGB 0-255 → Tuya HSV (h: 0-360, s: 0-1000, v: 0-1000)."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return {"h": round(h * 360), "s": round(s * 1000), "v": round(v * 1000)}


# ── Light actions ──────────────────────────────────────────────────────────────

def _light_on(light: dict) -> str:
    if not light.get("device_id"):
        return f"{light['name'].title()} isn't configured yet."
    ok = _send(light["device_id"], [{"code": "switch_led", "value": True}])
    return f"{light['name'].title()} is on." if ok else f"Couldn't reach {light['name']}."


def _light_off(light: dict) -> str:
    if not light.get("device_id"):
        return f"{light['name'].title()} isn't configured yet."
    ok = _send(light["device_id"], [{"code": "switch_led", "value": False}])
    return f"{light['name'].title()} is off." if ok else f"Couldn't reach {light['name']}."


def _light_brightness(light: dict, pct: int) -> str:
    if not light.get("device_id"):
        return f"{light['name'].title()} isn't configured yet."
    tuya_val = max(10, min(1000, round(pct * 10)))
    ok = _send(light["device_id"], [
        {"code": "switch_led", "value": True},
        {"code": "work_mode", "value": "white"},
        {"code": "bright_value_v2", "value": tuya_val},
    ])
    return f"{light['name'].title()} brightness set to {pct}%." if ok else f"Couldn't set brightness on {light['name']}."


def _light_color(light: dict, r: int, g: int, b: int) -> str:
    if not light.get("device_id"):
        return f"{light['name'].title()} isn't configured yet."
    hsv = _rgb_to_hsv_tuya(r, g, b)
    ok = _send(light["device_id"], [
        {"code": "switch_led", "value": True},
        {"code": "work_mode", "value": "colour"},
        {"code": "colour_data_v2", "value": json.dumps(hsv)},
    ])
    return f"{light['name'].title()} set to that color." if ok else f"Couldn't set color on {light['name']}."


def _light_white(light: dict, brightness: int = 100) -> str:
    if not light.get("device_id"):
        return f"{light['name'].title()} isn't configured yet."
    tuya_val = max(10, min(1000, round(brightness * 10)))
    ok = _send(light["device_id"], [
        {"code": "switch_led", "value": True},
        {"code": "work_mode", "value": "white"},
        {"code": "bright_value_v2", "value": tuya_val},
    ])
    return f"{light['name'].title()} set to white." if ok else f"Couldn't set {light['name']} to white."


def _light_color_with_brightness(light: dict, r: int, g: int, b: int, pct: int) -> str:
    """Set color AND brightness simultaneously. pct is 0-100."""
    if not light.get("device_id"):
        return f"{light['name'].title()} isn't configured yet."
    h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    tuya_val = max(10, min(1000, round(pct * 10)))
    hsv = {"h": round(h * 360), "s": round(s * 1000), "v": tuya_val}
    ok = _send(light["device_id"], [
        {"code": "switch_led", "value": True},
        {"code": "work_mode", "value": "colour"},
        {"code": "colour_data_v2", "value": json.dumps(hsv)},
    ])
    return f"{light['name'].title()} set to that color at {pct}%." if ok else f"Couldn't set color on {light['name']}."


# ── Color name → RGB ──────────────────────────────────────────────────────────

_COLORS = {
    "red":        (255, 0, 0),
    "green":      (0, 200, 0),
    "blue":       (0, 0, 255),
    "white":      None,  # handled as white mode
    "warm":       None,
    "warm white": None,
    "cool":       None,
    "cool white": None,
    "yellow":     (255, 200, 0),
    "orange":     (255, 80, 0),
    "purple":     (150, 0, 255),
    "pink":       (255, 0, 150),
    "cyan":       (0, 255, 255),
    "teal":       (0, 180, 150),
}

_WHITE_WORDS = {"white", "warm", "warm white", "cool", "cool white"}


def _parse_color(text: str):
    text = text.lower().strip()
    if text in _WHITE_WORDS:
        return "white"
    if text in _COLORS:
        return _COLORS[text]
    # Fuzzy match — typos like "bluie"→"blue", "purpel"→"purple" (3-char prefix)
    if len(text) >= 3:
        for color_name, rgb in _COLORS.items():
            if len(color_name) >= 3 and text[:3] == color_name[:3]:
                return "white" if color_name in _WHITE_WORDS else rgb
    m = re.search(r'(\d{1,3})[,\s]+(\d{1,3})[,\s]+(\d{1,3})', text)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


# ── Public natural language parser ────────────────────────────────────────────

_LIGHT_TRIGGER = re.compile(
    r'\b(turn\s+(?:on|off)|switch\s+(?:on|off)|set\s+\w|dim|brighten|'
    r'lights?\s+(?:on|off)|make\s+\w+\s+(?:light|room)|'
    r'(?:lab|bedroom|ceiling|office|wall)\s+light)\b',
    re.IGNORECASE,
)


_PRONOUN_RE = re.compile(
    r'\b(it|that|the\s+light|the\s+one|them)\b', re.IGNORECASE
)

# Continuation commands that imply the last-used light without naming it or using a pronoun
# e.g. "now back on", "back off", "turn back on", "on again", "off again", "now on"
_IMPLICIT_CONT_RE = re.compile(
    r'\b(back\s+on|back\s+off|on\s+again|off\s+again|'
    r'(?:now\s+)?(?:turn\s+(?:it\s+)?)?back\s+(?:on|off)|'
    r'^(?:now\s+)?(on|off)$)\b',
    re.IGNORECASE,
)


def parse_and_execute(message: str) -> Optional[str]:
    """
    Parse a natural language light command and execute it.
    Returns response string, or None if not a light command.
    """
    global _last_light
    msg = message.lower().strip()

    # Quick bail — must mention a known light or have a clear light verb
    light = _find_light_from_message(msg)
    all_lights_match = bool(re.search(r'\ball\s+lights?\b|\beverything\s+off\b', msg))

    # Pronoun resolution — "it", "that", "the light" → last used light
    if not light and not all_lights_match and _PRONOUN_RE.search(msg) and _last_light:
        light = _find_light(_last_light)

    # Implicit continuation — "now back on", "back off", "on again" → last used light
    if not light and not all_lights_match and _IMPLICIT_CONT_RE.search(msg) and _last_light:
        light = _find_light(_last_light)

    if not light and not all_lights_match:
        return None

    # Track whether the light was explicitly named (not just via pronoun/implicit)
    _explicitly_named = _find_light_from_message(msg) is not None or all_lights_match

    # Must also have an action verb to avoid false positives — but skip this check
    # when the user explicitly named a light (we're already confident it's a light command)
    if not _explicitly_named and not _LIGHT_TRIGGER.search(msg) and not re.search(
        r'\b(on|off|red|blue|green|purple|orange|pink|yellow|cyan|teal|white|warm|cool|dim|bright)\b', msg
    ):
        return None

    # Determine targets
    if all_lights_match:
        targets = [l for l in _load_lights() if l.get("device_id")]
    else:
        targets = [light] if light else []

    if not targets:
        return None

    # Parse intent
    is_off = bool(re.search(r'\b(turn\s+off|switch\s+off|off)\b', msg))
    is_on  = bool(re.search(r'\b(turn\s+on|switch\s+on|turn\s+it\s+on|switch\s+it\s+on)\b', msg))

    brightness_pct = None
    bm = re.search(r'\b(\d{1,3})\s*(?:%|percent)\b', msg)  # match "50%" or "50 percent"
    if bm:
        brightness_pct = int(bm.group(1))
    elif re.search(r'\b(dim|dimmer|low)\b', msg):
        brightness_pct = 10 if re.search(r'\b(more|lower|darker|less)\b', msg) else 20
    elif re.search(r'\b(half)\b', msg):
        brightness_pct = 50
    elif re.search(r'\b(bright|full|max)\b', msg) and not re.search(r'\b(brightness)\b', msg):
        brightness_pct = 100

    # Color detection
    color = None
    cm = re.search(
        r'\b(?:set|turn|make|change)\s+(?:\w+\s+)*?(?:to\s+)(\w+(?:\s+white)?)\s*$|'
        r'\bset\s+(?:\w+\s+)?(?:to\s+)?(\w+(?:\s+white)?)\s*$|'
        r'\b(red|green|blue|white|warm|warm\s+white|cool|cool\s+white|yellow|orange|purple|pink|cyan|teal)\b',
        msg
    )
    if cm:
        color_str = (cm.group(1) or cm.group(2) or cm.group(3) or "").strip()
        color = _parse_color(color_str)

    results = []
    for lt in targets:
        if is_off:
            results.append(_light_off(lt))
        elif color == "white":
            results.append(_light_white(lt, brightness_pct or 100))
        elif color and isinstance(color, tuple) and brightness_pct is not None:
            # Combined: set color and brightness together
            results.append(_light_color_with_brightness(lt, *color, brightness_pct))
        elif color and isinstance(color, tuple):
            results.append(_light_color(lt, *color))
        elif brightness_pct is not None:
            results.append(_light_brightness(lt, brightness_pct))
        elif is_on:
            results.append(_light_on(lt))
        else:
            results.append(_light_on(lt))  # default: turn on

    if results:
        # Track for pronoun resolution ("it", "the light") in next command + persist across restarts
        _last_light = targets[0]["name"]
        _save_last_light(_last_light)

    return " ".join(results) if results else None


def list_lights() -> str:
    lights = _load_lights()
    if not lights:
        return "No lights configured."
    lines = []
    for l in lights:
        status = "ready" if l.get("device_id") else "needs setup"
        lines.append(f"- **{l['name']}** [{l.get('type','tuya')}] {status}")
    return "\n".join(lines)
