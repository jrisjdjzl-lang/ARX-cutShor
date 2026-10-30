"""Free Notification Dispatcher for ARX-cutShor.

Supports optional zero-cost notification channels:
1. Generic JSON Webhook (e.g. Discord Webhook, Slack Incoming Webhook).
2. Telegram Bot API (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`).
3. Local database operation log fallback (always active).
"""

import json
import urllib.request
from typing import Optional

from arx_cutshor.database import Database
from arx_cutshor.security import scrub_secrets


class NotificationService:
    """Sends operation success/failure notifications without requiring paid services."""

    def __init__(
        self,
        db: Database,
        webhook_url: str = "",
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        dry_run: bool = True,
    ):
        self.db = db
        self.webhook_url = webhook_url.strip()
        self.telegram_bot_token = telegram_bot_token.strip()
        self.telegram_chat_id = telegram_chat_id.strip()
        self.dry_run = dry_run

    def notify(self, title: str, message: str, level: str = "info") -> bool:
        """Dispatch a notification message and record it in operation_logs."""
        safe_msg = scrub_secrets(f"[{level.upper()}] {title}: {message}")
        self.db.log_operation("notification", level, safe_msg)

        if self.dry_run:
            return True

        sent_any = False
        if self.webhook_url:
            sent_any = self._send_webhook(safe_msg) or sent_any

        if self.telegram_bot_token and self.telegram_chat_id:
            sent_any = self._send_telegram(safe_msg) or sent_any

        return sent_any

    def _send_webhook(self, content: str) -> bool:
        try:
            payload = json.dumps({"content": content, "text": content}).encode("utf-8")
            req = urllib.request.Request(
                self.webhook_url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "ARX-cutShor/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def _send_telegram(self, text: str) -> bool:
        try:
            url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
            payload = json.dumps({"chat_id": self.telegram_chat_id, "text": text}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False
