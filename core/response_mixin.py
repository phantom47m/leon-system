"""
Response formatting mixin — personality phrases, error translation, help text,
and skills manifest generation.

Extracted from leon.py (Issue #19) to reduce the main module's size and
centralise all output-formatting concerns.
"""

import logging
import random
import re
from pathlib import Path

logger = logging.getLogger("leon")

# Compiled once at import time
_SIR_RE = re.compile(r',?\s*\bsir\b\.?', re.IGNORECASE)
_MULTI_SPACE_RE = re.compile(r'  +')


class ResponseMixin:
    """Methods for formatting Leon's outgoing responses.

    Relies on attributes set during ``Leon.__init__``:
        self._task_complete_phrases   (list[str])
        self._task_failed_phrases     (list[str])
        self._error_translations      (dict[str, str])
        self.config                   (dict)
        self.printer                  (optional)
        self.vision                   (optional)
    """

    # ------------------------------------------------------------------
    # Post-processing filter
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_sir(text: str) -> str:
        """
        Hard post-processing filter — remove every form of 'sir' the LLM might generate.
        This runs on 100% of outgoing responses. No exceptions.
        """
        text = _SIR_RE.sub('', text)
        text = _MULTI_SPACE_RE.sub(' ', text).strip()
        return text

    # ------------------------------------------------------------------
    # Personality phrases
    # ------------------------------------------------------------------

    def _translate_error(self, raw_error: str) -> str:
        """Turn a raw error string into human-friendly language."""
        error_lower = raw_error.lower()
        for pattern, friendly in self._error_translations.items():
            if pattern.lower() in error_lower:
                return friendly
        # Fallback: truncate and present simply
        short = raw_error[:120].rstrip(".")
        return f"Something went wrong — {short}"

    def _pick_completion_phrase(self, summary: str = "") -> str:
        """Pick a random task completion phrase, optionally with summary."""
        phrase = random.choice(self._task_complete_phrases)
        if "{summary}" in phrase and summary:
            return phrase.replace("{summary}", summary[:100])
        elif "{summary}" in phrase:
            return phrase.replace("{summary}", "").strip()
        return phrase

    def _pick_failure_phrase(self, error: str = "") -> str:
        """Pick a random task failure phrase with translated error."""
        friendly = self._translate_error(error) if error else "unknown issue"
        phrase = random.choice(self._task_failed_phrases)
        return phrase.replace("{error}", friendly)

    # ------------------------------------------------------------------
    # Help text
    # ------------------------------------------------------------------

    def _build_help_text(self) -> str:
        """Build a help text listing all available modules and commands."""
        modules = []
        modules.append("**Available Modules:**\n")
        modules.append("- **Daily Briefing** — \"daily briefing\", \"brief me\", \"catch me up\"")
        modules.append("- **CRM** — \"pipeline\", \"clients\", \"deals\"")
        modules.append("- **Finance** — \"revenue\", \"invoice\", \"earnings\"")
        modules.append("- **Leads** — \"find leads\", \"prospect\", \"generate leads\"")
        modules.append("- **Comms** — \"check email\", \"inbox\", \"send email\"")
        if self.printer:
            modules.append("- **3D Printing** — \"printer status\", \"find stl\"")
        if self.vision:
            modules.append("- **Vision** — \"what do you see\", \"look at\"")
        modules.append("- **Security** — \"audit log\", \"security log\"")
        modules.append("\n**System Skills** (natural language — AI-routed):")
        modules.append("- **App Control** — \"open firefox\", \"close spotify\", \"switch to code\"")
        modules.append("- **System Info** — \"CPU usage\", \"RAM\", \"disk space\", \"top processes\"")
        modules.append("- **Media** — \"pause\", \"next track\", \"volume up\", \"now playing\"")
        modules.append("- **Desktop** — \"screenshot\", \"lock screen\", \"brightness up\"")
        modules.append("- **Files** — \"find file\", \"recent downloads\", \"trash\"")
        modules.append("- **Network** — \"wifi status\", \"my IP\", \"ping google\"")
        modules.append("- **Timers** — \"set timer 5 minutes\", \"set alarm 7:00\"")
        modules.append("- **Web** — \"search for X\", \"weather\", \"define Y\"")
        modules.append("- **Dev** — \"git status\", \"port check 3000\"")
        modules.append("\n**Dashboard Commands** (type / in command bar):")
        modules.append("- `/agents` — list active agents")
        modules.append("- `/status` — system overview")
        modules.append("- `/queue` — queued tasks")
        modules.append("- `/kill <id>` — terminate agent")
        modules.append("- `/retry <id>` — retry failed agent")
        modules.append("- `/history` — recent completed tasks")
        modules.append("- `/bridge` — Right Brain connection status")
        modules.append("- `/plan` — show active plan status")
        modules.append("\n**Plan Mode:**")
        modules.append("- \"plan and build [goal]\" — generate + execute a multi-phase plan")
        modules.append("- \"plan status\" — check plan progress")
        modules.append("- \"cancel plan\" — stop execution (agents finish current task)")
        return "\n".join(modules)

    # ------------------------------------------------------------------
    # Skills manifest (injected into task briefs)
    # ------------------------------------------------------------------

    def _get_skills_manifest(self) -> str:
        """Return a concise tools/skills section to inject into task briefs."""
        oc_bin = Path.home() / ".openclaw" / "bin" / "openclaw"
        skills_dir = Path.home() / ".openclaw" / "workspace" / "skills"
        openclaw_available = oc_bin.exists()
        skill_names = []
        if skills_dir.exists():
            skill_names = sorted(d.name for d in skills_dir.iterdir() if d.is_dir())

        lines = ["## Available Tools\n"]
        lines.append("You have full bash access.")

        if openclaw_available and skill_names:
            # Key skills relevant to coding tasks
            coding_skills = [
                s for s in skill_names
                if any(k in s for k in [
                    "github", "docker", "cloud", "frontend", "debug", "clean-code",
                    "security", "database", "sql", "drizzle", "research", "search",
                    "in-depth", "senior-dev", "spark", "jarvis", "task-dev", "self-improving",
                    "kubernetes", "jenkins", "mlops", "linux",
                ])
            ]
            lines.append(f"You can also leverage these installed OpenClaw skills")
            lines.append(f"({len(skill_names)} total) — invoke via `{oc_bin} agent` or use their expertise")
            lines.append(f"directly since their knowledge is available to you:\n")
            if coding_skills:
                lines.append("**Relevant skills for this task:**")
                for s in coding_skills[:20]:
                    lines.append(f"  - `{s}`")

        lines.append("\n**Always:**")
        lines.append("  - Write tests for any code you add")
        lines.append("  - Commit changes with descriptive git messages")
        lines.append("  - Leave the codebase in a working state\n")
        return "\n".join(lines)
