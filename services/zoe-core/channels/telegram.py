"""
Telegram Channel Adapter (Stub)
=================================

Phase 4: Stub interface for future Telegram integration.
Uses the Telegram Bot API.

To implement:
1. Create a Telegram bot via @BotFather
2. Set BOT_TOKEN in environment
3. Configure webhook to /api/channels/telegram/webhook
4. Implement send_message using python-telegram-bot
"""

import logging
from typing import Optional
from channels.base import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)


class TelegramChannelAdapter(ChannelAdapter):
    """Stub adapter for Telegram integration."""

    id = "telegram"
    label = "Telegram"

    async def send_message(
        self,
        session_id: str,
        content: str,
        attachments: list = None,
    ) -> bool:
        """Send a message via Telegram.

        TODO: Implement with Telegram Bot API
        """
        logger.warning("Telegram adapter: send_message not yet implemented")
        return False

    async def receive_message(self, raw_event: dict) -> ChannelMessage:
        """Parse a Telegram webhook event."""
        message = raw_event.get("message", {})
        chat = message.get("chat", {})
        from_user = message.get("from", {})

        telegram_id = str(from_user.get("id", ""))
        user_id = self.resolve_user("telegram", telegram_id)

        return ChannelMessage(
            channel="telegram",
            external_id=telegram_id,
            user_id=user_id,
            session_key=await self.get_session_key(raw_event),
            content=message.get("text", ""),
            attachments=[],
            raw_event=raw_event,
        )

    async def get_session_key(self, raw_event: dict) -> str:
        message = raw_event.get("message", {})
        from_user = message.get("from", {})
        telegram_id = str(from_user.get("id", ""))
        user_id = self.resolve_user("telegram", telegram_id) or "unknown"
        return f"user:{user_id}:channel:telegram:dm:{telegram_id}"
