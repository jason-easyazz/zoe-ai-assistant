"""
Household Manager
=================

Manages households, members, and per-user music state.
"""

import os
import asyncio
import logging
import json
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)


@dataclass
class HouseholdMember:
    """A member of a household."""
    user_id: str
    household_id: str
    role: str  # owner, admin, member, child
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    content_filter: str = "off"
    time_limits: Optional[dict] = None
    joined_at: Optional[datetime] = None


@dataclass
class Household:
    """A household grouping users."""
    id: str
    name: str
    owner_id: str
    settings: dict
    members: List[HouseholdMember]
    created_at: Optional[datetime] = None


class HouseholdManager:
    """
    Manages household structures and membership.
    
    A household groups multiple users together for shared music experiences:
    - Shared playlists
    - Family mix generation
    - Device sharing
    - Parental controls
    
    Usage:
        manager = HouseholdManager(db_path)
        await manager.init()
        
        # Create household
        household = await manager.create_household("Smith Family", owner_id="user123")
        
        # Add members
        await manager.add_member(household.id, "user456", role="member")
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """Initialize household manager."""
        self.db_path = db_path or os.getenv("ZOE_DB_PATH", "/app/data/zoe.db")
        self._conn = None
        self._initialized = False
    
    async def init(self) -> None:
        """Initialize database connection and schema."""
        if self._initialized:
            return
        
        import aiosqlite
        
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        
        # Run schema
        schema_path = os.path.join(
            os.path.dirname(__file__),
            "../../db/schema/household.sql"
        )
        
        if os.path.exists(schema_path):
            with open(schema_path, 'r') as f:
                schema = f.read()
            await self._conn.executescript(schema)
            await self._conn.commit()
        
        self._initialized = True
        logger.info("HouseholdManager initialized")
    
    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            self._initialized = False
    
    # ========================================
    # Household CRUD
    # ========================================
    
    async def create_household(
        self,
        name: str,
        owner_id: str,
        settings: Optional[dict] = None
    ) -> Household:
        """
        Create a new household.
        
        Args:
            name: Household name (e.g., "Smith Family")
            owner_id: User ID of the household owner
            settings: Optional settings dict
        
        Returns:
            Created Household object
        """
        household_id = str(uuid.uuid4())
        settings = settings or {}
        
        await self._conn.execute(
            """
            INSERT INTO households (id, name, owner_id, settings)
            VALUES (?, ?, ?, ?)
            """,
            (household_id, name, owner_id, json.dumps(settings))
        )
        
        # Add owner as member
        await self._conn.execute(
            """
            INSERT INTO household_members (household_id, user_id, role, display_name)
            VALUES (?, ?, 'owner', ?)
            """,
            (household_id, owner_id, name.split()[0] if name else "Owner")
        )
        
        await self._conn.commit()
        
        logger.info(f"Created household: {name} ({household_id})")
        
        return Household(
            id=household_id,
            name=name,
            owner_id=owner_id,
            settings=settings,
            members=[HouseholdMember(
                user_id=owner_id,
                household_id=household_id,
                role="owner"
            )]
        )
    
    async def get_household(self, household_id: str) -> Optional[Household]:
        """Get a household by ID."""
        cursor = await self._conn.execute(
            "SELECT * FROM households WHERE id = ?",
            (household_id,)
        )
        row = await cursor.fetchone()
        
        if not row:
            return None
        
        # Get members
        members = await self.get_members(household_id)
        
        return Household(
            id=row["id"],
            name=row["name"],
            owner_id=row["owner_id"],
            settings=json.loads(row["settings"] or "{}"),
            members=members,
            created_at=row["created_at"]
        )
    
    async def get_user_households(self, user_id: str) -> List[Household]:
        """Get all households a user belongs to."""
        cursor = await self._conn.execute(
            """
            SELECT h.* FROM households h
            JOIN household_members hm ON h.id = hm.household_id
            WHERE hm.user_id = ?
            """,
            (user_id,)
        )
        rows = await cursor.fetchall()
        
        households = []
        for row in rows:
            members = await self.get_members(row["id"])
            households.append(Household(
                id=row["id"],
                name=row["name"],
                owner_id=row["owner_id"],
                settings=json.loads(row["settings"] or "{}"),
                members=members,
                created_at=row["created_at"]
            ))
        
        return households
    
    async def update_household(
        self,
        household_id: str,
        name: Optional[str] = None,
        settings: Optional[dict] = None
    ) -> bool:
        """Update household details."""
        updates = []
        params = []
        
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        
        if settings is not None:
            updates.append("settings = ?")
            params.append(json.dumps(settings))
        
        if not updates:
            return False
        
        params.append(household_id)
        
        await self._conn.execute(
            f"UPDATE households SET {', '.join(updates)} WHERE id = ?",
            params
        )
        await self._conn.commit()
        
        return True
    
    async def delete_household(self, household_id: str) -> bool:
        """Delete a household and all related data."""
        await self._conn.execute(
            "DELETE FROM households WHERE id = ?",
            (household_id,)
        )
        await self._conn.commit()
        
        logger.info(f"Deleted household: {household_id}")
        return True
    
    # ========================================
    # Member Management
    # ========================================
    
    async def add_member(
        self,
        household_id: str,
        user_id: str,
        role: str = "member",
        display_name: Optional[str] = None,
        content_filter: str = "off"
    ) -> HouseholdMember:
        """Add a member to a household."""
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO household_members 
            (household_id, user_id, role, display_name, content_filter)
            VALUES (?, ?, ?, ?, ?)
            """,
            (household_id, user_id, role, display_name, content_filter)
        )
        await self._conn.commit()
        
        logger.info(f"Added member {user_id} to household {household_id}")
        
        return HouseholdMember(
            user_id=user_id,
            household_id=household_id,
            role=role,
            display_name=display_name,
            content_filter=content_filter
        )
    
    async def remove_member(self, household_id: str, user_id: str) -> bool:
        """Remove a member from a household."""
        # Don't allow removing the owner
        cursor = await self._conn.execute(
            "SELECT owner_id FROM households WHERE id = ?",
            (household_id,)
        )
        row = await cursor.fetchone()
        
        if row and row["owner_id"] == user_id:
            logger.warning(f"Cannot remove owner from household")
            return False
        
        await self._conn.execute(
            "DELETE FROM household_members WHERE household_id = ? AND user_id = ?",
            (household_id, user_id)
        )
        await self._conn.commit()
        
        logger.info(f"Removed member {user_id} from household {household_id}")
        return True
    
    async def get_members(self, household_id: str) -> List[HouseholdMember]:
        """Get all members of a household."""
        cursor = await self._conn.execute(
            "SELECT * FROM household_members WHERE household_id = ?",
            (household_id,)
        )
        rows = await cursor.fetchall()
        
        return [
            HouseholdMember(
                user_id=row["user_id"],
                household_id=row["household_id"],
                role=row["role"],
                display_name=row["display_name"],
                avatar_url=row["avatar_url"],
                content_filter=row["content_filter"],
                time_limits=json.loads(row["time_limits"] or "{}") if row["time_limits"] else None,
                joined_at=row["joined_at"]
            )
            for row in rows
        ]
    
    async def update_member(
        self,
        household_id: str,
        user_id: str,
        role: Optional[str] = None,
        display_name: Optional[str] = None,
        content_filter: Optional[str] = None,
        time_limits: Optional[dict] = None
    ) -> bool:
        """Update a household member's settings."""
        updates = []
        params = []
        
        if role is not None:
            updates.append("role = ?")
            params.append(role)
        
        if display_name is not None:
            updates.append("display_name = ?")
            params.append(display_name)
        
        if content_filter is not None:
            updates.append("content_filter = ?")
            params.append(content_filter)
        
        if time_limits is not None:
            updates.append("time_limits = ?")
            params.append(json.dumps(time_limits))
        
        if not updates:
            return False
        
        params.extend([household_id, user_id])
        
        await self._conn.execute(
            f"""
            UPDATE household_members 
            SET {', '.join(updates)} 
            WHERE household_id = ? AND user_id = ?
            """,
            params
        )
        await self._conn.commit()
        
        return True
    
    # ========================================
    # User Music Preferences
    # ========================================
    
    async def get_user_music_preferences(self, user_id: str) -> dict:
        """Get user's music preferences."""
        cursor = await self._conn.execute(
            "SELECT * FROM user_music_preferences WHERE user_id = ?",
            (user_id,)
        )
        row = await cursor.fetchone()
        
        if not row:
            return {
                "default_provider": "youtube_music",
                "default_volume": 75,
                "crossfade_enabled": False,
                "crossfade_seconds": 5,
                "audio_quality": "auto",
                "autoplay_enabled": True,
                "autoplay_source": "radio",
                "share_listening_activity": True,
                "explicit_content_allowed": True
            }
        
        return dict(row)
    
    async def update_user_music_preferences(
        self,
        user_id: str,
        preferences: dict
    ) -> bool:
        """Update user's music preferences."""
        # Upsert
        columns = [
            "user_id", "default_provider", "default_volume", "crossfade_enabled",
            "crossfade_seconds", "audio_quality", "autoplay_enabled",
            "autoplay_source", "share_listening_activity", "explicit_content_allowed"
        ]
        
        values = [
            user_id,
            preferences.get("default_provider", "youtube_music"),
            preferences.get("default_volume", 75),
            preferences.get("crossfade_enabled", False),
            preferences.get("crossfade_seconds", 5),
            preferences.get("audio_quality", "auto"),
            preferences.get("autoplay_enabled", True),
            preferences.get("autoplay_source", "radio"),
            preferences.get("share_listening_activity", True),
            preferences.get("explicit_content_allowed", True)
        ]
        
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        
        await self._conn.execute(
            f"""
            INSERT OR REPLACE INTO user_music_preferences ({col_names})
            VALUES ({placeholders})
            """,
            values
        )
        await self._conn.commit()
        
        return True
    
    # ========================================
    # Music Sessions
    # ========================================
    
    async def start_session(
        self,
        user_id: str,
        device_id: Optional[str] = None,
        household_id: Optional[str] = None
    ) -> str:
        """Start a new music listening session."""
        session_id = str(uuid.uuid4())
        
        await self._conn.execute(
            """
            INSERT INTO music_sessions (id, user_id, device_id, household_id)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, user_id, device_id, household_id)
        )
        await self._conn.commit()
        
        return session_id
    
    async def end_session(self, session_id: str) -> bool:
        """End a music listening session."""
        await self._conn.execute(
            "UPDATE music_sessions SET ended_at = datetime('now') WHERE id = ?",
            (session_id,)
        )
        await self._conn.commit()
        
        return True
    
    async def update_session(
        self,
        session_id: str,
        current_track_id: Optional[str] = None,
        tracks_played_delta: int = 0,
        tracks_skipped_delta: int = 0,
        listen_time_delta_ms: int = 0
    ) -> bool:
        """Update a music session with playback stats."""
        await self._conn.execute(
            """
            UPDATE music_sessions SET
                current_track_id = COALESCE(?, current_track_id),
                tracks_played = tracks_played + ?,
                tracks_skipped = tracks_skipped + ?,
                total_listen_time_ms = total_listen_time_ms + ?
            WHERE id = ?
            """,
            (
                current_track_id,
                tracks_played_delta,
                tracks_skipped_delta,
                listen_time_delta_ms,
                session_id
            )
        )
        await self._conn.commit()
        
        return True
    
    async def get_active_sessions(
        self,
        household_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> List[dict]:
        """Get active (non-ended) music sessions."""
        query = "SELECT * FROM music_sessions WHERE ended_at IS NULL"
        params = []
        
        if household_id:
            query += " AND household_id = ?"
            params.append(household_id)
        
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        
        cursor = await self._conn.execute(query, params)
        rows = await cursor.fetchall()
        
        return [dict(row) for row in rows]


# Singleton instance
_household_manager: Optional[HouseholdManager] = None


async def get_household_manager() -> HouseholdManager:
    """Get the singleton household manager instance."""
    global _household_manager
    if _household_manager is None:
        _household_manager = HouseholdManager()
        await _household_manager.init()
    return _household_manager

