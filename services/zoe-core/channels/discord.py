"""
Discord Channel Adapter
========================

Full implementation for Discord Bot API integration.
Supports sending messages to channels/DMs and parsing incoming events.

Setup:
1. Create a Discord bot at https://discord.com/developers/applications
2. Store bot token via channel setup orchestrator
3. Configure webhook to /api/channels/discord/webhook
"""

import httpx
import logging
import os
from typing import Optional
from channels.base import ChannelAdapter, ChannelMessage

logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"


class DiscordChannelAdapter(ChannelAdapter):
    """Full adapter for Discord Bot integration."""

    id = "discord"
    label = "Discord"

    def _get_token(self) -> Optional[str]:
        """Get Discord bot token from channel config or env."""
        token = os.getenv("DISCORD_BOT_TOKEN")
        if not token:
            try:
                from channels.setup_orchestrator import get_channel_config
                config = get_channel_config("discord")
                if config:
                    token = config.get("bot_token")
            except Exception:
                pass
        return token

    def _get_invite_url(self) -> Optional[str]:
        """Get the bot invite URL for server admins."""
        client_id = os.getenv("DISCORD_CLIENT_ID")
        if not client_id:
            try:
                from channels.setup_orchestrator import get_channel_config
                config = get_channel_config("discord")
                if config:
                    client_id = config.get("client_id")
            except Exception:
                pass
        if client_id:
            perms = 274877975552  # Send Messages, Read History, Embed Links
            return f"https://discord.com/api/oauth2/authorize?client_id={client_id}&permissions={perms}&scope=bot"
        return None

    async def send_message(
        self,
        session_id: str,
        content: str,
        attachments: list = None,
    ) -> bool:
        """Send a message to a Discord channel or DM.
        
        session_id should contain the channel_id to send to.
        Format: user:{uid}:channel:discord:dm:{channel_id}
        """
        token = self._get_token()
        if not token:
            logger.error("Discord: No bot token configured")
            return False

        channel_id = session_id.split(":")[-1] if ":" in session_id else session_id

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{DISCORD_API}/channels/{channel_id}/messages",
                    headers={
                        "Authorization": f"Bot {token}",
                        "Content-Type": "application/json",
                    },
                    json={"content": content},
                )
                response.raise_for_status()
                logger.info(f"Discord message sent to channel {channel_id}")
                return True
        except Exception as e:
            logger.error(f"Discord send_message failed: {e}")
            return False

    async def send_dm(self, user_discord_id: str, content: str) -> bool:
        """Send a direct message to a Discord user."""
        token = self._get_token()
        if not token:
            return False

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                dm_resp = await client.post(
                    f"{DISCORD_API}/users/@me/channels",
                    headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                    json={"recipient_id": user_discord_id},
                )
                dm_resp.raise_for_status()
                dm_channel = dm_resp.json()["id"]

                msg_resp = await client.post(
                    f"{DISCORD_API}/channels/{dm_channel}/messages",
                    headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                    json={"content": content},
                )
                msg_resp.raise_for_status()
                return True
        except Exception as e:
            logger.error(f"Discord DM failed: {e}")
            return False

    async def receive_message(self, raw_event: dict) -> ChannelMessage:
        """Parse a Discord gateway event or webhook payload."""
        if "d" in raw_event:
            data = raw_event["d"]
        else:
            data = raw_event

        author = data.get("author", {})
        discord_id = str(author.get("id", ""))
        channel_id = str(data.get("channel_id", ""))
        content = data.get("content", "")

        user_id = self.resolve_user("discord", discord_id)

        attachments = self._extract_attachments(data)

        return ChannelMessage(
            channel="discord",
            external_id=discord_id,
            user_id=user_id,
            session_key=await self.get_session_key(raw_event),
            content=content,
            attachments=attachments,
            raw_event=raw_event,
        )

    def _extract_attachments(self, data: dict) -> list:
        """Extract attachment URLs from Discord message data."""
        attachments = []
        for att in data.get("attachments", []):
            url = att.get("url") or att.get("proxy_url")
            if url:
                attachments.append(url)
        return attachments

    async def get_session_key(self, raw_event: dict) -> str:
        if "d" in raw_event:
            data = raw_event["d"]
        else:
            data = raw_event

        author = data.get("author", {})
        discord_id = str(author.get("id", ""))
        channel_id = str(data.get("channel_id", ""))
        user_id = self.resolve_user("discord", discord_id) or "unknown"
        return f"user:{user_id}:channel:discord:dm:{channel_id}"
