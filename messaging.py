"""
messaging.py — Unified cross-platform message sending for LUMEN.

Platforms:
  whatsapp  — Delegates to existing skills.py WhatsApp automation
  telegram  — Telegram Bot API (sendMessage)
  discord   — Discord Webhook POST
  email     — SMTP with TLS
"""
from __future__ import annotations

import json
from typing import Optional
from urllib import request as urllib_request
from credential_store import CredentialStore

_creds = CredentialStore()


class MessagingHub:
    def send(self, platform: str, recipient: str, message: str) -> str:
        handler = {
            "whatsapp": self._send_whatsapp,
            "telegram": self._send_telegram,
            "discord":  self._send_discord,
            "email":    self._send_email,
        }.get(platform.lower())

        if not handler:
            return f"Unknown messaging platform: {platform}. Supported: whatsapp, telegram, discord, email."
        return handler(recipient, message)

    def _send_whatsapp(self, recipient: str, message: str) -> str:
        from skills import SkillEngine
        engine = SkillEngine()
        return engine.execute("whatsapp", f"{recipient}||{message}")

    def _send_telegram(self, recipient: str, message: str) -> str:
        creds = _creds.get("telegram")
        if not creds.get("bot_token"):
            return ("Telegram credentials not configured. "
                    "Run: python -c \"from credential_store import CredentialStore; "
                    "CredentialStore().set('telegram', {'bot_token': 'YOUR_TOKEN'})\"")
        url = f"https://api.telegram.org/bot{creds['bot_token']}/sendMessage"
        payload = json.dumps({"chat_id": recipient, "text": message}).encode()
        req = urllib_request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib_request.urlopen(req, timeout=10)
        return f"Sent to {recipient} on Telegram."

    def _send_discord(self, recipient: str, message: str) -> str:
        creds = _creds.get("discord")
        if not creds.get("webhook_url"):
            return "Discord webhook not configured."
        payload = json.dumps({"content": f"@{recipient}: {message}"}).encode()
        req = urllib_request.Request(creds["webhook_url"], data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib_request.urlopen(req, timeout=10)
        return "Sent to Discord."

    def _send_email(self, recipient: str, message: str) -> str:
        creds = _creds.get("email")
        if not creds.get("username") or not creds.get("password"):
            return "Email credentials not configured."
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(message)
        msg["Subject"] = "Message from LUMEN"
        msg["From"] = creds["username"]
        msg["To"] = recipient
        with smtplib.SMTP(creds.get("smtp_host", "smtp.gmail.com"),
                          int(creds.get("smtp_port", 587))) as server:
            server.starttls()
            server.login(creds["username"], creds["password"])
            server.send_message(msg)
        return f"Email sent to {recipient}."
