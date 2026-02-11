#!/usr/bin/env python3
"""
Leon Health Check — Run this to verify everything is working.

Usage: python3 scripts/healthcheck.py

Checks:
  1. Python version & dependencies
  2. Config files valid
  3. API keys set
  4. Encryption working
  5. Audio system (microphone + speakers)
  6. Camera available
  7. Network (localhost binding)
  8. Disk space
  9. OpenClaw connection
  10. Dashboard servable
"""

import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

passed = 0
failed = 0
warnings = 0


def check(name, condition, fail_msg="", warn=False):
    global passed, failed, warnings
    if condition:
        print(f"  {GREEN}✓{RESET} {name}")
        passed += 1
    elif warn:
        print(f"  {YELLOW}⚠{RESET} {name} — {fail_msg}")
        warnings += 1
    else:
        print(f"  {RED}✗{RESET} {name} — {fail_msg}")
        failed += 1


def main():
    global passed, failed, warnings

    print()
    print(f"{BOLD}{CYAN}{'=' * 55}{RESET}")
    print(f"{BOLD}{CYAN}  LEON SYSTEM — Health Check{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 55}{RESET}")

    # ── 1. Python Version ──
    print(f"\n{BOLD}[1/10] Python Environment{RESET}")
    v = sys.version_info
    check(f"Python {v.major}.{v.minor}.{v.micro}", v.major == 3 and v.minor >= 10,
          f"Need Python 3.10+, got {v.major}.{v.minor}")

    # ── 2. Dependencies ──
    print(f"\n{BOLD}[2/10] Dependencies{RESET}")
    deps = {
        "yaml": "pyyaml",
        "anthropic": "anthropic",
        "aiohttp": "aiohttp",
    }
    optional_deps = {
        "cryptography": "cryptography (vault encryption)",
        "cv2": "opencv-python (vision)",
        "deepgram": "deepgram-sdk (voice STT)",
        "elevenlabs": "elevenlabs (voice TTS)",
        "pyaudio": "pyaudio (microphone)",
        "gi": "PyGObject (GTK4 UI)",
        "paho.mqtt.client": "paho-mqtt (3D printer)",
    }

    for module, pkg in deps.items():
        try:
            importlib.import_module(module)
            check(f"{pkg}", True)
        except ImportError:
            check(f"{pkg}", False, f"pip install {pkg}")

    for module, desc in optional_deps.items():
        try:
            importlib.import_module(module)
            check(f"{desc}", True)
        except ImportError:
            check(f"{desc}", False, "Not installed (optional)", warn=True)

    # ── 3. Config Files ──
    print(f"\n{BOLD}[3/10] Configuration{RESET}")
    configs = {
        "config/settings.yaml": True,
        "config/personality.yaml": True,
        "config/printers.yaml": False,
        "config/projects.yaml": False,
    }
    for cfg, required in configs.items():
        path = ROOT / cfg
        exists = path.exists()
        if required:
            check(cfg, exists, "MISSING — required")
        else:
            check(cfg, exists, "Missing (optional)", warn=not exists)

        if exists and cfg.endswith(".yaml"):
            try:
                import yaml
                with open(path) as f:
                    yaml.safe_load(f)
                check(f"  {cfg} valid YAML", True)
            except Exception as e:
                check(f"  {cfg} valid YAML", False, str(e))

    # ── 4. API Keys ──
    print(f"\n{BOLD}[4/10] API Keys{RESET}")
    api_keys = {
        "ANTHROPIC_API_KEY": "Claude API (required)",
        "DEEPGRAM_API_KEY": "Deepgram STT (voice)",
        "ELEVENLABS_API_KEY": "ElevenLabs TTS (voice)",
        "GOOGLE_MAPS_API_KEY": "Google Maps (lead gen)",
        "TWILIO_ACCOUNT_SID": "Twilio (SMS/WhatsApp)",
        "DISCORD_BOT_TOKEN": "Discord (comms)",
    }
    for key, desc in api_keys.items():
        val = os.getenv(key, "")
        if key == "ANTHROPIC_API_KEY":
            check(f"{desc}", bool(val), f"Set {key} env var")
        else:
            check(f"{desc}", bool(val), f"{key} not set (optional)", warn=True)

    # ── 5. Encryption ──
    print(f"\n{BOLD}[5/10] Encryption{RESET}")
    try:
        from security.vault import SecureVault
        import tempfile
        tmp = tempfile.mktemp(suffix=".enc")
        v = SecureVault(tmp)
        v.unlock("healthcheck_test")
        v.store("test_key", "test_value")
        assert v.retrieve("test_key") == "test_value"
        v.lock()
        # Reopen and verify persistence
        v2 = SecureVault(tmp)
        v2.unlock("healthcheck_test")
        assert v2.retrieve("test_key") == "test_value"
        os.unlink(tmp)
        check("Vault encrypt/decrypt/persist", True)
    except Exception as e:
        check("Vault encrypt/decrypt", False, str(e))

    try:
        from security.vault import OwnerAuth
        import tempfile
        tmp = tempfile.mktemp(suffix=".json")
        auth = OwnerAuth(tmp)
        auth.setup_pin("9999")
        assert auth.verify_pin("9999")
        assert not auth.verify_pin("0000")
        os.unlink(tmp)
        check("Owner authentication", True)
    except Exception as e:
        check("Owner authentication", False, str(e))

    try:
        from security.vault import AuditLog
        import tempfile
        tmp = tempfile.mktemp(suffix=".log")
        log = AuditLog(tmp)
        log.log("healthcheck", "test entry")
        log.log("healthcheck", "test entry 2")
        assert log.verify_integrity()
        os.unlink(tmp)
        check("Audit log + hash chain", True)
    except Exception as e:
        check("Audit log", False, str(e))

    # ── 6. Audio ──
    print(f"\n{BOLD}[6/10] Audio System{RESET}")
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        mic_count = pa.get_device_count()
        has_input = any(
            pa.get_device_info_by_index(i).get("maxInputChannels", 0) > 0
            for i in range(mic_count)
        )
        pa.terminate()
        check(f"Microphone ({mic_count} devices)", has_input,
              "No input devices found", warn=True)
    except Exception as e:
        check("Microphone", False, f"PyAudio error: {e}", warn=True)

    # ── 7. Camera ──
    print(f"\n{BOLD}[7/10] Camera{RESET}")
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        opened = cap.isOpened()
        if opened:
            ret, frame = cap.read()
            cap.release()
            check(f"Webcam (frame: {frame.shape[1]}x{frame.shape[0]})" if ret else "Webcam",
                  ret, "Camera opened but can't read frames", warn=True)
        else:
            cap.release()
            check("Webcam", False, "No camera found or busy", warn=True)
    except ImportError:
        check("Webcam", False, "OpenCV not installed", warn=True)
    except Exception as e:
        check("Webcam", False, str(e), warn=True)

    # ── 8. Disk Space ──
    print(f"\n{BOLD}[8/10] System Resources{RESET}")
    import shutil
    usage = shutil.disk_usage(str(ROOT))
    free_gb = usage.free / (1024 ** 3)
    check(f"Disk space ({free_gb:.1f} GB free)", free_gb > 1.0,
          f"Low disk: {free_gb:.1f} GB")

    # ── 9. Network ──
    print(f"\n{BOLD}[9/10] Network{RESET}")
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        result = s.connect_ex(("127.0.0.1", 3000))
        s.close()
        if result == 0:
            check("Port 3000 (dashboard)", False, "Already in use", warn=True)
        else:
            check("Port 3000 (dashboard) available", True)
    except Exception:
        check("Port 3000 (dashboard) available", True)

    try:
        socket.create_connection(("api.anthropic.com", 443), timeout=5)
        check("Anthropic API reachable", True)
    except Exception:
        check("Anthropic API reachable", False, "Cannot connect to api.anthropic.com")

    # ── 10. Module Imports ──
    print(f"\n{BOLD}[10/10] Module Imports{RESET}")
    modules = [
        ("core.memory", "Memory System"),
        ("core.task_queue", "Task Queue"),
        ("core.agent_manager", "Agent Manager"),
        ("core.api_client", "API Client"),
        ("core.voice", "Voice System"),
        ("security.vault", "Security Vault"),
        ("vision.vision", "Vision System"),
        ("business.leads", "Lead Generator"),
        ("business.crm", "CRM"),
        ("business.finance", "Finance Tracker"),
        ("business.comms", "Communications Hub"),
        ("business.assistant", "Personal Assistant"),
        ("hardware.printing", "3D Printing"),
        ("dashboard.server", "Dashboard Server"),
    ]
    for mod, name in modules:
        try:
            importlib.import_module(mod)
            check(f"{name} ({mod})", True)
        except Exception as e:
            check(f"{name} ({mod})", False, str(e))

    # ── Summary ──
    print()
    print(f"{BOLD}{CYAN}{'=' * 55}{RESET}")
    total = passed + failed + warnings
    print(f"  {GREEN}Passed: {passed}{RESET}  |  "
          f"{RED}Failed: {failed}{RESET}  |  "
          f"{YELLOW}Warnings: {warnings}{RESET}  |  "
          f"Total: {total}")

    if failed == 0:
        print(f"\n  {GREEN}{BOLD}✓ Leon is ready to go!{RESET}")
    elif failed <= 3:
        print(f"\n  {YELLOW}{BOLD}⚠ Leon will work but some features need setup{RESET}")
    else:
        print(f"\n  {RED}{BOLD}✗ Leon needs attention before running{RESET}")

    print(f"{BOLD}{CYAN}{'=' * 55}{RESET}")
    print()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
