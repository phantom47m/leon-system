"""
Leon Communications Hub — Email, Discord, WhatsApp, SMS

Unified messaging system. Leon can send and receive messages
across all platforms from a single interface.

"Leon, text Marcus on Discord that I'll be late"
"Leon, email the client the invoice"
"Leon, check my emails"
"""

import asyncio
import json
import logging
import os
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("leon.business.comms")


class CommsHub:
    """
    Unified communications across all platforms.
    """

    def __init__(self):
        # Email config
        self.email_address = os.getenv("EMAIL_ADDRESS", "")
        self.email_password = os.getenv("EMAIL_PASSWORD", "")  # App password
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.imap_host = os.getenv("IMAP_HOST", "imap.gmail.com")

        # Discord config
        self.discord_token = os.getenv("DISCORD_BOT_TOKEN", "")

        # Twilio config (SMS + WhatsApp)
        self.twilio_sid = os.getenv("TWILIO_SID", "")
        self.twilio_token = os.getenv("TWILIO_TOKEN", "")
        self.twilio_phone = os.getenv("TWILIO_PHONE", "")
        self.twilio_whatsapp = os.getenv("TWILIO_WHATSAPP", "")

        # Message log
        self.log_file = Path("data/message_log.json")
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Communications hub initialized")

    # ══════════════════════════════════════════════════════
    # EMAIL
    # ══════════════════════════════════════════════════════

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        attachments: list = None,
    ) -> dict:
        """Send an email."""
        if not self.email_address or not self.email_password:
            return {"success": False, "error": "Email not configured. Set EMAIL_ADDRESS and EMAIL_PASSWORD."}

        try:
            msg = MIMEMultipart("alternative") if html else MIMEMultipart()
            msg["From"] = self.email_address
            msg["To"] = to
            msg["Subject"] = subject

            if html:
                msg.attach(MIMEText(body, "html"))
            else:
                msg.attach(MIMEText(body, "plain"))

            # Attachments
            if attachments:
                for filepath in attachments:
                    path = Path(filepath)
                    if path.exists():
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(path.read_bytes())
                        encoders.encode_base64(part)
                        part.add_header("Content-Disposition", f"attachment; filename={path.name}")
                        msg.attach(part)

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.email_address, self.email_password)
                server.send_message(msg)

            self._log_message("email", "sent", to, subject)
            logger.info(f"Email sent to {to}: {subject}")
            return {"success": True, "to": to, "subject": subject}

        except Exception as e:
            logger.error(f"Email send error: {e}")
            return {"success": False, "error": str(e)}

    async def check_emails(self, folder: str = "INBOX", limit: int = 10) -> list:
        """Check recent emails."""
        if not self.email_address or not self.email_password:
            return []

        try:
            mail = imaplib.IMAP4_SSL(self.imap_host)
            mail.login(self.email_address, self.email_password)
            mail.select(folder)

            _, message_ids = mail.search(None, "ALL")
            ids = message_ids[0].split()[-limit:]  # Last N emails

            emails = []
            for mid in reversed(ids):
                _, msg_data = mail.fetch(mid, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                            break
                else:
                    body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

                emails.append({
                    "from": msg.get("From", ""),
                    "subject": msg.get("Subject", ""),
                    "date": msg.get("Date", ""),
                    "body": body[:500],  # Truncate
                    "read": True,
                })

            mail.close()
            mail.logout()

            logger.info(f"Fetched {len(emails)} emails")
            return emails

        except Exception as e:
            logger.error(f"Email check error: {e}")
            return []

    # ══════════════════════════════════════════════════════
    # DISCORD
    # ══════════════════════════════════════════════════════

    async def send_discord(self, channel_id: str, message: str) -> dict:
        """Send a Discord message."""
        if not self.discord_token:
            return {"success": False, "error": "Discord bot token not configured."}

        try:
            import aiohttp

            url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
            headers = {
                "Authorization": f"Bot {self.discord_token}",
                "Content-Type": "application/json",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json={"content": message}) as resp:
                    if resp.status == 200:
                        self._log_message("discord", "sent", channel_id, message[:50])
                        return {"success": True}
                    else:
                        error = await resp.text()
                        return {"success": False, "error": error}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def send_discord_dm(self, user_id: str, message: str) -> dict:
        """Send a Discord DM to a user."""
        if not self.discord_token:
            return {"success": False, "error": "Discord bot token not configured."}

        try:
            import aiohttp

            # First create a DM channel
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bot {self.discord_token}",
                    "Content-Type": "application/json",
                }

                # Create DM channel
                dm_url = "https://discord.com/api/v10/users/@me/channels"
                async with session.post(dm_url, headers=headers, json={"recipient_id": user_id}) as resp:
                    dm_data = await resp.json()
                    channel_id = dm_data["id"]

                # Send message
                msg_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
                async with session.post(msg_url, headers=headers, json={"content": message}) as resp:
                    if resp.status == 200:
                        self._log_message("discord_dm", "sent", user_id, message[:50])
                        return {"success": True}
                    return {"success": False, "error": await resp.text()}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ══════════════════════════════════════════════════════
    # SMS
    # ══════════════════════════════════════════════════════

    async def send_sms(self, to: str, message: str) -> dict:
        """Send an SMS via Twilio."""
        if not self.twilio_sid:
            return {"success": False, "error": "Twilio not configured."}

        try:
            from twilio.rest import Client
            client = Client(self.twilio_sid, self.twilio_token)

            msg = client.messages.create(
                body=message,
                from_=self.twilio_phone,
                to=to,
            )

            self._log_message("sms", "sent", to, message[:50])
            logger.info(f"SMS sent to {to}")
            return {"success": True, "sid": msg.sid}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ══════════════════════════════════════════════════════
    # WHATSAPP
    # ══════════════════════════════════════════════════════

    async def send_whatsapp(self, to: str, message: str) -> dict:
        """Send WhatsApp message via Twilio."""
        if not self.twilio_sid or not self.twilio_whatsapp:
            return {"success": False, "error": "Twilio WhatsApp not configured."}

        try:
            from twilio.rest import Client
            client = Client(self.twilio_sid, self.twilio_token)

            msg = client.messages.create(
                body=message,
                from_=f"whatsapp:{self.twilio_whatsapp}",
                to=f"whatsapp:{to}",
            )

            self._log_message("whatsapp", "sent", to, message[:50])
            logger.info(f"WhatsApp sent to {to}")
            return {"success": True, "sid": msg.sid}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ══════════════════════════════════════════════════════
    # UNIFIED SEND
    # ══════════════════════════════════════════════════════

    async def send_message(self, platform: str, to: str, message: str, **kwargs) -> dict:
        """
        Send a message on any platform.

        "Leon, send Marcus a message on Discord saying I'll be late"
        → send_message("discord_dm", "marcus_user_id", "Hey, I'll be late")
        """
        platform = platform.lower()

        if platform == "email":
            subject = kwargs.get("subject", "Message from Leon")
            return await self.send_email(to, subject, message)
        elif platform == "discord":
            return await self.send_discord(to, message)
        elif platform == "discord_dm":
            return await self.send_discord_dm(to, message)
        elif platform == "sms":
            return await self.send_sms(to, message)
        elif platform == "whatsapp":
            return await self.send_whatsapp(to, message)
        else:
            return {"success": False, "error": f"Unknown platform: {platform}"}

    # ══════════════════════════════════════════════════════
    # MESSAGE LOG
    # ══════════════════════════════════════════════════════

    def _log_message(self, platform: str, direction: str, recipient: str, preview: str):
        log = []
        if self.log_file.exists():
            try:
                log = json.loads(self.log_file.read_text())
            except json.JSONDecodeError:
                pass

        log.append({
            "platform": platform,
            "direction": direction,
            "recipient": recipient,
            "preview": preview,
            "timestamp": datetime.now().isoformat(),
        })

        # Keep last 500
        log = log[-500:]
        self.log_file.write_text(json.dumps(log, indent=2))

    def get_recent_messages(self, limit: int = 20) -> list:
        if self.log_file.exists():
            try:
                log = json.loads(self.log_file.read_text())
                return log[-limit:]
            except json.JSONDecodeError:
                pass
        return []
