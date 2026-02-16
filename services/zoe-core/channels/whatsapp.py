"""
WhatsApp Channel Adapter (Stub)
================================

Phase 4: Stub interface for future WhatsApp integration.
Uses the Twilio WhatsApp API or WhatsApp Business API.

To implement:
1. Set up Twilio/WhatsApp Business account
2. Configure webhook URL to /api/channels/whatsapp/webhook
3. Implement send_message using Twilio client
4. Complete receive_message parsing
"""

import logging
from typing import Optional
from channels.base import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)


class WhatsAppChannelAdapter(ChannelAdapter):
    """Stub adapter for WhatsApp integration."""

    id = "whatsapp"
    label = "WhatsApp"

    async def send_message(
        self,
        session_id: str,
        content: str,
        attachments: list = None,
    ) -> bool:
        """Send a message via WhatsApp.

        TODO: Implement with Twilio WhatsApp API
        """
        logger.warning("WhatsApp adapter: send_message not yet implemented")
        return False

    async def receive_message(self, raw_event: dict) -> ChannelMessage:
        """Parse a WhatsApp webhook event.

        Expected webhook payload includes:
        - From: The sender's phone number
        - Body: The message text
        """
        phone = raw_event.get("From", "").replace("whatsapp:", "")
        user_id = self.resolve_user("whatsapp", phone)

        return ChannelMessage(
            channel="whatsapp",
            external_id=phone,
            user_id=user_id,
            session_key=await self.get_session_key(raw_event),
            content=raw_event.get("Body", ""),
            attachments=[],
            raw_event=raw_event,
        )

    async def get_session_key(self, raw_event: dict) -> str:
        phone = raw_event.get("From", "").replace("whatsapp:", "")
        user_id = self.resolve_user("whatsapp", phone) or "unknown"
        return f"user:{user_id}:channel:whatsapp:dm:{phone}"
