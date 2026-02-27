"""
Leon Discord Dashboard — live system stats + full server layout.

Creates and maintains a structured Discord server with auto-updating
embeds for tasks/logs and sidebar voice-channel stats.

Layout
------
📊 STATUS         voice-only, sidebar stats visible without clicking
💬 LEON           #chat  #screenshots  #updates
⚙️ TASKS          #active  #dev  #personal  #log
📁 PROJECTS       one channel per project in projects.yaml
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import discord
import psutil
import yaml

logger = logging.getLogger("leon.discord.dashboard")

# ── Visual constants ───────────────────────────────────────────────────────────
BAR_FULL  = "█"
BAR_EMPTY = "░"
BAR_WIDTH = 10

COLOR_OK   = 0x00FF88
COLOR_WARN = 0xFFCC00
COLOR_CRIT = 0xFF4444

# ── Voice stat channels (sidebar-visible without clicking) ────────────────────
# marker is embedded in the name so we can re-find the channel after restart.
# Format: key → (stable_marker, initial_display_name)
_STAT_VOICE: dict[str, tuple[str, str]] = {
    "vc-cpu":    ("▸CPU",    "📊 CPU: Loading…"),
    "vc-ram":    ("▸RAM",    "💾 RAM: Loading…"),
    "vc-disk":   ("▸DISK",   "💿 Disk: Loading…"),
    "vc-net":    ("▸NET",    "🌐 Net: Loading…"),
    "vc-status": ("▸STATUS", "🟢 Status: Loading…"),
    "vc-tasks":  ("▸TASKS",  "⚡ Tasks: Loading…"),
}

# ── Server layout ──────────────────────────────────────────────────────────────
_LAYOUT = [
    {
        "category": "📊 STATUS",
        "read_only": True,
        "channels": [],          # voice channels only — no text channels here
        "has_stat_vcs": True,
    },
    {
        "category": "💬 LEON",
        "read_only": False,
        "channels": ["chat", "screenshots", "updates"],
        "voice_channels": ["🎤 Talk to Leon"],
        "has_stat_vcs": False,
    },
    {
        "category": "⚙️ TASKS",
        "read_only": True,
        "channels": ["active", "dev", "personal", "log"],
        "has_stat_vcs": False,
    },
    {
        "category": "📁 PROJECTS",
        "read_only": True,
        "channels": [],          # populated from projects.yaml at runtime
        "has_stat_vcs": False,
    },
]

# Old layout names — migrated to new names on startup
_CATEGORY_RENAMES = {
    "📊 LEON DASHBOARD": "📊 STATUS",
    "🤖 LEON":           "💬 LEON",
    "⚡ AGENT ZERO":     "⚙️ TASKS",
    "🏗️ PROJECTS":       "📁 PROJECTS",
}

# Old text channels that no longer belong in the layout
_CHANNELS_TO_REMOVE = {"system-stats", "active-tasks", "logs", "jobs", "patches"}

_PLACEHOLDER_EMBED = discord.Embed(
    title="⏳ Initialising…",
    description="First update arriving shortly.",
    color=COLOR_OK,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s).strip("-")
    return s[:100]


def _bar(pct: float) -> str:
    filled = round(BAR_WIDTH * min(pct, 100) / 100)
    return BAR_FULL * filled + BAR_EMPTY * (BAR_WIDTH - filled)


def _color(cpu: float, mem: float, disk: float) -> int:
    worst = max(cpu, mem, disk)
    if worst >= 85:
        return COLOR_CRIT
    if worst >= 65:
        return COLOR_WARN
    return COLOR_OK


def _fmt_uptime(boot_time: float) -> str:
    delta = int(time.time() - boot_time)
    d, rem = divmod(delta, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts: list[str] = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


# ── LeonDashboard ──────────────────────────────────────────────────────────────

class LeonDashboard:
    def __init__(self, client: discord.Client, config_root: str):
        self._client      = client
        self._config_root = Path(config_root)
        self._guild: Optional[discord.Guild] = None
        self._channels: dict[str, discord.TextChannel]  = {}
        self._stat_vcs:  dict[str, discord.VoiceChannel] = {}
        self._pinned_msgs: dict[str, discord.Message]   = {}
        self._updater_task: Optional[asyncio.Task]       = None
        self._setup_failed = False
        self._prev_net: Optional[psutil._common.snetio]  = None
        self._prev_net_ts: float = 0.0
        self._projects: list[dict] = []

    # ── Setup ──────────────────────────────────────────────────────────────────

    async def setup(self, guild: discord.Guild):
        try:
            await self._setup_impl(guild)
        except discord.Forbidden:
            logger.warning("Dashboard: missing Discord permissions — DM-only mode")
            self._setup_failed = True
        except Exception as e:
            logger.warning("Dashboard: setup error — %s", e)
            self._setup_failed = True

    async def _setup_impl(self, guild: discord.Guild):
        self._guild = guild
        self._projects = self._load_projects()

        # ── Migrate old category names ─────────────────────────────────────
        for old_name, new_name in _CATEGORY_RENAMES.items():
            old_cat = discord.utils.get(guild.categories, name=old_name)
            if old_cat and not discord.utils.get(guild.categories, name=new_name):
                await old_cat.edit(name=new_name)
                logger.info("Dashboard: renamed category %r → %r", old_name, new_name)

        # ── Remove stale text channels ─────────────────────────────────────
        for cat in guild.categories:
            for ch in list(cat.text_channels):
                if ch.name in _CHANNELS_TO_REMOVE:
                    try:
                        await ch.delete(reason="Dashboard layout migration")
                        logger.info("Dashboard: removed stale channel #%s", ch.name)
                    except Exception as e:
                        logger.warning("Dashboard: could not remove #%s: %s", ch.name, e)

        everyone = guild.default_role

        for section in _LAYOUT:
            channels = list(section["channels"])
            cat_name = section["category"]

            # Inject project slugs for PROJECTS category
            if cat_name == "📁 PROJECTS":
                channels = [_slugify(p["name"]) for p in self._projects]

            # Find or create category
            category = discord.utils.get(guild.categories, name=cat_name)
            if category is None:
                category = await guild.create_category(cat_name)
                logger.info("Dashboard: created category %r", cat_name)

            # Category-level read-only overwrites
            if section["read_only"]:
                desired = {
                    everyone: discord.PermissionOverwrite(
                        send_messages=False,
                        read_messages=True,
                    )
                }
                if dict(category.overwrites) != desired:
                    await category.edit(overwrites=desired)

            # ── Voice stat channels (STATUS category only) ─────────────────
            if section.get("has_stat_vcs"):
                vc_overwrites = {
                    everyone: discord.PermissionOverwrite(
                        view_channel=True,
                        connect=False,
                    )
                }
                for vc_key, (marker, initial_name) in _STAT_VOICE.items():
                    existing = next(
                        (vc for vc in category.voice_channels if marker in vc.name),
                        None,
                    )
                    if existing is None:
                        existing = await guild.create_voice_channel(
                            initial_name,
                            category=category,
                            overwrites=vc_overwrites,
                        )
                        logger.info("Dashboard: created stat VC %r", initial_name)
                    self._stat_vcs[vc_key] = existing

            # ── Text channels ──────────────────────────────────────────────
            for ch_name in channels:
                ch = discord.utils.get(category.text_channels, name=ch_name)
                if ch is None:
                    ch = await guild.create_text_channel(ch_name, category=category)
                    logger.info("Dashboard: created channel #%s", ch_name)
                self._channels[ch_name] = ch

                # Pin placeholder embeds in auto-updated channels
                if ch_name in ("active", "active-tasks"):
                    await self._ensure_pinned(ch, ch_name)

                # Pin project status embeds
                if cat_name == "📁 PROJECTS":
                    await self._ensure_pinned(ch, ch_name)

            # ── Extra voice channels (e.g. 🎤 Talk to Leon) ────────────────
            for vc_name in section.get("voice_channels", []):
                existing = discord.utils.get(category.voice_channels, name=vc_name)
                if existing is None:
                    await guild.create_voice_channel(vc_name, category=category)
                    logger.info("Dashboard: created voice channel %r", vc_name)

        logger.info(
            "Dashboard ready — %d text channels, %d stat VCs: %s",
            len(self._channels),
            len(self._stat_vcs),
            list(self._channels.keys()),
        )

    async def _ensure_pinned(self, channel: discord.TextChannel, key: str):
        try:
            pins = await channel.pins()
            for msg in pins:
                if msg.author == self._client.user:
                    self._pinned_msgs[key] = msg
                    return
        except Exception:
            pass
        try:
            msg = await channel.send(embed=_PLACEHOLDER_EMBED)
            await msg.pin()
            self._pinned_msgs[key] = msg
        except Exception as e:
            logger.warning("Dashboard: could not pin in #%s: %s", key, e)

    def _load_projects(self) -> list[dict]:
        proj_file = self._config_root / "projects.yaml"
        try:
            data = yaml.safe_load(proj_file.read_text()) or {}
            return data.get("projects", [])
        except Exception as e:
            logger.warning("Dashboard: could not load projects.yaml: %s", e)
            return []

    # ── Updater loop ───────────────────────────────────────────────────────────

    async def start_updater(self):
        if self._setup_failed:
            return
        self._updater_task = asyncio.create_task(self._update_loop())

    async def _update_loop(self):
        tick = 0
        while True:
            try:
                loop = asyncio.get_event_loop()
                metrics = await loop.run_in_executor(None, self._collect_metrics)

                await self._update_active_tasks(metrics)
                # Every 15 min: rename sidebar stat VCs (Discord rate-limits channel PATCH to 2/10min)
                if tick % 15 == 0:
                    await self._update_stat_channels(metrics)
                tick += 1
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("Dashboard update error: %s", e)
            await asyncio.sleep(60)

    # ── Metrics collection (sync, runs in executor) ────────────────────────────

    def _collect_metrics(self) -> dict:
        cpu_pct = psutil.cpu_percent(interval=1)

        temp = 0.0
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                temp = int(f.read().strip()) / 1000
        except Exception:
            try:
                temps = psutil.sensors_temperatures()
                for key in ("coretemp", "cpu_thermal", "k10temp", "acpitz"):
                    if key in temps and temps[key]:
                        temp = temps[key][0].current
                        break
            except Exception:
                pass

        vm = psutil.virtual_memory()
        ram_used_gb  = vm.used  / (1024 ** 3)
        ram_total_gb = vm.total / (1024 ** 3)
        ram_pct      = vm.percent

        du = psutil.disk_usage("/")
        disk_used_gb  = du.used  / (1024 ** 3)
        disk_total_gb = du.total / (1024 ** 3)
        disk_pct      = du.percent

        now = time.monotonic()
        net = psutil.net_io_counters()
        up_mbps = down_mbps = 0.0
        if self._prev_net is not None and (now - self._prev_net_ts) > 0:
            dt = now - self._prev_net_ts
            up_mbps   = (net.bytes_sent - self._prev_net.bytes_sent) / dt / (1024 ** 2)
            down_mbps = (net.bytes_recv - self._prev_net.bytes_recv) / dt / (1024 ** 2)
        self._prev_net    = net
        self._prev_net_ts = now

        uptime_str = _fmt_uptime(psutil.boot_time())

        leon_online = False
        for proc in psutil.process_iter(["cmdline"]):
            try:
                cmdline = " ".join(proc.info["cmdline"] or [])
                if "main.py" in cmdline or (
                    "leon" in cmdline.lower() and "python" in cmdline.lower()
                ):
                    leon_online = True
                    break
            except Exception:
                pass

        try:
            from tools.agent_zero_runner import get_runner
            runner   = get_runner()
            az_count = runner.active_job_count()
            az_status = f"{az_count} active job(s)" if az_count else "Ready"
        except Exception:
            az_count  = 0
            az_status = "Unknown"

        # Count all active tasks from memory
        active_task_count = 0
        try:
            mem_file = Path(__file__).parent.parent.parent / "data" / "leon_memory.json"
            if mem_file.exists():
                data = json.loads(mem_file.read_text())
                active_task_count = len(data.get("active_tasks", {}))
        except Exception:
            pass

        return {
            "cpu_pct":          cpu_pct,
            "temp":             temp,
            "ram_used_gb":      ram_used_gb,
            "ram_total_gb":     ram_total_gb,
            "ram_pct":          ram_pct,
            "disk_used_gb":     disk_used_gb,
            "disk_total_gb":    disk_total_gb,
            "disk_pct":         disk_pct,
            "up_mbps":          max(0.0, up_mbps),
            "down_mbps":        max(0.0, down_mbps),
            "uptime":           uptime_str,
            "leon_online":      leon_online,
            "az_count":         az_count,
            "az_status":        az_status,
            "active_task_count": active_task_count,
        }

    # ── Sidebar voice channel renaming ─────────────────────────────────────────

    async def _update_stat_channels(self, m: dict):
        """Rename sidebar voice channels to show live stats (runs every 5 min)."""
        if not self._stat_vcs:
            return

        leon_icon = "🟢" if m["leon_online"] else "🔴"
        task_count = m["active_task_count"]

        new_names = {
            "vc-cpu":    f"📊 ▸CPU {m['cpu_pct']:.0f}% · {m['temp']:.0f}°C",
            "vc-ram":    f"💾 ▸RAM {m['ram_pct']:.0f}% · {m['ram_used_gb']:.1f}/{m['ram_total_gb']:.0f}GB",
            "vc-disk":   f"💿 ▸DISK {m['disk_pct']:.0f}% · {m['disk_used_gb']:.0f}/{m['disk_total_gb']:.0f}GB",
            "vc-net":    f"🌐 ▸NET ↑{m['up_mbps']:.1f} ↓{m['down_mbps']:.1f} MB/s",
            "vc-status": f"{leon_icon} ▸STATUS Leon · {m['uptime']}",
            "vc-tasks":  f"⚡ ▸TASKS {task_count} Active" if task_count else "⚡ ▸TASKS Idle",
        }

        for key, vc in self._stat_vcs.items():
            name = new_names.get(key)
            if not name or vc.name == name:
                continue
            try:
                await vc.edit(name=name)
            except discord.HTTPException as e:
                logger.debug("Stat VC rename skipped (%s): %s", key, e)
            except Exception as e:
                logger.debug("Stat VC rename error (%s): %s", key, e)

    # ── Active tasks embed ─────────────────────────────────────────────────────

    async def _update_active_tasks(self, metrics: dict):
        ch = self._channels.get("active")
        if not ch:
            return

        tasks_data: dict = {}
        try:
            mem_file = Path(__file__).parent.parent.parent / "data" / "leon_memory.json"
            if mem_file.exists():
                data = json.loads(mem_file.read_text())
                tasks_data = data.get("active_tasks", {})
        except Exception as e:
            logger.debug("Could not read leon_memory.json: %s", e)

        task_list = list(tasks_data.items())[:15]
        color = COLOR_WARN if task_list else COLOR_OK
        embed = discord.Embed(
            title="⚡ Active Tasks",
            color=color,
            timestamp=datetime.utcnow(),
        )

        if task_list:
            for tid, tdata in task_list:
                if isinstance(tdata, dict):
                    desc   = tdata.get("task", tdata.get("description", str(tdata)))[:80]
                    status = tdata.get("status", "running")
                else:
                    desc   = str(tdata)[:80]
                    status = "running"
                embed.add_field(
                    name=f"`{tid[:16]}`",
                    value=f"{status}: {desc}",
                    inline=False,
                )
        else:
            embed.description = "No active tasks — Leon is idle."

        embed.set_footer(
            text=(
                f"CPU {metrics['cpu_pct']:.0f}% · "
                f"RAM {metrics['ram_pct']:.0f}% · "
                f"Updated {datetime.now().strftime('%H:%M:%S')}"
            )
        )
        await self._edit_pinned(ch, "active", embed)

    # ── Pinned message helpers ─────────────────────────────────────────────────

    async def _edit_pinned(
        self,
        channel: discord.TextChannel,
        key: str,
        embed: discord.Embed,
    ):
        msg = self._pinned_msgs.get(key)
        if msg:
            try:
                await msg.edit(embed=embed)
                return
            except discord.NotFound:
                self._pinned_msgs.pop(key, None)
            except discord.Forbidden:
                logger.warning("Dashboard: no permission to edit pinned in #%s", key)
                return
            except Exception as e:
                logger.warning("Dashboard: edit_pinned error in #%s: %s", key, e)

        try:
            new_msg = await channel.send(embed=embed)
            await new_msg.pin()
            self._pinned_msgs[key] = new_msg
        except discord.Forbidden:
            logger.warning("Dashboard: no permission to send/pin in #%s", key)
        except Exception as e:
            logger.warning("Dashboard: recreate_pinned error in #%s: %s", key, e)

    # ── Public posting methods ─────────────────────────────────────────────────

    async def post_screenshot(self, path: str, caption: str = ""):
        ch = self._channels.get("screenshots")
        if not ch:
            return
        try:
            await ch.send(
                content=caption or None,
                file=discord.File(path, filename="screenshot.png"),
            )
        except Exception as e:
            logger.warning("Dashboard: post_screenshot failed: %s", e)

    async def post_to_dev(self, text: str, embed: Optional[discord.Embed] = None):
        """Agent Zero job notifications — start, progress, diffs."""
        ch = self._channels.get("dev")
        if not ch:
            return
        try:
            await ch.send(content=text[:2000], embed=embed)
        except Exception as e:
            logger.warning("Dashboard: post_to_dev failed: %s", e)

    async def post_to_personal(self, text: str, embed: Optional[discord.Embed] = None):
        """OpenClaw tasks — PC control, lights, NPC, hardware."""
        ch = self._channels.get("personal")
        if not ch:
            return
        try:
            await ch.send(content=text[:2000], embed=embed)
        except Exception as e:
            logger.warning("Dashboard: post_to_personal failed: %s", e)

    async def post_to_log(self, text: str):
        """Autonomous action log — one-liner per action Leon took unprompted."""
        ch = self._channels.get("log")
        if not ch:
            return
        try:
            ts = datetime.now().strftime("%H:%M")
            await ch.send(f"`{ts}` {text[:1900]}")
        except Exception as e:
            logger.warning("Dashboard: post_to_log failed: %s", e)

    async def post_to_updates(self, text: str, embed: Optional[discord.Embed] = None):
        """Leon's proactive messages — morning briefs, alerts, reports."""
        ch = self._channels.get("updates")
        if not ch:
            return
        try:
            await ch.send(content=text[:2000], embed=embed)
        except Exception as e:
            logger.warning("Dashboard: post_to_updates failed: %s", e)

    async def update_project_status(
        self,
        project_name: str,
        status: str,
        details: str = "",
    ):
        slug = _slugify(project_name)
        ch = self._channels.get(slug)
        if not ch:
            return
        embed = discord.Embed(
            title=f"📁 {project_name}",
            description=(f"**Status:** {status}\n{details}")[:4096],
            color=COLOR_OK,
            timestamp=datetime.utcnow(),
        )
        await self._edit_pinned(ch, slug, embed)

    # ── Backwards-compat aliases (keep agent_zero_runner working) ──────────────

    async def post_to_jobs(self, text: str, embed: Optional[discord.Embed] = None):
        await self.post_to_dev(text, embed)

    async def post_to_patches(self, text: str, embed: Optional[discord.Embed] = None):
        await self.post_to_dev(text, embed)


# ── Module singleton ───────────────────────────────────────────────────────────

_dashboard: Optional[LeonDashboard] = None


def get_dashboard() -> Optional[LeonDashboard]:
    return _dashboard


def init_dashboard(client: discord.Client, config_root: str) -> LeonDashboard:
    global _dashboard
    _dashboard = LeonDashboard(client, config_root)
    return _dashboard
