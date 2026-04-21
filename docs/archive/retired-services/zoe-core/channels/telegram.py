"""
Telegram Channel Adapter
=========================

Full implementation for Telegram Bot API integration.
Supports sending messages and parsing webhook updates.

Setup:
1. Create a bot via @BotFather or auto-setup via channel orchestrator
2. Store bot token via channel config
3. Configure webhook to /api/channels/telegram/webhook
"""

import httpx
import logging
import os
from typing import Optional
from channels.base import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class TelegramChannelAdapter(ChannelAdapter):
    """Full adapter for Telegram Bot API."""

    id = "telegram"
    label = "Telegram"

    def _get_token(self) -> Optional[str]:
        """Get Telegram bot token from channel config or env."""
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            try:
                from channels.setup_orchestrator import get_channel_config
                config = get_channel_config("telegram")
                if config:
                    token = config.get("bot_token")
            except Exception:
                pass
        return token

    async def send_message(
        self,
        session_id: str,
        content: str,
        attachments: list = None,
    ) -> bool:
        """Send a message via Telegram Bot API.
        
        session_id format: user:{uid}:channel:telegram:dm:{chat_id}
        """
        token = self._get_token()
        if not token:
            logger.error("Telegram: No bot token configured")
            return False

        chat_id = session_id.split(":")[-1] if ":" in session_id else session_id

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{TELEGRAM_API}/bot{token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": content,
                        "parse_mode": "Markdown",
                    },
                )
                result = response.json()
                if result.get("ok"):
                    logger.info(f"Telegram message sent to chat {chat_id}")
                    return True
                else:
                    logger.error(f"Telegram API error: {result.get('description')}")
                    return False
        except Exception as e:
            logger.error(f"Telegram send_message failed: {e}")
            return False

    async def receive_message(self, raw_event: dict) -> ChannelMessage:
        """Parse a Telegram webhook update."""
        message = raw_event.get("message", {})
        chat = message.get("chat", {})
        from_user = message.get("from", {})

        telegram_id = str(from_user.get("id", ""))
        chat_id = str(chat.get("id", ""))
        content = message.get("text", "")

        user_id = self.resolve_user("telegram", telegram_id)

        attachments = self._extract_attachments(message)

        return ChannelMessage(
            channel="telegram",
            external_id=telegram_id,
            user_id=user_id,
            session_key=await self.get_session_key(raw_event),
            content=content,
            attachments=attachments,
            raw_event=raw_event,
        )

    def _extract_attachments(self, message: dict) -> list:
        """Extract attachment references from Telegram message."""
        attachments = []
        if message.get("photo"):
            largest = max(message["photo"], key=lambda p: p.get("file_size", 0))
            attachments.append(f"telegram_file:{largest.get('file_id')}")
        if message.get("document"):
            attachments.append(f"telegram_file:{message['document'].get('file_id')}")
        if message.get("voice"):
            attachments.append(f"telegram_file:{message['voice'].get('file_id')}")
        if message.get("audio"):
            attachments.append(f"telegram_file:{message['audio'].get('file_id')}")
        return attachments

    async def get_session_key(self, raw_event: dict) -> str:
        message = raw_event.get("message", {})
        chat = message.get("chat", {})
        from_user = message.get("from", {})
        telegram_id = str(from_user.get("id", ""))
        chat_id = str(chat.get("id", ""))
        user_id = self.resolve_user("telegram", telegram_id) or "unknown"
        return f"user:{user_id}:channel:telegram:dm:{chat_id}"
