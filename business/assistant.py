"""
Leon Personal Assistant — Calendar, reminders, daily briefings, life management.

"Morning. You've got 3 leads going cold, a $1,200 invoice due from Marcus,
 and two agents finished overnight. Oh, and it's 78 and sunny — not that
 you'll notice from in here."
"""

import asyncio
import json
import logging
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("leon.business.assistant")


class PersonalAssistant:
    """
    Leon's personal assistant brain.
    Manages calendar, reminders, daily briefings, and life automation.
    """

    def __init__(self, crm, finance, comms, memory, api_client):
        self.crm = crm
        self.finance = finance
        self.comms = comms
        self.memory = memory
        self.api = api_client

        self.data_file = Path("data/assistant.json")
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

        # Load personality config for greetings
        self.personality = {}
        try:
            with open("config/personality.yaml", "r") as f:
                self.personality = yaml.safe_load(f) or {}
        except Exception:
            pass

        logger.info("Personal assistant initialized")

    def _load(self) -> dict:
        if self.data_file.exists():
            try:
                with open(self.data_file) as f:
                    return json.load(f)
            except json.JSONDecodeError:
                pass
        return {
            "reminders": [],
            "calendar": [],
            "notes": [],
            "daily_goals": [],
            "habits": [],
        }

    def save(self):
        """Persist assistant data to disk."""
        with open(self.data_file, "w") as f:
            json.dump(self.data, f, indent=2, default=str)

    # ══════════════════════════════════════════════════════
    # DAILY BRIEFING
    # ══════════════════════════════════════════════════════

    async def generate_daily_briefing(self) -> str:
        """
        Generate a conversational briefing — like Jarvis catching you up.
        """
        now = datetime.now()
        greeting = self._get_greeting(now.hour)

        # Gather all data
        finance_summary = self.finance.get_daily_summary()
        pipeline = self.crm.get_pipeline_summary()
        followups = self.crm.get_leads_needing_followup()
        overdue_invoices = self.finance.get_overdue_invoices()
        today_reminders = self.get_todays_reminders()
        today_calendar = self.get_todays_events()
        active_tasks = self.memory.get_all_active_tasks()
        weather = await self._get_weather()

        # Build a conversational briefing — no walls of emoji headers
        lines = [greeting, ""]

        # Weather — casual, one line
        if weather:
            lines.append(weather)
            lines.append("")

        # Money — straight to the point
        lines.append(f"**Money:** {finance_summary}")

        # Pipeline — conversational summary
        total = pipeline["total_leads"]
        followup_count = pipeline["needs_followup"]
        deals = pipeline["active_deals"]
        value = pipeline["pipeline_value"]
        lines.append(
            f"**Pipeline:** {total} leads in the funnel, {deals} active deals "
            f"worth ${value:,.0f}."
        )

        # Urgent items — flag them clearly but naturally
        if followups or overdue_invoices:
            urgent_parts = []
            if followups:
                urgent_parts.append(f"{len(followups)} leads going cold")
            if overdue_invoices:
                total_overdue = sum(i["total"] for i in overdue_invoices)
                urgent_parts.append(
                    f"{len(overdue_invoices)} overdue invoice{'s' if len(overdue_invoices) > 1 else ''} "
                    f"(${total_overdue:,.0f})"
                )
            lines.append(f"**Needs attention:** {' and '.join(urgent_parts)}.")

        # Calendar — only if there are events
        if today_calendar:
            if len(today_calendar) == 1:
                e = today_calendar[0]
                lines.append(f"**Schedule:** {e['time']} — {e['title']}.")
            else:
                lines.append(f"**Schedule:** {len(today_calendar)} things on the calendar today:")
                for e in today_calendar:
                    lines.append(f"  - {e['time']} — {e['title']}")

        # Reminders
        if today_reminders:
            if len(today_reminders) == 1:
                lines.append(f"**Reminder:** {today_reminders[0]['text']}")
            else:
                lines.append(f"**Reminders:** {len(today_reminders)} for today:")
                for r in today_reminders:
                    lines.append(f"  - {r['text']}")

        # Active agents — casual
        if active_tasks:
            count = len(active_tasks)
            if count == 1:
                task = list(active_tasks.values())[0]
                lines.append(f"**Agents:** One still running — {task['description'][:60]}.")
            else:
                lines.append(f"**Agents:** {count} running in the background:")
                for t in list(active_tasks.values())[:5]:
                    lines.append(f"  - {t['description'][:60]}")

        # Nothing urgent? Say so
        if not followups and not overdue_invoices and not today_calendar and not today_reminders:
            lines.append("")
            lines.append("Nothing urgent. Quiet day.")

        return "\n".join(lines)

    def _get_greeting(self, hour: int) -> str:
        """Pick a time-appropriate greeting from personality config."""
        greetings = self.personality.get("greetings", {})

        if hour < 7:
            options = greetings.get("early_morning", ["You're up early. Here's the rundown."])
        elif hour < 12:
            options = greetings.get("morning", ["Morning. Here's your day."])
        elif hour < 17:
            options = greetings.get("afternoon", ["Afternoon update."])
        elif hour < 21:
            options = greetings.get("evening", ["Evening. Here's the summary."])
        else:
            options = greetings.get("late_night", ["Late one. Here's what you should know."])

        return random.choice(options)

    async def _get_weather(self) -> str:
        """Get current weather using a free API."""
        try:
            import aiohttp
            # Using wttr.in — free, no API key needed
            location = self.memory.get_preference("location", "Tampa")
            url = f"https://wttr.in/{location}?format=%C+%t+%w"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        weather = await resp.text()
                        return f"Weather in {location}: {weather.strip()}"
        except Exception:
            pass
        return ""

    # ══════════════════════════════════════════════════════
    # REMINDERS
    # ══════════════════════════════════════════════════════

    def add_reminder(self, text: str, when: str = None, recurring: str = None) -> dict:
        """
        Add a reminder.

        Args:
            text: What to remind
            when: ISO datetime or relative ("tomorrow", "in 2 hours")
            recurring: None, "daily", "weekly", "monthly"
        """
        reminder = {
            "id": len(self.data["reminders"]) + 1,
            "text": text,
            "when": when or datetime.now().isoformat(),
            "recurring": recurring,
            "created_at": datetime.now().isoformat(),
            "completed": False,
        }
        self.data["reminders"].append(reminder)
        self.save()
        logger.info(f"Reminder added: {text}")
        return reminder

    def get_todays_reminders(self) -> list:
        today = datetime.now().date()
        results = []
        for r in self.data["reminders"]:
            if r["completed"]:
                continue
            try:
                when = datetime.fromisoformat(r["when"]).date()
                if when <= today:
                    results.append(r)
            except (ValueError, TypeError):
                pass
        return results

    def complete_reminder(self, reminder_id: int):
        for r in self.data["reminders"]:
            if r["id"] == reminder_id:
                r["completed"] = True
                self.save()
                return True
        return False

    # ══════════════════════════════════════════════════════
    # CALENDAR
    # ══════════════════════════════════════════════════════

    def add_event(self, title: str, date: str, time: str = "", duration_minutes: int = 60, notes: str = "") -> dict:
        """Add a calendar event."""
        event = {
            "id": len(self.data["calendar"]) + 1,
            "title": title,
            "date": date,
            "time": time,
            "duration_minutes": duration_minutes,
            "notes": notes,
            "created_at": datetime.now().isoformat(),
        }
        self.data["calendar"].append(event)
        self.save()
        logger.info(f"Event added: {title} on {date}")
        return event

    def get_todays_events(self) -> list:
        today = datetime.now().strftime("%Y-%m-%d")
        return sorted(
            [e for e in self.data["calendar"] if e["date"] == today],
            key=lambda x: x.get("time", "00:00"),
        )

    def get_upcoming_events(self, days: int = 7) -> list:
        today = datetime.now().date()
        end = today + timedelta(days=days)
        return [
            e for e in self.data["calendar"]
            if today <= datetime.strptime(e["date"], "%Y-%m-%d").date() <= end
        ]

    # ══════════════════════════════════════════════════════
    # DAILY GOALS
    # ══════════════════════════════════════════════════════

    def set_daily_goals(self, goals: list):
        """Set today's goals."""
        today = datetime.now().strftime("%Y-%m-%d")
        self.data["daily_goals"] = [
            {"text": g, "date": today, "completed": False} for g in goals
        ]
        self.save()

    def complete_goal(self, goal_text: str):
        for g in self.data["daily_goals"]:
            if g["text"].lower() == goal_text.lower():
                g["completed"] = True
                self.save()
                return True
        return False

    # ══════════════════════════════════════════════════════
    # AUTONOMOUS DAILY TASKS
    # ══════════════════════════════════════════════════════

    async def run_daily_automation(self):
        """
        Tasks Leon runs automatically every day:
        1. Check for overdue invoices and send reminders
        2. Follow up with leads that are going cold
        3. Generate daily briefing
        4. Check emails for important messages
        """
        logger.info("Running daily automation...")

        # 1. Invoice reminders
        overdue = self.finance.get_overdue_invoices()
        for inv in overdue:
            if inv.get("client_email"):
                # Auto-generate reminder email
                reminder_body = (
                    f"Hi,\n\nThis is a friendly reminder that invoice {inv['id']} "
                    f"for ${inv['total']:,.2f} is past due.\n\n"
                    f"Please let me know if you have any questions.\n\nBest regards"
                )
                await self.comms.send_email(
                    to=inv["client_email"],
                    subject=f"Reminder: Invoice {inv['id']} Past Due",
                    body=reminder_body,
                )
                logger.info(f"Sent overdue reminder for {inv['id']}")

        # 2. Lead follow-ups
        cold_leads = self.crm.get_leads_needing_followup(days_since_contact=5)
        for lead in cold_leads[:3]:  # Max 3 per day to not be spammy
            if lead.get("email") and lead.get("emails_sent", 0) < 3:
                # Generate follow-up
                from .leads import LeadGenerator
                # Would generate and send follow-up email here
                logger.info(f"Would follow up with: {lead['name']}")

        # 3. Generate briefing
        briefing = await self.generate_daily_briefing()

        # 4. Check emails
        recent_emails = await self.comms.check_emails(limit=5)

        return {
            "briefing": briefing,
            "overdue_reminders_sent": len(overdue),
            "leads_followed_up": min(len(cold_leads), 3),
            "new_emails": len(recent_emails),
        }

    # ══════════════════════════════════════════════════════
    # SMART RESPONSES (answer questions about your life)
    # ══════════════════════════════════════════════════════

    async def answer_question(self, question: str) -> str:
        """
        Answer personal/business questions using all available data.

        "How much did I make this month?" → Finance data
        "Who needs follow up?" → CRM data
        "What's on my schedule?" → Calendar data
        "How's business going?" → Full overview
        """
        q = question.lower()

        # Money questions
        if any(w in q for w in ["revenue", "money", "made", "earn", "income", "profit"]):
            if "today" in q:
                report = self.finance.get_revenue_report("today")
            elif "week" in q:
                report = self.finance.get_revenue_report("week")
            elif "year" in q:
                report = self.finance.get_revenue_report("year")
            else:
                report = self.finance.get_revenue_report("month")

            return (
                f"Revenue ({report['period']}): ${report['revenue']:,.2f}\n"
                f"Expenses: ${report['expenses']:,.2f}\n"
                f"Profit: ${report['profit']:,.2f}\n"
                f"Pending invoices: {report['pending_invoices']} (${report['pending_amount']:,.2f})"
            )

        # Lead/pipeline questions
        if any(w in q for w in ["lead", "prospect", "pipeline", "follow up", "followup", "client"]):
            pipeline = self.crm.get_pipeline_summary()
            followups = self.crm.get_leads_needing_followup()
            hot = self.crm.get_hot_leads()

            response = f"Pipeline: {pipeline['total_leads']} leads, {pipeline['active_deals']} active deals\n"
            if followups:
                response += f"\n{len(followups)} need follow-up:\n"
                for l in followups[:5]:
                    response += f"  • {l['name']} (score: {l.get('lead_score', '?')})\n"
            if hot:
                response += f"\nTop hot leads:\n"
                for l in hot[:3]:
                    response += f"  🔥 {l['name']} — {l.get('notes', '')[:50]}\n"
            return response

        # Schedule questions
        if any(w in q for w in ["schedule", "calendar", "today", "events", "meeting"]):
            events = self.get_todays_events()
            reminders = self.get_todays_reminders()
            if events:
                result = "Today's schedule:\n" + "\n".join(f"  📅 {e['time']} — {e['title']}" for e in events)
            else:
                result = "No events scheduled today."
            if reminders:
                result += "\n\nReminders:\n" + "\n".join(f"  🔔 {r['text']}" for r in reminders)
            return result

        # Business overview
        if any(w in q for w in ["business", "overview", "how's it going", "status", "summary"]):
            return await self.generate_daily_briefing()

        # Fall back to AI
        return None  # Let Leon's brain handle it
