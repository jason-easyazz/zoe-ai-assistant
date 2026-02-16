"""
Channel Registry
==================

Phase 4: Registry of available channel adapters.
"""

import logging
import secrets
import sqlite3
import os
from typing import Dict, Optional, List, Any

from channels.base import ChannelAdapter, DB_PATH

logger = logging.getLogger(__name__)


class ChannelRegistry:
    """Registry of channel adapters."""

    def __init__(self):
        self._channels: Dict[str, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter):
        """Register a channel adapter."""
        self._channels[adapter.id] = adapter
        logger.info(f"Channel registered: {adapter.id} ({adapter.label})")

    def get(self, channel_id: str) -> Optional[ChannelAdapter]:
        """Get a channel adapter by ID."""
        return self._channels.get(channel_id)

    def list_channels(self) -> List[Dict[str, str]]:
        """List all registered channels."""
        return [
            {"id": c.id, "label": c.label}
            for c in self._channels.values()
        ]

    # ---- Channel Binding Management ----

    @staticmethod
    def generate_verification_code(user_id: str, channel: str) -> str:
        """Generate a 6-digit verification code for channel linking."""
        code = f"{secrets.randbelow(900000) + 100000}"

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO channel_bindings (user_id, channel, external_id, verification_code)
                VALUES (?, ?, 'pending', ?)
            """, (user_id, channel, code))
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to generate verification code: {e}")
            return ""
        finally:
            conn.close()

        return code

    @staticmethod
    def verify_code(channel: str, external_id: str, code: str) -> Optional[str]:
        """Verify a code and create a channel binding.

        Returns the bound Zoe user_id or None if verification fails.
        """
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT user_id FROM channel_bindings
                WHERE channel = ? AND verification_code = ? AND external_id = 'pending'
            """, (channel, code))
            row = cursor.fetchone()

            if not row:
                return None

            user_id = row[0]

            # Update the binding with the actual external_id
            cursor.execute("""
                UPDATE channel_bindings
                SET external_id = ?, verified = 1, verification_code = NULL
                WHERE channel = ? AND verification_code = ?
            """, (external_id, channel, code))
            conn.commit()

            logger.info(f"Channel binding verified: {channel}:{external_id} -> {user_id}")
            return user_id
        except Exception as e:
            logger.error(f"Verification failed: {e}")
            return None
        finally:
            conn.close()

    @staticmethod
    def get_bindings(user_id: str) -> List[Dict[str, Any]]:
        """Get all channel bindings for a user."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT * FROM channel_bindings
                WHERE user_id = ? AND verified = 1
                ORDER BY created_at DESC
            """, (user_id,))
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get bindings: {e}")
            return []
        finally:
            conn.close()

    @staticmethod
    def remove_binding(user_id: str, binding_id: int) -> bool:
        """Remove a channel binding."""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                DELETE FROM channel_bindings WHERE id = ? AND user_id = ?
            """, (binding_id, user_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to remove binding: {e}")
            return False
        finally:
            conn.close()


# Singleton
channel_registry = ChannelRegistry()
