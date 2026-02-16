"""
Web Channel Adapter
====================

Phase 4: Adapter for the web UI (the primary/default channel).
Web users are authenticated via session tokens, so channel binding
is handled automatically.
"""

import logging
from typing import Optional
from channels.base import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)


class WebChannelAdapter(ChannelAdapter):
    """Adapter for the Zoe web UI."""

    id = "web"
    label = "Zoe Web Interface"

    async def send_message(
        self,
        session_id: str,
        content: str,
        attachments: list = None,
    ) -> bool:
        """Web messages are delivered via the existing SSE/WebSocket stream."""
        # Web UI already receives responses via the chat API
        # This method exists for compatibility with the channel interface
        return True

    async def receive_message(self, raw_event: dict) -> ChannelMessage:
        """Parse a web API request into a ChannelMessage."""
        return ChannelMessage(
            channel="web",
            external_id=raw_event.get("user_id", "unknown"),
            user_id=raw_event.get("user_id"),
            session_key=f"user:{raw_event.get('user_id', 'unknown')}:channel:web:session:{raw_event.get('session_id', 'default')}",
            content=raw_event.get("message", ""),
            attachments=raw_event.get("attachments", []),
            raw_event=raw_event,
        )

    async def get_session_key(self, raw_event: dict) -> str:
        """Web sessions use authenticated session keys directly."""
        user_id = raw_event.get("user_id", "unknown")
        session_id = raw_event.get("session_id", "default")
        return f"user:{user_id}:channel:web:session:{session_id}"
