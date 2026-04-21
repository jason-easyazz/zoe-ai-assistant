"""
WhatsApp Channel Adapter
=========================

Full implementation for WhatsApp integration via Twilio.
Supports sending messages and parsing Twilio webhook events.

Setup:
1. Set up Twilio account with WhatsApp sandbox or Business API
2. Store credentials via channel config (account_sid, auth_token, from_number)
3. Configure webhook to /api/channels/whatsapp/webhook
"""

import httpx
import logging
import os
import base64
from typing import Optional
from channels.base import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)


class WhatsAppChannelAdapter(ChannelAdapter):
    """Full adapter for WhatsApp via Twilio."""

    id = "whatsapp"
    label = "WhatsApp"

    def _get_config(self) -> dict:
        """Get WhatsApp/Twilio config from channel config or env."""
        config = {
            "account_sid": os.getenv("TWILIO_ACCOUNT_SID"),
            "auth_token": os.getenv("TWILIO_AUTH_TOKEN"),
            "from_number": os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886"),
        }

        if not config["account_sid"]:
            try:
                from channels.setup_orchestrator import get_channel_config
                stored = get_channel_config("whatsapp")
                if stored:
                    config["account_sid"] = stored.get("account_sid", config["account_sid"])
                    config["auth_token"] = stored.get("auth_token", config["auth_token"])
                    config["from_number"] = stored.get("from_number", config["from_number"])
            except Exception:
                pass

        return config

    async def send_message(
        self,
        session_id: str,
        content: str,
        attachments: list = None,
    ) -> bool:
        """Send a message via Twilio WhatsApp API.
        
        session_id format: user:{uid}:channel:whatsapp:dm:{phone}
        """
        config = self._get_config()
        if not config["account_sid"] or not config["auth_token"]:
            logger.error("WhatsApp: Twilio credentials not configured")
            return False

        phone = session_id.split(":")[-1] if ":" in session_id else session_id
        to_number = f"whatsapp:{phone}" if not phone.startswith("whatsapp:") else phone

        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{config['account_sid']}/Messages.json"
            auth_str = base64.b64encode(
                f"{config['account_sid']}:{config['auth_token']}".encode()
            ).decode()

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Basic {auth_str}"},
                    data={
                        "From": config["from_number"],
                        "To": to_number,
                        "Body": content,
                    },
                )
                response.raise_for_status()
                result = response.json()
                logger.info(f"WhatsApp message sent to {to_number}: SID={result.get('sid')}")
                return True
        except Exception as e:
            logger.error(f"WhatsApp send_message failed: {e}")
            return False

    async def receive_message(self, raw_event: dict) -> ChannelMessage:
        """Parse a Twilio WhatsApp webhook event.

        Twilio sends form-encoded data with fields:
        - From: whatsapp:+1234567890
        - Body: message text
        - NumMedia: number of media attachments
        - MediaUrl0, MediaUrl1, ...: attachment URLs
        """
        phone = raw_event.get("From", "").replace("whatsapp:", "")
        content = raw_event.get("Body", "")

        user_id = self.resolve_user("whatsapp", phone)

        attachments = self._extract_attachments(raw_event)

        return ChannelMessage(
            channel="whatsapp",
            external_id=phone,
            user_id=user_id,
            session_key=await self.get_session_key(raw_event),
            content=content,
            attachments=attachments,
            raw_event=raw_event,
        )

    def _extract_attachments(self, raw_event: dict) -> list:
        """Extract media attachment URLs from Twilio webhook."""
        attachments = []
        num_media = int(raw_event.get("NumMedia", 0))
        for i in range(num_media):
            url = raw_event.get(f"MediaUrl{i}")
            if url:
                attachments.append(url)
        return attachments

    async def get_session_key(self, raw_event: dict) -> str:
        phone = raw_event.get("From", "").replace("whatsapp:", "")
        user_id = self.resolve_user("whatsapp", phone) or "unknown"
        return f"user:{user_id}:channel:whatsapp:dm:{phone}"
