#!/usr/bin/env python3
"""
Agent Progress Monitor — Sends WhatsApp updates every 5 minutes
about the self-improvement agents' progress.

Usage: python3 scripts/agent-monitor.py
"""

import json
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT = Path("/home/deansabr/leon-system")
OUTPUTS = PROJECT / "data" / "agent_outputs"
WHATSAPP_SEND_URL = "http://127.0.0.1:3001/send"
PHONE = "17275427167"
CHECK_INTERVAL = 300  # 5 minutes

TASKS = {
    "self_improve_01_bugs": "Bug Fixes",
    "self_improve_02_dashboard_ui": "Dashboard UI",
    "self_improve_03_system_skills": "System Skills",
    "self_improve_04_voice": "Voice System",
    "self_improve_05_cleanup": "Code Cleanup",
    "self_improve_06_personality": "Personality",
}


def send_whatsapp(message: str):
    """Send a message via the WhatsApp bridge outbound API."""
    try:
        data = json.dumps({"number": PHONE, "message": message}).encode()
        req = urllib.request.Request(
            WHATSAPP_SEND_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[monitor] WhatsApp send failed: {e}")
        return False


def check_agent_status() -> dict:
    """Check each agent's output file to determine status."""
    statuses = {}
    for task_key, label in TASKS.items():
        log_file = OUTPUTS / f"{task_key}.log"
        err_file = OUTPUTS / f"{task_key}.err"

        if not log_file.exists():
            statuses[label] = {"status": "not started", "lines": 0, "last_line": ""}
            continue

        log_text = log_file.read_text()
        err_text = err_file.read_text() if err_file.exists() else ""
        lines = [l for l in log_text.strip().split("\n") if l.strip()]
        line_count = len(lines)
        last_line = lines[-1][:100] if lines else ""

        # Check if process is still running (check if file is still being written)
        mtime = log_file.stat().st_mtime
        age = time.time() - mtime

        if line_count == 0:
            status = "waiting"
        elif age < 120:  # File modified in last 2 min = still running
            status = "working"
        elif err_text.strip() and "error" in err_text.lower():
            status = "failed"
        else:
            status = "done"

        statuses[label] = {
            "status": status,
            "lines": line_count,
            "last_line": last_line,
            "age_min": int(age / 60),
        }

    return statuses


def format_update(statuses: dict, check_num: int) -> str:
    """Format a progress update message."""
    now = datetime.now().strftime("%H:%M")
    emoji = {"not started": "⏳", "waiting": "⏳", "working": "🔨", "done": "✅", "failed": "❌"}

    lines = [f"🤖 Leon Self-Improvement Update #{check_num} ({now})\n"]

    working = 0
    done = 0
    failed = 0

    for label, info in statuses.items():
        s = info["status"]
        e = emoji.get(s, "❓")
        detail = ""
        if s == "working":
            detail = f" — {info['lines']} lines output"
            working += 1
        elif s == "done":
            detail = f" — complete ({info['lines']} lines)"
            done += 1
        elif s == "failed":
            detail = " — FAILED"
            failed += 1

        lines.append(f"{e} {label}: {s}{detail}")

    lines.append(f"\nSummary: {done} done, {working} working, {failed} failed")

    if done == len(statuses):
        lines.append("\n🎉 ALL AGENTS FINISHED! Check the results when you wake up.")

    return "\n".join(lines)


def main():
    print(f"[monitor] Starting agent progress monitor")
    print(f"[monitor] WhatsApp: {PHONE}")
    print(f"[monitor] Checking every {CHECK_INTERVAL}s")
    print(f"[monitor] Watching: {OUTPUTS}")
    print()

    # Send initial message
    send_whatsapp("🤖 Leon self-improvement started. I'll update you every 5 minutes. Go to sleep! 😴")
    print("[monitor] Sent startup notification")

    check_num = 0
    all_done_sent = False

    while True:
        time.sleep(CHECK_INTERVAL)
        check_num += 1

        statuses = check_agent_status()
        update = format_update(statuses, check_num)

        print(f"\n[monitor] Check #{check_num}:")
        print(update)

        sent = send_whatsapp(update)
        print(f"[monitor] WhatsApp sent: {sent}")

        # Check if all done
        all_statuses = [s["status"] for s in statuses.values()]
        if all(s in ("done", "failed") for s in all_statuses):
            if not all_done_sent:
                send_whatsapp("🏁 All agents finished! Leon self-improvement complete. Check results when you wake up.")
                all_done_sent = True
                print("[monitor] All agents finished — sent final notification")
                # Keep running a bit longer in case more context is needed
                time.sleep(600)
                break


if __name__ == "__main__":
    main()
