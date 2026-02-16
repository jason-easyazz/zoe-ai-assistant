"""
Channel Base Classes
=====================

Phase 4: Abstract base classes for channel adapters.

All channels implement the same interface, ensuring consistent behavior
across web, WhatsApp, Telegram, Discord, and future channels.

Each channel adapter:
1. Receives raw events from the platform
2. Extracts sender identity
3. Resolves the Zoe user via channel_bindings
4. Passes through Trust Gate for READ/ACT classification
5. Routes to the chat pipeline
"""

import sqlite3
import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")


@dataclass
class ChannelMessage:
    """Normalized message from any channel."""
    channel: str              # "web", "whatsapp", "telegram", "discord"
    external_id: str          # Platform-specific user ID
    user_id: Optional[str]    # Resolved Zoe user ID (from binding)
    session_key: str          # Full session key
    content: str              # Message text
    attachments: List[str]    # URLs of attachments
    raw_event: dict           # Original platform event
    timestamp: str = ""


class ChannelAdapter(ABC):
    """Abstract base class for channel adapters."""

    id: str = ""
    label: str = ""

    @abstractmethod
    async def send_message(
        self,
        session_id: str,
        content: str,
        attachments: list = None,
    ) -> bool:
        """Send a message through this channel."""
        ...

    @abstractmethod
    async def receive_message(self, raw_event: dict) -> ChannelMessage:
        """Parse a raw platform event into a ChannelMessage."""
        ...

    @abstractmethod
    async def get_session_key(self, raw_event: dict) -> str:
        """Derive a session key from a raw event.

        Format: user:{user_id}:channel:{channel_id}:{type}:{identifier}
        """
        ...

    def resolve_user(self, channel: str, external_id: str) -> Optional[str]:
        """Resolve an external ID to a Zoe user via channel_bindings.

        Returns the Zoe user_id or None if no binding exists.
        """
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT user_id FROM channel_bindings
                WHERE channel = ? AND external_id = ? AND verified = 1
            """, (channel, external_id))
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.error(f"Failed to resolve user binding: {e}")
            return None
        finally:
            conn.close()


def init_channels_db():
    """Initialize channel binding tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS channel_bindings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            external_id TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0,
            verification_code TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(channel, external_id)
        );

        CREATE INDEX IF NOT EXISTS idx_channel_bindings_lookup
            ON channel_bindings(channel, external_id, verified);
    """)
    conn.commit()
    conn.close()
    logger.info("Channel bindings DB initialized")


# Initialize on import
init_channels_db()
