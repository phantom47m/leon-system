"""
Leon System Skills — PC control via natural language

Provides categorized system control functions that Leon can invoke
based on AI-classified user intent. All commands use subprocess with
argument lists (no shell=True) to prevent injection.

Home Assistant skills (ha_set, ha_get, ha_list):
  - Use the Home Assistant REST API (not Tuya Cloud).
  - Require HA_URL and HA_TOKEN environment variables.
  - Designed for HA-managed devices (e.g. ZigBee, Z-Wave, other HA integrations).

See also: tools/lights.py — Tuya Cloud API (tinytuya) for lab ceiling and Geeni devices.
  These are two intentional parallel hardware backends, not duplicates.
  lights.py handles Tuya-direct; system_skills.py HA section handles Home Assistant REST.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("leon.skills")

# Ensure GUI commands have access to the display
def _gui_env() -> dict:
    """Return env dict with DISPLAY/WAYLAND_DISPLAY set for GUI subprocesses."""
    env = os.environ.copy()
    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":1"
    return env


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, returning a failed-like result if the binary is missing."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    except FileNotFoundError:
        tool = cmd[0] if cmd else "unknown"
        fake = subprocess.CompletedProcess(cmd, returncode=127,
                                           stdout="", stderr=f"{tool}: not installed")
        return fake


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
- port_check(port) — Check what's using a port

EXTRA UTILITIES:
- system_info() — Quick system summary (hostname, OS, CPU, RAM, GPU, uptime)
- hostname() — Get system hostname
- date_time() — Current date and time
- volume_get() — Get current volume level
- open_terminal(path) — Open terminal in a directory
- disk_free() — Quick free space check on main drive
- who_am_i() — Current user info

TERMINAL & CODE:
- shell_exec(command) — Run a shell command and return output
- python_exec(code) — Run Python code and return result (15s timeout)
- ocr_screen() — Screenshot the screen and extract all visible text via OCR

SEARCH:
- fast_search(query) — Quick search via DuckDuckGo Instant Answers (no browser)

NOTES:
- note_add(content, title) — Save a persistent note
- note_list(n) — List the most recent N notes
- note_get(note_id) — Get full note content by ID
- note_search(query) — Search notes by content or title
- note_delete(note_id) — Delete a note by ID

HOME ASSISTANT:
- ha_get(entity_id) — Get state of a Home Assistant entity (e.g. light.bedroom)
- ha_set(entity_id, service, data) — Call a HA service (e.g. service=turn_on)
- ha_list(domain) — List HA entities, optionally filtered by domain

TELEGRAM:
- send_telegram(message, to) — Send a Telegram message via OpenClaw"""

    # ------------------------------------------------------------------
    # Execute a skill by name (called by Leon's AI router)
    # ------------------------------------------------------------------

    # Methods that should NOT be callable as skills
    _internal_methods = frozenset({
        "execute", "get_skill_list", "_start_clipboard_monitor",
        "_load_notes", "_save_notes", "_xdotool_or_missing",
    })

    async def execute(self, skill_name: str, args: dict) -> str:
        """Execute a skill by name with given arguments. Returns result string."""
        # Block private/internal methods
        if skill_name.startswith("_") or skill_name in self._internal_methods:
            return f"Unknown skill: {skill_name}"

        method = getattr(self, skill_name, None)
        if not method or not callable(method):
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
        # First try wmctrl to close the window gracefully
        result = _run(["wmctrl", "-c", name])
        if result.returncode == 0:
            return f"Closed {name}."
        # Try exact process name match (safe, won't match unrelated processes)
        result = _run(["pkill", "-x", name])
        if result.returncode == 0:
            return f"Closed {name}."
        # Try matching the app map in reverse to get the binary name
        app_map = getattr(self, '_close_app_map', {
            "firefox": "firefox", "chrome": "chrome", "chromium": "chromium",
            "code": "code", "spotify": "spotify", "discord": "discord",
            "slack": "slack", "gimp": "gimp", "vlc": "vlc", "obs": "obs",
            "steam": "steam", "blender": "blender",
        })
        cmd_name = app_map.get(name.lower().strip())
        if cmd_name and cmd_name != name:
            result = _run(["pkill", "-x", cmd_name])
            if result.returncode == 0:
                return f"Closed {name}."
        return f"No running process found for '{name}'."

    def list_running(self) -> str:
        """List running GUI applications."""
        result = _run(["wmctrl", "-l"])
        if result.returncode == 0:
            return "Open windows:\n" + result.stdout.strip()
        # Fallback without wmctrl
        result = _run(["ps", "-eo", "pid,comm", "--sort=-%mem"])
        lines = result.stdout.strip().split("\n")[:20]
        return "Running processes:\n" + "\n".join(lines)

    def switch_to(self, name: str) -> str:
        """Bring a window to the foreground using xdotool."""
        result = _run(["xdotool", "search", "--name", name])
        if result.returncode == 127:
            return "xdotool not installed. Run: sudo apt install xdotool"
        window_ids = result.stdout.strip().split("\n")
        if not window_ids or not window_ids[0]:
            return f"No window found matching '{name}'."

        _run(["xdotool", "windowactivate", window_ids[0]])
        return f"Switched to {name}."

    def open_url(self, url: str, monitor: int = None, **kwargs) -> str:
        """Open a URL in the default browser, optionally on a specific monitor."""
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if monitor and monitor > 1:
            # Give the browser window a moment to open, then move it
            time.sleep(1.5)
            # Get monitor geometry via xrandr and move the active window there
            xr = _run(["xrandr", "--listmonitors"])
            monitors = []
            for line in xr.stdout.splitlines():
                parts = line.strip().split()
                if parts and parts[0].endswith(":"):
                    # parse geometry like 1920/527x1080/296+1920+0
                    for p in parts:
                        if "x" in p and "+" in p:
                            try:
                                geo = p.split("/")[0] if "/" in p.split("+")[0] else p.split("+")[0]
                                x_off = int(p.split("+")[1])
                                y_off = int(p.split("+")[2])
                                monitors.append((x_off, y_off))
                            except Exception:
                                pass
            idx = monitor - 1
            if idx < len(monitors):
                x, y = monitors[idx]
                _run(["xdotool", "getactivewindow", "windowmove", str(x + 100), str(y + 100)])
                return f"Opened {url} on monitor {monitor}."
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
        # Read /proc/stat directly — no subprocess needed
        try:
            with open("/proc/stat") as f:
                for line in f:
                    if line.startswith("cpu "):
                        parts = line.split()
                        idle = int(parts[4])
                        total = sum(int(x) for x in parts[1:])
                        usage = 100.0 * (1 - idle / total)
                        return f"CPU usage: {usage:.1f}%"
        except (OSError, IndexError, ValueError):
            pass
        # Fallback to top
        try:
            result = _run(["top", "-bn1"], timeout=5)
            for line in result.stdout.split("\n"):
                if "Cpu" in line:
                    return f"CPU: {line.strip()}"
        except subprocess.TimeoutExpired:
            pass
        return "Could not read CPU usage."

    def ram_usage(self) -> str:
        """Get memory usage statistics."""
        result = _run(["free", "-h"])
        if result.returncode == 127:
            return "free not installed."
        lines = result.stdout.strip().split("\n")
        if len(lines) >= 2:
            header = lines[0]
            mem = lines[1]
            return f"Memory:\n{header}\n{mem}"
        return result.stdout.strip()

    def disk_usage(self) -> str:
        """Get disk usage for all mounted drives."""
        result = _run(["df", "-h", "--total", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs"])
        if result.returncode == 127:
            return "df not installed."
        return "Disk usage:\n" + result.stdout.strip()

    def top_processes(self, n: int = 10) -> str:
        """Show top processes by CPU and RAM usage."""
        n = min(int(n), 25)
        result = _run(["ps", "aux", "--sort=-%cpu"])
        if result.returncode == 127:
            return "ps not installed."
        lines = result.stdout.strip().split("\n")
        if not lines:
            return "Could not list processes."
        header = lines[0]
        procs = lines[1:n + 1]
        return f"Top {n} processes by CPU:\n{header}\n" + "\n".join(procs)

    def uptime(self) -> str:
        """Get system uptime."""
        result = _run(["uptime", "-p"])
        if result.returncode == 0:
            return result.stdout.strip()
        # Fallback: read /proc/uptime
        try:
            with open("/proc/uptime") as f:
                secs = float(f.read().split()[0])
            hours = int(secs // 3600)
            mins = int((secs % 3600) // 60)
            return f"up {hours} hours, {mins} minutes"
        except (OSError, ValueError):
            return "Could not read uptime."

    def ip_address(self) -> str:
        """Get local and public IP addresses."""
        # Local IP
        local = _run(["hostname", "-I"])
        local_ip = local.stdout.strip().split()[0] if local.stdout.strip() else "unknown"

        # Public IP
        try:
            pub = _run(["curl", "-s", "--max-time", "5", "https://ifconfig.me"], timeout=10)
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
        result = _run(["sensors"])
        if result.returncode == 0:
            return result.stdout.strip()
        return "Temperature sensors not available — install lm-sensors: sudo apt install lm-sensors"

    # ==================================================================
    # PROCESS CONTROL
    # ==================================================================

    def kill_process(self, name: str) -> str:
        """Kill a process by name."""
        result = _run(["pkill", name])
        if result.returncode == 0:
            return f"Killed process: {name}"
        return f"No process found with name '{name}'."

    def kill_pid(self, pid: int) -> str:
        """Kill a process by PID."""
        pid = int(pid)
        result = _run(["kill", str(pid)])
        if result.returncode == 0:
            return f"Killed PID {pid}."
        return f"Failed to kill PID {pid}: {result.stderr.strip()}"

    # ==================================================================
    # MEDIA CONTROL (playerctl)
    # ==================================================================

    def play_pause(self) -> str:
        """Toggle media playback."""
        result = _run(["playerctl", "play-pause"])
        if result.returncode == 127:
            return "playerctl not installed. Run: sudo apt install playerctl"
        return "Toggled play/pause." if result.returncode == 0 else "No media player running."

    def next_track(self) -> str:
        """Skip to next track."""
        result = _run(["playerctl", "next"])
        if result.returncode == 127:
            return "playerctl not installed. Run: sudo apt install playerctl"
        return "Skipped to next track." if result.returncode == 0 else "No media player running."

    def prev_track(self) -> str:
        """Go to previous track."""
        result = _run(["playerctl", "previous"])
        if result.returncode == 127:
            return "playerctl not installed. Run: sudo apt install playerctl"
        return "Went to previous track." if result.returncode == 0 else "No media player running."

    def volume_up(self, step: int = 5) -> str:
        """Increase system volume."""
        step = int(step)
        result = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{step}%"])
        if result.returncode == 127:
            return "pactl not installed. Run: sudo apt install pulseaudio-utils"
        return f"Volume up by {step}%."

    def volume_down(self, step: int = 5) -> str:
        """Decrease system volume."""
        step = int(step)
        result = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{step}%"])
        if result.returncode == 127:
            return "pactl not installed. Run: sudo apt install pulseaudio-utils"
        return f"Volume down by {step}%."

    def volume_set(self, pct: int) -> str:
        """Set volume to a specific percentage."""
        pct = max(0, min(150, int(pct)))
        result = _run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"])
        if result.returncode == 127:
            return "pactl not installed. Run: sudo apt install pulseaudio-utils"
        return f"Volume set to {pct}%."

    def mute(self) -> str:
        """Toggle mute."""
        result = _run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
        if result.returncode == 127:
            return "pactl not installed. Run: sudo apt install pulseaudio-utils"
        return "Toggled mute."

    def now_playing(self) -> str:
        """Get current track info."""
        result = _run(["playerctl", "metadata", "--format",
                        "{{artist}} - {{title}} ({{album}})"])
        if result.returncode == 127:
            return "playerctl not installed. Run: sudo apt install playerctl"
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

        result = _run(["scrot", str(path)])
        if result.returncode == 0:
            return f"Screenshot saved to {path}"

        # Fallback: gnome-screenshot
        result = _run(["gnome-screenshot", "-f", str(path)])
        if result.returncode == 0:
            return f"Screenshot saved to {path}"
        return "Screenshot failed — install scrot or gnome-screenshot."

    def screenshot_area(self) -> str:
        """Take a screenshot of a selected area."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path.home() / "Pictures" / f"screenshot_area_{ts}.png"
        path.parent.mkdir(parents=True, exist_ok=True)

        result = _run(["scrot", "-s", str(path)])
        if result.returncode == 0:
            return f"Screenshot saved to {path}"
        return "Area screenshot failed — install scrot: sudo apt install scrot"

    def clipboard_get(self) -> str:
        """Get clipboard contents."""
        result = _run(["xclip", "-selection", "clipboard", "-o"])
        if result.returncode == 127:
            return "xclip not installed. Run: sudo apt install xclip"
        if result.returncode == 0:
            content = result.stdout[:500]
            return f"Clipboard: {content}"
        return "Clipboard is empty."

    def clipboard_set(self, text: str) -> str:
        """Set clipboard contents."""
        if not shutil.which("xclip"):
            return "xclip not installed. Run: sudo apt install xclip"
        proc = subprocess.Popen(
            ["xclip", "-selection", "clipboard"],
            stdin=subprocess.PIPE,
        )
        proc.communicate(input=text.encode())
        return "Text copied to clipboard."

    def notify(self, title: str, msg: str = "") -> str:
        """Show a desktop notification."""
        result = _run(["notify-send", title, msg])
        if result.returncode == 127:
            return "notify-send not installed. Run: sudo apt install libnotify-bin"
        return f"Notification sent: {title}"

    def lock_screen(self) -> str:
        """Lock the desktop."""
        # Try multiple lock methods
        for cmd in [
            ["loginctl", "lock-session"],
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
        ]:
            result = _run(cmd)
            if result.returncode == 0:
                return "Screen locked."
        return "Could not lock screen — no supported locker found."

    def brightness_up(self) -> str:
        """Increase screen brightness."""
        result = _run(["brightnessctl", "set", "+10%"])
        if result.returncode == 0:
            return "Brightness increased."
        # Fallback via xdotool
        result = _run(["xdotool", "key", "XF86MonBrightnessUp"])
        if result.returncode == 0:
            return "Brightness increase attempted."
        return "Brightness control not available — install brightnessctl."

    def brightness_down(self) -> str:
        """Decrease screen brightness."""
        result = _run(["brightnessctl", "set", "10%-"])
        if result.returncode == 0:
            return "Brightness decreased."
        result = _run(["xdotool", "key", "XF86MonBrightnessDown"])
        if result.returncode == 0:
            return "Brightness decrease attempted."
        return "Brightness control not available — install brightnessctl."

    # ==================================================================
    # FILE OPERATIONS
    # ==================================================================

    def find_file(self, name: str) -> str:
        """Search for files by name in home directory."""
        try:
            result = _run(
                ["find", str(Path.home()), "-iname", f"*{name}*",
                 "-maxdepth", "5", "-not", "-path", "*/.*"],
                timeout=15,
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
            result = _run(cmd, timeout=15)
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
        result = _run(["du", "-sh", str(p)])
        return result.stdout.strip() or f"Could not determine size of {path}"

    def trash(self, path: str) -> str:
        """Move a file to trash."""
        p = Path(path).expanduser()
        if not p.exists():
            return f"File not found: {path}"
        result = _run(["gio", "trash", str(p)])
        if result.returncode == 127:
            return "gio not installed. Run: sudo apt install glib2.0"
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
        result = _run(["nmcli", "-t", "-f", "active,ssid,signal,security", "device", "wifi"])
        if result.returncode == 127:
            return "nmcli not installed. Run: sudo apt install network-manager"
        for line in result.stdout.strip().split("\n"):
            if line.startswith("yes:"):
                parts = line.split(":")
                if len(parts) >= 4:
                    return f"Connected to: {parts[1]} (signal: {parts[2]}%, security: {parts[3]})"
        # Check if connected via ethernet instead
        eth = _run(["nmcli", "-t", "-f", "TYPE,STATE", "connection", "show", "--active"])
        if eth.returncode == 0 and "ethernet" in eth.stdout.lower():
            return "Not connected to WiFi (using Ethernet)."
        return "Not connected to WiFi."

    def wifi_list(self) -> str:
        """List available WiFi networks."""
        result = _run(["nmcli", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"])
        if result.returncode == 127:
            return "nmcli not installed. Run: sudo apt install network-manager"
        if result.returncode == 0:
            return "Available networks:\n" + result.stdout.strip()
        return "Could not scan WiFi networks."

    def speedtest(self) -> str:
        """Run an internet speed test."""
        if not shutil.which("speedtest-cli") and not shutil.which("speedtest"):
            return "speedtest-cli not installed. Run: pip install speedtest-cli"

        cmd = "speedtest-cli" if shutil.which("speedtest-cli") else "speedtest"
        try:
            result = _run([cmd, "--simple"], timeout=60)
            return result.stdout.strip() or "Speed test returned no results."
        except subprocess.TimeoutExpired:
            return "Speed test timed out after 60 seconds."

    def ping(self, host: str = "8.8.8.8") -> str:
        """Ping a host to check connectivity."""
        try:
            result = _run(["ping", "-c", "4", "-W", "3", host], timeout=20)
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
                _run(["notify-send", "-u", "critical", f"Timer: {label}",
                      f"{minutes} minute timer is up!"])
                # Also try to play a sound
                for sound in [
                    "/usr/share/sounds/freedesktop/stereo/complete.oga",
                    "/usr/share/sounds/gnome/default/alerts/glass.ogg",
                ]:
                    if Path(sound).exists():
                        _run(["paplay", sound])
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
            result = _run(
                ["curl", "-s", "--max-time", "5",
                 f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"],
                timeout=10,
            )
            if result.returncode == 127:
                return "curl not installed."
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
        except (json.JSONDecodeError, subprocess.TimeoutExpired):
            return f"Could not look up '{word}'."

    def weather(self, location: str = "") -> str:
        """Get current weather from wttr.in."""
        loc = location or ""
        try:
            result = _run(
                ["curl", "-s", "--max-time", "5", f"https://wttr.in/{loc}?format=3"],
                timeout=10,
            )
            if result.returncode == 127:
                return "curl not installed."
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
            try:
                result = _run(
                    ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                     "--format=csv,noheader,nounits"],
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                return "nvidia-smi timed out."
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
            try:
                result = _run(
                    ["rocm-smi", "--showuse", "--showtemp", "--showmeminfo", "vram"],
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                return "rocm-smi timed out."
            if result.returncode == 0:
                return "**GPU Status (AMD):**\n\n" + result.stdout.strip()

        # Try generic (lspci + sensors)
        result = _run(["lspci"])
        gpu_lines = [l for l in result.stdout.split("\n") if "VGA" in l or "3D" in l]
        if gpu_lines:
            return "GPU detected but no monitoring tool found:\n" + "\n".join(gpu_lines) + \
                   "\n\nInstall nvidia-smi (NVIDIA) or rocm-smi (AMD) for detailed stats."
        return "No GPU detected."

    def gpu_temp(self) -> str:
        """Get GPU temperature only."""
        if shutil.which("nvidia-smi"):
            try:
                result = _run(
                    ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                return "nvidia-smi timed out."
            if result.returncode == 0:
                return f"GPU temperature: {result.stdout.strip()} C"

        if shutil.which("rocm-smi"):
            try:
                result = _run(["rocm-smi", "--showtemp"], timeout=10)
            except subprocess.TimeoutExpired:
                return "rocm-smi timed out."
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
        result = _run(["wmctrl", "-d"])
        if result.returncode == 0:
            return "Workspaces:\n" + result.stdout.strip()

        # Fallback: xdotool (may return code 1 on Wayland but still output data)
        result = _run(["xdotool", "get_num_desktops"])
        if result.returncode == 127:
            return "Could not list workspaces — install wmctrl or xdotool."
        num = result.stdout.strip()
        if num and num.isdigit():
            current = _run(["xdotool", "get_desktop"])
            cur = current.stdout.strip() if current.stdout.strip().isdigit() else "?"
            return f"Workspaces: {num} total, currently on workspace {cur}"
        return "Could not list workspaces — window manager may not support workspace queries."

    def move_to_workspace(self, n: int) -> str:
        """Switch to workspace N (0-indexed)."""
        n = int(n)
        result = _run(["xdotool", "set_desktop", str(n)])
        if result.returncode == 0:
            return f"Switched to workspace {n}."
        result = _run(["wmctrl", "-s", str(n)])
        if result.returncode == 0:
            return f"Switched to workspace {n}."
        return f"Failed to switch to workspace {n} — install xdotool or wmctrl."

    def _xdotool_or_missing(self, args: list[str], success_msg: str) -> str:
        """Helper for xdotool-based window commands."""
        result = _run(["xdotool"] + args)
        if result.returncode == 127:
            return "xdotool not installed. Run: sudo apt install xdotool"
        if result.returncode == 0:
            return success_msg
        return f"Failed: {result.stderr.strip() or 'xdotool error'}"

    def tile_left(self) -> str:
        """Tile the current window to the left half of the screen."""
        return self._xdotool_or_missing(["key", "super+Left"], "Window tiled to left.")

    def tile_right(self) -> str:
        """Tile the current window to the right half of the screen."""
        return self._xdotool_or_missing(["key", "super+Right"], "Window tiled to right.")

    def minimize_window(self) -> str:
        """Minimize the current window."""
        return self._xdotool_or_missing(
            ["getactivewindow", "windowminimize"], "Window minimized.")

    def maximize_window(self) -> str:
        """Maximize or restore the current window."""
        return self._xdotool_or_missing(["key", "super+Up"], "Window maximized.")

    def close_window(self) -> str:
        """Close the current window."""
        return self._xdotool_or_missing(
            ["getactivewindow", "windowclose"], "Window closed.")

    # ==================================================================
    # DEV TOOLS
    # ==================================================================

    def git_status(self, path: str = ".") -> str:
        """Get git status for a project."""
        p = Path(path).expanduser()
        if not p.exists():
            return f"Path not found: {path}"
        result = _run(["git", "status", "--short"], cwd=str(p))
        if result.returncode == 127:
            return "git not installed."
        if result.returncode == 0:
            output = result.stdout.strip()
            return f"Git status for {p.name}:\n{output}" if output else f"{p.name}: clean working tree"
        return f"Not a git repository: {path}"

    def npm_run(self, script: str, path: str = ".") -> str:
        """Run an npm script."""
        if not shutil.which("npm"):
            return "npm not installed. Install Node.js to get npm."
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
        try:
            result = _run(["pip", "install", "--break-system-packages", pkg], timeout=120)
        except subprocess.TimeoutExpired:
            return f"pip install {pkg} timed out after 120 seconds."
        if result.returncode == 127:
            return "pip not installed. Run: sudo apt install python3-pip"
        if result.returncode == 0:
            return f"Installed {pkg} successfully."
        return f"Failed to install {pkg}: {result.stderr.strip()[:200]}"

    def port_check(self, port: int) -> str:
        """Check what's using a specific port."""
        port = int(port)
        result = _run(["ss", "-tlnp", f"sport = :{port}"])
        if result.returncode == 127:
            return "ss not installed. Run: sudo apt install iproute2"
        output = result.stdout.strip()
        if output and len(output.split("\n")) > 1:
            return f"Port {port}:\n{output}"
        return f"Port {port} is not in use."

    # ==================================================================
    # EXTRA UTILITIES
    # ==================================================================

    def system_info(self) -> str:
        """Quick system summary — hostname, OS, CPU, RAM, GPU, uptime."""
        parts = []
        # Hostname
        import socket
        parts.append(f"Hostname: {socket.gethostname()}")
        # OS
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        parts.append(f"OS: {line.split('=', 1)[1].strip().strip('\"')}")
                        break
        except OSError:
            pass
        # CPU model
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        parts.append(f"CPU: {line.split(':', 1)[1].strip()}")
                        break
        except OSError:
            pass
        # RAM total
        result = _run(["free", "-h", "--si"])
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if line.startswith("Mem:"):
                    total = line.split()[1]
                    parts.append(f"RAM: {total}")
                    break
        # GPU
        if shutil.which("nvidia-smi"):
            r = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
            if r.returncode == 0 and r.stdout.strip():
                parts.append(f"GPU: {r.stdout.strip()}")
        # Uptime
        parts.append(f"Uptime: {self.uptime()}")
        return "\n".join(parts)

    def hostname(self) -> str:
        """Get the system hostname."""
        import socket
        return f"Hostname: {socket.gethostname()}"

    def date_time(self) -> str:
        """Get current date and time."""
        now = datetime.now()
        return now.strftime("Date: %A, %B %d, %Y\nTime: %I:%M %p")

    def volume_get(self) -> str:
        """Get current volume level."""
        result = _run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        if result.returncode == 127:
            return "pactl not installed. Run: sudo apt install pulseaudio-utils"
        if result.returncode == 0:
            # Parse "Volume: front-left: 32768 /  50% / ..."
            for part in result.stdout.split("/"):
                part = part.strip()
                if part.endswith("%"):
                    return f"Volume: {part}"
        return "Could not get volume level."

    def open_terminal(self, path: str = "~") -> str:
        """Open a terminal in a specific directory."""
        p = Path(path).expanduser()
        if not p.is_dir():
            return f"Directory not found: {path}"
        # Try common terminal emulators
        for term_cmd in [
            ["cosmic-term"],
            ["x-terminal-emulator"],
            ["gnome-terminal", f"--working-directory={p}"],
            ["konsole", f"--workdir={p}"],
            ["xfce4-terminal", f"--working-directory={p}"],
            ["xterm", "-e", f"cd {p} && bash"],
        ]:
            if shutil.which(term_cmd[0]):
                subprocess.Popen(
                    term_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return f"Opened terminal in {p}"
        return "No supported terminal emulator found."

    def disk_free(self) -> str:
        """Quick check of free space on the main filesystem."""
        import os
        stat = os.statvfs("/")
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
        total_gb = (stat.f_blocks * stat.f_frsize) / (1024 ** 3)
        used_pct = 100 * (1 - stat.f_bavail / stat.f_blocks)
        return f"Disk: {free_gb:.1f} GB free / {total_gb:.1f} GB total ({used_pct:.0f}% used)"

    def who_am_i(self) -> str:
        """Get current user and groups."""
        result = _run(["id"])
        if result.returncode == 0:
            return result.stdout.strip()
        return f"User: {os.environ.get('USER', 'unknown')}"

    # ==================================================================
    # TERMINAL EXECUTION
    # ==================================================================

    # Patterns that are never allowed regardless of context
    _BLOCKED_PATTERNS = [
        "rm -rf /", "rm -rf ~", "mkfs", "dd if=", "> /dev/sd",
        ":(){ :|:& };:", "chmod -R 777 /", "chown -R",
        "sudo rm", "shred ", "> /etc/",
    ]

    # Shell metacharacters that indicate injection attempts
    _UNSAFE_CHARS = [";", "|", "$(", "`", "&&", "||", ">>", "<<", "<("]

    # Modules that must never be imported in python_exec code
    _BLOCKED_PYTHON_IMPORTS = frozenset({
        "subprocess", "shutil", "ctypes", "socket",
        "urllib", "requests", "multiprocessing", "signal",
        "importlib", "http",
    })

    # Dangerous Python patterns — blocked by substring match
    _BLOCKED_PYTHON_PATTERNS = [
        "os.system",       # Command execution
        "os.popen",        # Command execution
        "os.exec",         # Process replacement (execv, execve, etc.)
        "os.spawn",        # Process spawning
        "os.remove",       # File deletion
        "os.unlink",       # File deletion
        "os.rmdir",        # Directory deletion
        "os.removedirs",   # Recursive directory deletion
        "os.kill",         # Process killing
        "os.fork",         # Process forking
        "__import__",      # Import blocklist bypass
        "open(",           # File I/O — use shell_exec for file operations
        "eval(",           # Code injection bypass
        "exec(",           # Code injection bypass
        "compile(",        # Code compilation bypass
    ]

    # Regex to extract module names from import statements
    _PYTHON_IMPORT_RE = re.compile(r'(?:^|;|\n)\s*(?:import|from)\s+(\w+)')

    def shell_exec(self, command: str) -> str:
        """Run a shell command and return its output (stdout + stderr).

        Uses shlex.split() + shell=False to prevent command injection.
        """
        import shlex

        cmd_lower = command.lower()
        for pattern in self._BLOCKED_PATTERNS:
            if pattern in cmd_lower:
                return f"Blocked: unsafe pattern '{pattern}' in command."
        for char in self._UNSAFE_CHARS:
            if char in command:
                return f"Blocked: shell metacharacter '{char}' not allowed. Use separate commands."
        try:
            args = shlex.split(command)
        except ValueError as e:
            return f"Invalid command syntax: {e}"
        try:
            result = subprocess.run(
                args, shell=False, capture_output=True, text=True,
                timeout=30, env=_gui_env(),
            )
            out = (result.stdout + result.stderr).strip()
            return out[:2000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            return "Command timed out after 30 seconds."
        except FileNotFoundError:
            return f"Command not found: {args[0]}"
        except Exception as e:
            return f"Error: {e}"

    # ==================================================================
    # SCREEN OCR
    # ==================================================================

    def ocr_screen(self) -> str:
        """Take a screenshot of the screen and extract text via OCR."""
        path = "/tmp/leon_ocr_screen.png"
        # Try scrot, then gnome-screenshot
        r = _run(["scrot", path], env=_gui_env())
        if r.returncode != 0:
            r = _run(["gnome-screenshot", "-f", path], env=_gui_env())
        if r.returncode != 0:
            return "Screenshot failed — install scrot: sudo apt install scrot"
        r = _run(["tesseract", path, "stdout", "-l", "eng"])
        if r.returncode == 127:
            return "tesseract not installed. Run: sudo apt install tesseract-ocr"
        text = r.stdout.strip()
        return text[:3000] if text else "No text detected on screen."

    # ==================================================================
    # FAST SEARCH (no browser — DuckDuckGo Instant Answers)
    # ==================================================================

    def fast_search(self, query: str) -> str:
        """Search via DuckDuckGo Instant Answers API — no browser needed."""
        import urllib.parse
        url = (
            "https://api.duckduckgo.com/?q="
            + urllib.parse.quote_plus(query)
            + "&format=json&no_html=1&skip_disambig=1"
        )
        try:
            r = _run(["curl", "-s", "--max-time", "6", "-A", "Leon/1.0", url], timeout=10)
            if r.returncode != 0:
                return "Search request failed."
            data = json.loads(r.stdout)
            parts = []
            answer = data.get("Answer", "").strip()
            abstract = data.get("AbstractText", "").strip()
            abstract_src = data.get("AbstractSource", "").strip()
            if answer:
                parts.append(f"**Answer:** {answer}")
            if abstract:
                src = f" (via {abstract_src})" if abstract_src else ""
                parts.append(f"**Summary{src}:** {abstract[:500]}")
            related = [t for t in data.get("RelatedTopics", []) if isinstance(t, dict) and t.get("Text")][:3]
            for rt in related:
                parts.append(f"• {rt['Text'][:150]}")
            if parts:
                return "\n".join(parts)
            return f"No instant answer for '{query}'. Try: /search {query} to use the browser."
        except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception) as e:
            return f"Search failed: {e}"

    # ==================================================================
    # PERSISTENT NOTES
    # ==================================================================

    _NOTES_FILE = Path.home() / ".leon" / "notes.json"

    def _load_notes(self) -> list:
        self._NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        if self._NOTES_FILE.exists():
            try:
                return json.loads(self._NOTES_FILE.read_text())
            except Exception:
                pass
        return []

    def _save_notes(self, notes: list):
        self._NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._NOTES_FILE.write_text(json.dumps(notes, indent=2))

    def note_add(self, content: str, title: str = "") -> str:
        """Save a note to Leon's persistent notes."""
        notes = self._load_notes()
        note_id = (notes[-1]["id"] + 1) if notes else 1
        notes.append({
            "id": note_id,
            "title": title or content[:50].strip(),
            "content": content,
            "created": datetime.now().isoformat(),
        })
        self._save_notes(notes)
        return f"Note #{note_id} saved."

    def note_list(self, n: int = 10) -> str:
        """List saved notes (most recent first)."""
        notes = self._load_notes()
        if not notes:
            return "No notes saved."
        lines = ["**Notes:**"]
        for note in reversed(notes[-int(n):]):
            lines.append(f"  #{note['id']} [{note['created'][:10]}] {note['title']}")
        return "\n".join(lines)

    def note_get(self, note_id: int) -> str:
        """Get the full content of a note by ID."""
        for note in self._load_notes():
            if note["id"] == int(note_id):
                return f"**#{note['id']} — {note['title']}**\n{note['content']}"
        return f"Note #{note_id} not found."

    def note_search(self, query: str) -> str:
        """Search notes by content or title."""
        q = query.lower()
        matches = [n for n in self._load_notes()
                   if q in n["content"].lower() or q in n["title"].lower()]
        if not matches:
            return f"No notes matching '{query}'."
        lines = [f"**Notes matching '{query}':**"]
        for n in matches[-10:]:
            lines.append(f"  #{n['id']} [{n['created'][:10]}] {n['title']}\n    {n['content'][:150]}")
        return "\n\n".join(lines)

    def note_delete(self, note_id: int) -> str:
        """Delete a note by ID."""
        notes = self._load_notes()
        new_notes = [n for n in notes if n["id"] != int(note_id)]
        if len(new_notes) == len(notes):
            return f"Note #{note_id} not found."
        self._save_notes(new_notes)
        return f"Note #{note_id} deleted."

    # ==================================================================
    # PYTHON CODE EXECUTION
    # ==================================================================

    def python_exec(self, code: str) -> str:
        """Run Python code in a subprocess sandbox (15s timeout).

        Blocks dangerous imports and operations to prevent filesystem
        damage, command execution, and network access.
        """
        # Check for blocked imports
        for match in self._PYTHON_IMPORT_RE.finditer(code):
            module = match.group(1)
            if module in self._BLOCKED_PYTHON_IMPORTS:
                return f"Blocked: import of '{module}' is not allowed in python_exec."

        # Check for dangerous patterns
        for pattern in self._BLOCKED_PYTHON_PATTERNS:
            if pattern in code:
                return f"Blocked: '{pattern}' is not allowed in python_exec."

        try:
            result = subprocess.run(
                ["python3", "-c", code],
                capture_output=True, text=True, timeout=15,
            )
            out = (result.stdout + result.stderr).strip()
            return out[:2000] if out else "(no output)"
        except subprocess.TimeoutExpired:
            return "Code execution timed out after 15 seconds."
        except Exception as e:
            return f"Execution error: {e}"

    # ==================================================================
    # HOME ASSISTANT
    # ==================================================================

    def ha_get(self, entity_id: str) -> str:
        """Get the current state of a Home Assistant entity."""
        ha_url = os.environ.get("HA_URL", "").rstrip("/")
        ha_token = os.environ.get("HA_TOKEN", "")
        if not ha_url or not ha_token:
            return "Home Assistant not configured. Add HA_URL and HA_TOKEN to .env"
        try:
            r = _run(["curl", "-s", "--max-time", "5",
                      "-H", f"Authorization: Bearer {ha_token}",
                      f"{ha_url}/api/states/{entity_id}"], timeout=10)
            if r.returncode != 0:
                return f"HA request failed."
            data = json.loads(r.stdout)
            name = data.get("attributes", {}).get("friendly_name", entity_id)
            state = data.get("state", "unknown")
            attrs = data.get("attributes", {})
            extra = ""
            if "temperature" in attrs:
                extra = f", temp: {attrs['temperature']}"
            elif "brightness" in attrs:
                extra = f", brightness: {attrs['brightness']}"
            return f"{name}: {state}{extra}"
        except Exception as e:
            return f"HA error: {e}"

    def ha_set(self, entity_id: str, service: str, data: dict = None) -> str:
        """Call a Home Assistant service (e.g. entity='light.bedroom', service='turn_on')."""
        ha_url = os.environ.get("HA_URL", "").rstrip("/")
        ha_token = os.environ.get("HA_TOKEN", "")
        if not ha_url or not ha_token:
            return "Home Assistant not configured. Add HA_URL and HA_TOKEN to .env"
        domain = entity_id.split(".")[0] if "." in entity_id else service.split(".")[0]
        payload = json.dumps({"entity_id": entity_id, **(data or {})})
        try:
            r = _run(["curl", "-s", "--max-time", "5", "-X", "POST",
                      "-H", f"Authorization: Bearer {ha_token}",
                      "-H", "Content-Type: application/json",
                      "-d", payload,
                      f"{ha_url}/api/services/{domain}/{service}"], timeout=10)
            if r.returncode == 0:
                return f"Called {domain}.{service} on {entity_id}."
            return f"HA error: {r.stderr.strip()[:100]}"
        except Exception as e:
            return f"HA error: {e}"

    def ha_list(self, domain: str = "") -> str:
        """List Home Assistant entities (optionally filter by domain like 'light', 'switch')."""
        ha_url = os.environ.get("HA_URL", "").rstrip("/")
        ha_token = os.environ.get("HA_TOKEN", "")
        if not ha_url or not ha_token:
            return "Home Assistant not configured. Add HA_URL and HA_TOKEN to .env"
        try:
            r = _run(["curl", "-s", "--max-time", "8",
                      "-H", f"Authorization: Bearer {ha_token}",
                      f"{ha_url}/api/states"], timeout=12)
            entities = json.loads(r.stdout)
            if domain:
                entities = [e for e in entities if e["entity_id"].startswith(domain + ".")]
            lines = []
            for e in entities[:30]:
                name = e.get("attributes", {}).get("friendly_name", e["entity_id"])
                lines.append(f"  {e['entity_id']}: {e['state']} ({name})")
            return "\n".join(lines) if lines else "No entities found."
        except Exception as e:
            return f"HA error: {e}"

    # ==================================================================
    # TELEGRAM (via OpenClaw channels)
    # ==================================================================

    def send_telegram(self, message: str, to: str = "") -> str:
        """Send a Telegram message via OpenClaw's connected Telegram channel."""
        oc = shutil.which("openclaw") or str(Path.home() / ".openclaw" / "bin" / "openclaw")
        if not Path(oc).exists() and not shutil.which("openclaw"):
            return "OpenClaw not found."
        args = [oc, "message", "send", "--channel", "telegram", message]
        if to:
            args += ["--to", to]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return "Telegram message sent."
            err = r.stderr.strip() or r.stdout.strip()
            if "not configured" in err.lower() or "no channel" in err.lower():
                return "Telegram not configured in OpenClaw. Run: openclaw configure"
            return f"Telegram error: {err[:150]}"
        except subprocess.TimeoutExpired:
            return "Telegram send timed out."
        except Exception as e:
            return f"Telegram error: {e}"
