"""
Leon System Skills — PC control via natural language

Provides categorized system control functions that Leon can invoke
based on AI-classified user intent. All commands use subprocess with
argument lists (no shell=True) to prevent injection.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.skills")


class SystemSkills:
    """System control skills for Leon — apps, media, desktop, files, network, etc."""

    def __init__(self):
        self._timers: list[dict] = []
        self._timer_id = 0
        self._clipboard_history: list[dict] = []
        self._clipboard_max = 50
        self._clipboard_last = ""
        self._clipboard_thread = None
        self._clipboard_running = False
        self._start_clipboard_monitor()
        logger.info("System skills module loaded")

    def _start_clipboard_monitor(self):
        """Start background thread that polls clipboard for changes."""
        self._clipboard_running = True

        def _monitor():
            while self._clipboard_running:
                try:
                    result = subprocess.run(
                        ["xclip", "-selection", "clipboard", "-o"],
                        capture_output=True, text=True, timeout=2,
                    )
                    if result.returncode == 0:
                        content = result.stdout[:500]
                        if content and content != self._clipboard_last:
                            self._clipboard_last = content
                            self._clipboard_history.append({
                                "content": content,
                                "timestamp": datetime.now().isoformat(),
                            })
                            if len(self._clipboard_history) > self._clipboard_max:
                                self._clipboard_history = self._clipboard_history[-self._clipboard_max:]
                except Exception:
                    pass
                time.sleep(3)

        self._clipboard_thread = threading.Thread(target=_monitor, daemon=True)
        self._clipboard_thread.start()

    # ------------------------------------------------------------------
    # Skill registry — used by AI router to pick the right skill
    # ------------------------------------------------------------------

    def get_skill_list(self) -> str:
        """Return a formatted list of all available skills for AI classification."""
        return """Available system skills:

APP CONTROL:
- open_app(name) — Launch an application (firefox, code, terminal, spotify, etc.)
- close_app(name) — Close/kill an application by name
- list_running() — List currently running GUI applications
- switch_to(name) — Bring a window to the foreground
- open_url(url) — Open a URL in the default browser
- open_file(path) — Open a file with its default application

SYSTEM INFO:
- cpu_usage() — Current CPU load percentage
- ram_usage() — Memory usage statistics
- disk_usage() — Storage usage for all mounted drives
- top_processes(n) — Top N processes by CPU/RAM usage
- uptime() — System uptime
- ip_address() — Local and public IP addresses
- battery() — Battery status (laptops only)
- temperature() — CPU temperature

PROCESS CONTROL:
- kill_process(name) — Kill process by name
- kill_pid(pid) — Kill process by PID

MEDIA CONTROL:
- play_pause() — Toggle media playback
- next_track() — Skip to next track
- prev_track() — Go to previous track
- volume_up(step) — Increase volume
- volume_down(step) — Decrease volume
- volume_set(pct) — Set volume to percentage
- mute() — Toggle mute
- now_playing() — Current track info

DESKTOP CONTROL:
- screenshot() — Take a full screenshot
- screenshot_area() — Take a screenshot of selected area
- clipboard_get() — Get clipboard contents
- clipboard_set(text) — Set clipboard contents
- notify(title, msg) — Show desktop notification
- lock_screen() — Lock the desktop
- brightness_up() — Increase brightness
- brightness_down() — Decrease brightness

FILE OPERATIONS:
- find_file(name) — Search for files by name
- find_recent(ext, hours) — Find recently modified files
- file_size(path) — Get file size
- trash(path) — Move file to trash
- list_downloads() — Show recent downloads

NETWORK:
- wifi_status() — Connected network info
- wifi_list() — Available WiFi networks
- speedtest() — Internet speed test
- ping(host) — Ping a host

TIMERS & REMINDERS:
- set_timer(minutes, label) — Set a countdown timer
- set_alarm(time_str, label) — Set an alarm
- list_timers() — List active timers
- cancel_timer(timer_id) — Cancel a timer

WEB/SEARCH:
- web_search(query) — Open a Google search
- define(word) — Look up a word definition
- weather(location) — Current weather

GPU:
- gpu_usage() — GPU utilization, memory, and temperature (NVIDIA/AMD)
- gpu_temp() — GPU temperature only

CLIPBOARD:
- clipboard_history() — Show recent clipboard entries
- clipboard_search(query) — Search clipboard history

WINDOW MANAGEMENT:
- list_workspaces() — List available workspaces
- move_to_workspace(n) — Move to workspace number N
- tile_left() — Tile current window to left half
- tile_right() — Tile current window to right half
- minimize_window() — Minimize current window
- maximize_window() — Maximize/restore current window
- close_window() — Close current window

DEV TOOLS:
- git_status(path) — Git status for a project
- npm_run(script, path) — Run an npm script
- pip_install(pkg) — Install a Python package
- port_check(port) — Check what's using a port"""

    # ------------------------------------------------------------------
    # Execute a skill by name (called by Leon's AI router)
    # ------------------------------------------------------------------

    async def execute(self, skill_name: str, args: dict) -> str:
        """Execute a skill by name with given arguments. Returns result string."""
        method = getattr(self, skill_name, None)
        if not method:
            return f"Unknown skill: {skill_name}"

        try:
            result = method(**args)
            # Handle both sync and async results
            if asyncio.iscoroutine(result):
                result = await result
            return str(result)
        except TypeError as e:
            return f"Skill '{skill_name}' called with wrong arguments: {e}"
        except Exception as e:
            logger.error(f"Skill {skill_name} failed: {e}")
            return f"Skill failed: {e}"

    # ==================================================================
    # APP CONTROL
    # ==================================================================

    def open_app(self, name: str) -> str:
        """Launch an application by name."""
        # Map common names to actual commands
        app_map = {
            "firefox": "firefox",
            "chrome": "google-chrome",
            "chromium": "chromium-browser",
            "code": "code",
            "vscode": "code",
            "terminal": "gnome-terminal",
            "files": "nautilus",
            "file manager": "nautilus",
            "nautilus": "nautilus",
            "spotify": "spotify",
            "discord": "discord",
            "slack": "slack",
            "gimp": "gimp",
            "vlc": "vlc",
            "calculator": "gnome-calculator",
            "settings": "gnome-control-center",
            "text editor": "gedit",
            "gedit": "gedit",
            "obs": "obs",
            "steam": "steam",
            "blender": "blender",
            "inkscape": "inkscape",
        }

        cmd = app_map.get(name.lower().strip(), name.lower().strip())

        if not shutil.which(cmd):
            return f"Application '{name}' not found on this system."

        subprocess.Popen(
            [cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"Opened {name}."

    def close_app(self, name: str) -> str:
        """Close an application by name."""
        result = subprocess.run(
            ["pkill", "-f", name],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return f"Closed {name}."
        return f"No running process found for '{name}'."

    def list_running(self) -> str:
        """List running GUI applications."""
        result = subprocess.run(
            ["wmctrl", "-l"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # Fallback without wmctrl
            result = subprocess.run(
                ["ps", "-eo", "pid,comm", "--sort=-%mem"],
                capture_output=True, text=True,
            )
            lines = result.stdout.strip().split("\n")[:20]
            return "Running processes:\n" + "\n".join(lines)
        return "Open windows:\n" + result.stdout.strip()

    def switch_to(self, name: str) -> str:
        """Bring a window to the foreground using xdotool."""
        result = subprocess.run(
            ["xdotool", "search", "--name", name],
            capture_output=True, text=True,
        )
        window_ids = result.stdout.strip().split("\n")
        if not window_ids or not window_ids[0]:
            return f"No window found matching '{name}'."

        subprocess.run(["xdotool", "windowactivate", window_ids[0]])
        return f"Switched to {name}."

    def open_url(self, url: str) -> str:
        """Open a URL in the default browser."""
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return f"Opened {url} in browser."

    def open_file(self, path: str) -> str:
        """Open a file with its default application."""
        p = Path(path).expanduser()
        if not p.exists():
            return f"File not found: {path}"
        subprocess.Popen(
            ["xdg-open", str(p)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return f"Opened {p.name}."

    # ==================================================================
    # SYSTEM INFO
    # ==================================================================

    def cpu_usage(self) -> str:
        """Get current CPU usage."""
        result = subprocess.run(
            ["grep", "cpu ", "/proc/stat"],
            capture_output=True, text=True,
        )
        # Simple CPU calculation from /proc/stat
        try:
            parts = result.stdout.split()
            idle = int(parts[4])
            total = sum(int(x) for x in parts[1:])
            usage = 100.0 * (1 - idle / total)
            return f"CPU usage: {usage:.1f}%"
        except (IndexError, ValueError):
            # Fallback to top
            result = subprocess.run(
                ["top", "-bn1"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.split("\n"):
                if "Cpu" in line:
                    return f"CPU: {line.strip()}"
            return "Could not read CPU usage."

    def ram_usage(self) -> str:
        """Get memory usage statistics."""
        result = subprocess.run(
            ["free", "-h"],
            capture_output=True, text=True,
        )
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            header = lines[0]
            mem = lines[1]
            return f"Memory:\n{header}\n{mem}"
        return result.stdout.strip()

    def disk_usage(self) -> str:
        """Get disk usage for all mounted drives."""
        result = subprocess.run(
            ["df", "-h", "--total", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs"],
            capture_output=True, text=True,
        )
        return "Disk usage:\n" + result.stdout.strip()

    def top_processes(self, n: int = 10) -> str:
        """Show top processes by CPU and RAM usage."""
        n = min(int(n), 25)
        result = subprocess.run(
            ["ps", "aux", "--sort=-%cpu"],
            capture_output=True, text=True,
        )
        lines = result.stdout.strip().split("\n")
        header = lines[0]
        procs = lines[1:n + 1]
        return f"Top {n} processes by CPU:\n{header}\n" + "\n".join(procs)

    def uptime(self) -> str:
        """Get system uptime."""
        result = subprocess.run(
            ["uptime", "-p"],
            capture_output=True, text=True,
        )
        return result.stdout.strip()

    def ip_address(self) -> str:
        """Get local and public IP addresses."""
        # Local IP
        local = subprocess.run(
            ["hostname", "-I"],
            capture_output=True, text=True,
        )
        local_ip = local.stdout.strip().split()[0] if local.stdout.strip() else "unknown"

        # Public IP
        try:
            pub = subprocess.run(
                ["curl", "-s", "--max-time", "5", "https://ifconfig.me"],
                capture_output=True, text=True, timeout=10,
            )
            public_ip = pub.stdout.strip() or "unavailable"
        except subprocess.TimeoutExpired:
            public_ip = "timed out"

        return f"Local IP: {local_ip}\nPublic IP: {public_ip}"

    def battery(self) -> str:
        """Get battery status."""
        bat_path = Path("/sys/class/power_supply/BAT0")
        if not bat_path.exists():
            bat_path = Path("/sys/class/power_supply/BAT1")
        if not bat_path.exists():
            return "No battery detected — probably a desktop."

        try:
            capacity = (bat_path / "capacity").read_text().strip()
            status = (bat_path / "status").read_text().strip()
            return f"Battery: {capacity}% ({status})"
        except Exception:
            return "Could not read battery info."

    def temperature(self) -> str:
        """Get CPU temperature."""
        # Try thermal_zone first
        thermal = Path("/sys/class/thermal/thermal_zone0/temp")
        if thermal.exists():
            try:
                temp_mc = int(thermal.read_text().strip())
                temp_c = temp_mc / 1000.0
                return f"CPU temperature: {temp_c:.1f} C"
            except ValueError:
                pass

        # Fallback to sensors
        result = subprocess.run(
            ["sensors"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return "Temperature sensors not available."

    # ==================================================================
    # PROCESS CONTROL
    # ==================================================================

    def kill_process(self, name: str) -> str:
        """Kill a process by name."""
        result = subprocess.run(
            ["pkill", name],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return f"Killed process: {name}"
        return f"No process found with name '{name}'."

    def kill_pid(self, pid: int) -> str:
        """Kill a process by PID."""
        pid = int(pid)
        result = subprocess.run(
            ["kill", str(pid)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return f"Killed PID {pid}."
        return f"Failed to kill PID {pid}: {result.stderr.strip()}"

    # ==================================================================
    # MEDIA CONTROL (playerctl)
    # ==================================================================

    def play_pause(self) -> str:
        """Toggle media playback."""
        result = subprocess.run(["playerctl", "play-pause"], capture_output=True, text=True)
        return "Toggled play/pause." if result.returncode == 0 else "No media player running."

    def next_track(self) -> str:
        """Skip to next track."""
        result = subprocess.run(["playerctl", "next"], capture_output=True, text=True)
        return "Skipped to next track." if result.returncode == 0 else "No media player running."

    def prev_track(self) -> str:
        """Go to previous track."""
        result = subprocess.run(["playerctl", "previous"], capture_output=True, text=True)
        return "Went to previous track." if result.returncode == 0 else "No media player running."

    def volume_up(self, step: int = 5) -> str:
        """Increase system volume."""
        step = int(step)
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{step}%"])
        return f"Volume up by {step}%."

    def volume_down(self, step: int = 5) -> str:
        """Decrease system volume."""
        step = int(step)
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{step}%"])
        return f"Volume down by {step}%."

    def volume_set(self, pct: int) -> str:
        """Set volume to a specific percentage."""
        pct = max(0, min(150, int(pct)))
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"])
        return f"Volume set to {pct}%."

    def mute(self) -> str:
        """Toggle mute."""
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        return "Toggled mute."

    def now_playing(self) -> str:
        """Get current track info."""
        result = subprocess.run(
            ["playerctl", "metadata", "--format",
             "{{artist}} - {{title}} ({{album}})"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"Now playing: {result.stdout.strip()}"
        return "Nothing is playing right now."

    # ==================================================================
    # DESKTOP CONTROL
    # ==================================================================

    def screenshot(self) -> str:
        """Take a full screenshot."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path.home() / "Pictures" / f"screenshot_{ts}.png"
        path.parent.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            ["scrot", str(path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return f"Screenshot saved to {path}"

        # Fallback: gnome-screenshot
        result = subprocess.run(
            ["gnome-screenshot", "-f", str(path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return f"Screenshot saved to {path}"
        return "Screenshot failed — scrot and gnome-screenshot not available."

    def screenshot_area(self) -> str:
        """Take a screenshot of a selected area."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path.home() / "Pictures" / f"screenshot_area_{ts}.png"
        path.parent.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            ["scrot", "-s", str(path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return f"Screenshot saved to {path}"
        return "Area screenshot failed. Select an area after running the command."

    def clipboard_get(self) -> str:
        """Get clipboard contents."""
        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            content = result.stdout[:500]
            return f"Clipboard: {content}"
        return "Clipboard is empty or xclip not available."

    def clipboard_set(self, text: str) -> str:
        """Set clipboard contents."""
        proc = subprocess.Popen(
            ["xclip", "-selection", "clipboard"],
            stdin=subprocess.PIPE,
        )
        proc.communicate(input=text.encode())
        return "Text copied to clipboard."

    def notify(self, title: str, msg: str = "") -> str:
        """Show a desktop notification."""
        subprocess.run(
            ["notify-send", title, msg],
            capture_output=True,
        )
        return f"Notification sent: {title}"

    def lock_screen(self) -> str:
        """Lock the desktop."""
        # Try multiple lock methods
        for cmd in [
            ["loginctl", "lock-session"],
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
        ]:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                return "Screen locked."
        return "Could not lock screen — no supported locker found."

    def brightness_up(self) -> str:
        """Increase screen brightness."""
        result = subprocess.run(
            ["brightnessctl", "set", "+10%"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return "Brightness increased."
        # Fallback via xrandr
        subprocess.run(
            ["xdotool", "key", "XF86MonBrightnessUp"],
            capture_output=True,
        )
        return "Brightness increase attempted."

    def brightness_down(self) -> str:
        """Decrease screen brightness."""
        result = subprocess.run(
            ["brightnessctl", "set", "10%-"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return "Brightness decreased."
        subprocess.run(
            ["xdotool", "key", "XF86MonBrightnessDown"],
            capture_output=True,
        )
        return "Brightness decrease attempted."

    # ==================================================================
    # FILE OPERATIONS
    # ==================================================================

    def find_file(self, name: str) -> str:
        """Search for files by name in home directory."""
        try:
            result = subprocess.run(
                ["find", str(Path.home()), "-iname", f"*{name}*",
                 "-maxdepth", "5", "-not", "-path", "*/.*"],
                capture_output=True, text=True, timeout=15,
            )
            files = result.stdout.strip().split("\n")
            files = [f for f in files if f][:15]
            if files:
                return "Found files:\n" + "\n".join(files)
            return f"No files found matching '{name}'."
        except subprocess.TimeoutExpired:
            return "Search timed out — try a more specific name."

    def find_recent(self, ext: str = "*", hours: int = 24) -> str:
        """Find recently modified files."""
        hours = int(hours)
        minutes = hours * 60
        cmd = [
            "find", str(Path.home()),
            "-maxdepth", "4",
            "-mmin", f"-{minutes}",
            "-not", "-path", "*/.*",
            "-type", "f",
        ]
        if ext != "*":
            ext = ext.lstrip(".")
            cmd.extend(["-iname", f"*.{ext}"])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
            )
            files = [f for f in result.stdout.strip().split("\n") if f][:20]
            if files:
                return f"Files modified in last {hours}h:\n" + "\n".join(files)
            return f"No files modified in the last {hours} hours."
        except subprocess.TimeoutExpired:
            return "Search timed out."

    def file_size(self, path: str) -> str:
        """Get file size."""
        p = Path(path).expanduser()
        if not p.exists():
            return f"File not found: {path}"
        result = subprocess.run(
            ["du", "-sh", str(p)],
            capture_output=True, text=True,
        )
        return result.stdout.strip()

    def trash(self, path: str) -> str:
        """Move a file to trash."""
        p = Path(path).expanduser()
        if not p.exists():
            return f"File not found: {path}"
        result = subprocess.run(
            ["gio", "trash", str(p)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return f"Moved {p.name} to trash."
        return f"Failed to trash: {result.stderr.strip()}"

    def list_downloads(self) -> str:
        """Show recent downloads."""
        dl_dir = Path.home() / "Downloads"
        if not dl_dir.exists():
            return "No Downloads directory found."

        files = sorted(dl_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
        files = [f for f in files if f.is_file()][:15]
        if not files:
            return "Downloads folder is empty."

        lines = ["Recent downloads:"]
        for f in files:
            size = f.stat().st_size
            if size > 1_000_000:
                sz = f"{size / 1_000_000:.1f} MB"
            elif size > 1_000:
                sz = f"{size / 1_000:.1f} KB"
            else:
                sz = f"{size} B"
            lines.append(f"  {f.name} ({sz})")
        return "\n".join(lines)

    # ==================================================================
    # NETWORK
    # ==================================================================

    def wifi_status(self) -> str:
        """Get connected WiFi network info."""
        result = subprocess.run(
            ["nmcli", "-t", "-f", "active,ssid,signal,security", "device", "wifi"],
            capture_output=True, text=True,
        )
        for line in result.stdout.strip().split("\n"):
            if line.startswith("yes:"):
                parts = line.split(":")
                return f"Connected to: {parts[1]} (signal: {parts[2]}%, security: {parts[3]})"
        return "Not connected to WiFi."

    def wifi_list(self) -> str:
        """List available WiFi networks."""
        result = subprocess.run(
            ["nmcli", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return "Available networks:\n" + result.stdout.strip()
        return "Could not scan WiFi networks."

    def speedtest(self) -> str:
        """Run an internet speed test."""
        if not shutil.which("speedtest-cli") and not shutil.which("speedtest"):
            return "speedtest-cli not installed. Run: pip install speedtest-cli"

        cmd = "speedtest-cli" if shutil.which("speedtest-cli") else "speedtest"
        try:
            result = subprocess.run(
                [cmd, "--simple"],
                capture_output=True, text=True, timeout=60,
            )
            return result.stdout.strip() or "Speed test returned no results."
        except subprocess.TimeoutExpired:
            return "Speed test timed out after 60 seconds."

    def ping(self, host: str = "8.8.8.8") -> str:
        """Ping a host to check connectivity."""
        try:
            result = subprocess.run(
                ["ping", "-c", "4", "-W", "3", host],
                capture_output=True, text=True, timeout=20,
            )
            # Get just the summary line
            lines = result.stdout.strip().split("\n")
            summary = [l for l in lines if "packets" in l or "rtt" in l]
            return f"Ping {host}:\n" + "\n".join(summary) if summary else result.stdout.strip()
        except subprocess.TimeoutExpired:
            return f"Ping to {host} timed out."

    # ==================================================================
    # TIMERS & REMINDERS
    # ==================================================================

    def set_timer(self, minutes: float, label: str = "Timer") -> str:
        """Set a countdown timer with desktop notification."""
        minutes = float(minutes)
        self._timer_id += 1
        timer_id = self._timer_id

        timer_info = {
            "id": timer_id,
            "label": label,
            "minutes": minutes,
            "set_at": datetime.now().isoformat(),
            "active": True,
        }
        self._timers.append(timer_info)

        def _timer_thread():
            time.sleep(minutes * 60)
            if timer_info["active"]:
                timer_info["active"] = False
                subprocess.run(
                    ["notify-send", "-u", "critical", f"Timer: {label}",
                     f"{minutes} minute timer is up!"],
                )
                # Also try to play a sound
                for sound in [
                    "/usr/share/sounds/freedesktop/stereo/complete.oga",
                    "/usr/share/sounds/gnome/default/alerts/glass.ogg",
                ]:
                    if Path(sound).exists():
                        subprocess.run(["paplay", sound], capture_output=True)
                        break
                logger.info(f"Timer '{label}' ({minutes}min) fired")

        t = threading.Thread(target=_timer_thread, daemon=True)
        t.start()

        return f"Timer set: {label} — {minutes} minutes (ID: {timer_id})"

    def set_alarm(self, time_str: str, label: str = "Alarm") -> str:
        """Set an alarm for a specific time (HH:MM format)."""
        try:
            target = datetime.strptime(time_str, "%H:%M").replace(
                year=datetime.now().year,
                month=datetime.now().month,
                day=datetime.now().day,
            )
            now = datetime.now()
            if target <= now:
                # Assume tomorrow
                from datetime import timedelta
                target += timedelta(days=1)

            diff_minutes = (target - now).total_seconds() / 60
            return self.set_timer(diff_minutes, f"Alarm: {label} ({time_str})")
        except ValueError:
            return f"Invalid time format: '{time_str}'. Use HH:MM (24h format)."

    def list_timers(self) -> str:
        """List all active timers."""
        active = [t for t in self._timers if t["active"]]
        if not active:
            return "No active timers."

        lines = ["Active timers:"]
        for t in active:
            lines.append(f"  #{t['id']} — {t['label']} ({t['minutes']}min, set at {t['set_at'][:19]})")
        return "\n".join(lines)

    def cancel_timer(self, timer_id: int) -> str:
        """Cancel a timer by ID."""
        timer_id = int(timer_id)
        for t in self._timers:
            if t["id"] == timer_id and t["active"]:
                t["active"] = False
                return f"Cancelled timer #{timer_id}: {t['label']}"
        return f"No active timer with ID {timer_id}."

    # ==================================================================
    # WEB / SEARCH
    # ==================================================================

    def web_search(self, query: str) -> str:
        """Open a Google search in the default browser."""
        import urllib.parse
        url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return f"Searching Google for: {query}"

    def define(self, word: str) -> str:
        """Look up a word definition using curl + dictionary API."""
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "5",
                 f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"],
                capture_output=True, text=True, timeout=10,
            )
            data = json.loads(result.stdout)
            if isinstance(data, list) and data:
                meanings = data[0].get("meanings", [])
                lines = [f"**{word}**"]
                for m in meanings[:3]:
                    pos = m.get("partOfSpeech", "")
                    defs = m.get("definitions", [])
                    if defs:
                        lines.append(f"  ({pos}) {defs[0].get('definition', '')}")
                return "\n".join(lines)
            return f"No definition found for '{word}'."
        except Exception:
            return f"Could not look up '{word}'."

    def weather(self, location: str = "") -> str:
        """Get current weather from wttr.in."""
        loc = location or ""
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "5", f"https://wttr.in/{loc}?format=3"],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout.strip() or "Could not fetch weather."
        except subprocess.TimeoutExpired:
            return "Weather request timed out."

    # ==================================================================
    # GPU
    # ==================================================================

    def gpu_usage(self) -> str:
        """Get GPU utilization, memory, and temperature."""
        # Try NVIDIA first
        if shutil.which("nvidia-smi"):
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                lines = ["**GPU Status (NVIDIA):**\n"]
                for line in result.stdout.strip().split("\n"):
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 5:
                        lines.append(
                            f"- {parts[0]}: {parts[1]}% utilization, "
                            f"{parts[2]}/{parts[3]} MB VRAM, {parts[4]} C"
                        )
                return "\n".join(lines)

        # Try AMD
        if shutil.which("rocm-smi"):
            result = subprocess.run(
                ["rocm-smi", "--showuse", "--showtemp", "--showmeminfo", "vram"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return "**GPU Status (AMD):**\n\n" + result.stdout.strip()

        # Try generic (lspci + sensors)
        result = subprocess.run(
            ["lspci"],
            capture_output=True, text=True,
        )
        gpu_lines = [l for l in result.stdout.split("\n") if "VGA" in l or "3D" in l]
        if gpu_lines:
            return "GPU detected but no monitoring tool found:\n" + "\n".join(gpu_lines) + \
                   "\n\nInstall nvidia-smi (NVIDIA) or rocm-smi (AMD) for detailed stats."
        return "No GPU detected."

    def gpu_temp(self) -> str:
        """Get GPU temperature only."""
        if shutil.which("nvidia-smi"):
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return f"GPU temperature: {result.stdout.strip()} C"

        if shutil.which("rocm-smi"):
            result = subprocess.run(
                ["rocm-smi", "--showtemp"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()

        return "GPU temperature not available — install nvidia-smi or rocm-smi."

    # ==================================================================
    # CLIPBOARD HISTORY
    # ==================================================================

    def clipboard_history(self) -> str:
        """Show recent clipboard entries."""
        if not self._clipboard_history:
            return "Clipboard history is empty."
        lines = ["**Clipboard History:**\n"]
        for i, entry in enumerate(reversed(self._clipboard_history[:20]), 1):
            ts = entry["timestamp"][11:19]  # HH:MM:SS
            content = entry["content"][:80].replace("\n", " ")
            lines.append(f"{i}. [{ts}] {content}")
        return "\n".join(lines)

    def clipboard_search(self, query: str) -> str:
        """Search clipboard history for a query."""
        query_lower = query.lower()
        matches = [
            e for e in self._clipboard_history
            if query_lower in e["content"].lower()
        ]
        if not matches:
            return f"No clipboard entries matching '{query}'."
        lines = [f"**Clipboard matches for '{query}':**\n"]
        for entry in reversed(matches[-10:]):
            ts = entry["timestamp"][11:19]
            content = entry["content"][:100].replace("\n", " ")
            lines.append(f"[{ts}] {content}")
        return "\n".join(lines)

    # ==================================================================
    # WINDOW MANAGEMENT
    # ==================================================================

    def list_workspaces(self) -> str:
        """List available workspaces."""
        # Try wmctrl
        if shutil.which("wmctrl"):
            result = subprocess.run(
                ["wmctrl", "-d"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                return "Workspaces:\n" + result.stdout.strip()

        # Fallback: xdotool
        if shutil.which("xdotool"):
            result = subprocess.run(
                ["xdotool", "get_num_desktops"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                num = result.stdout.strip()
                current = subprocess.run(
                    ["xdotool", "get_desktop"],
                    capture_output=True, text=True,
                )
                cur = current.stdout.strip() if current.returncode == 0 else "?"
                return f"Workspaces: {num} total, currently on workspace {cur}"
        return "Could not list workspaces — wmctrl or xdotool not available."

    def move_to_workspace(self, n: int) -> str:
        """Switch to workspace N (0-indexed)."""
        n = int(n)
        result = subprocess.run(
            ["xdotool", "set_desktop", str(n)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return f"Switched to workspace {n}."
        # Try wmctrl
        result = subprocess.run(
            ["wmctrl", "-s", str(n)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return f"Switched to workspace {n}."
        return f"Failed to switch to workspace {n}."

    def tile_left(self) -> str:
        """Tile the current window to the left half of the screen."""
        # Use xdotool key combo
        subprocess.run(
            ["xdotool", "key", "super+Left"],
            capture_output=True,
        )
        return "Window tiled to left."

    def tile_right(self) -> str:
        """Tile the current window to the right half of the screen."""
        subprocess.run(
            ["xdotool", "key", "super+Right"],
            capture_output=True,
        )
        return "Window tiled to right."

    def minimize_window(self) -> str:
        """Minimize the current window."""
        result = subprocess.run(
            ["xdotool", "getactivewindow", "windowminimize"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return "Window minimized."
        return "Failed to minimize window."

    def maximize_window(self) -> str:
        """Maximize or restore the current window."""
        subprocess.run(
            ["xdotool", "key", "super+Up"],
            capture_output=True,
        )
        return "Window maximized."

    def close_window(self) -> str:
        """Close the current window."""
        result = subprocess.run(
            ["xdotool", "getactivewindow", "windowclose"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return "Window closed."
        return "Failed to close window."

    # ==================================================================
    # DEV TOOLS
    # ==================================================================

    def git_status(self, path: str = ".") -> str:
        """Get git status for a project."""
        p = Path(path).expanduser()
        if not p.exists():
            return f"Path not found: {path}"
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, cwd=str(p),
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            return f"Git status for {p.name}:\n{output}" if output else f"{p.name}: clean working tree"
        return f"Not a git repository: {path}"

    def npm_run(self, script: str, path: str = ".") -> str:
        """Run an npm script."""
        p = Path(path).expanduser()
        if not (p / "package.json").exists():
            return f"No package.json found in {path}"
        subprocess.Popen(
            ["npm", "run", script],
            cwd=str(p),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"Started `npm run {script}` in {p.name}."

    def pip_install(self, pkg: str) -> str:
        """Install a Python package."""
        result = subprocess.run(
            ["pip", "install", "--break-system-packages", pkg],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            return f"Installed {pkg} successfully."
        return f"Failed to install {pkg}: {result.stderr.strip()[:200]}"

    def port_check(self, port: int) -> str:
        """Check what's using a specific port."""
        port = int(port)
        result = subprocess.run(
            ["ss", "-tlnp", f"sport = :{port}"],
            capture_output=True, text=True,
        )
        output = result.stdout.strip()
        if output and len(output.split("\n")) > 1:
            return f"Port {port}:\n{output}"
        return f"Port {port} is not in use."
