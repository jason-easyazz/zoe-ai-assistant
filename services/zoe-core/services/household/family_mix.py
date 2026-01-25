"""
Family Mix Generator
====================

Generates shared playlists that blend the tastes of all household members.
"""

import os
import asyncio
import logging
import json
import random
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid

logger = logging.getLogger(__name__)


@dataclass
class FamilyMixTrack:
    """A track in the family mix."""
    track_id: str
    provider: str
    title: str
    artist: str
    album_art_url: Optional[str] = None
    duration_ms: Optional[int] = None
    contributed_by: Optional[str] = None  # User who contributed this
    weight: float = 1.0  # How much this track fits the family taste


@dataclass
class FamilyMix:
    """A generated family mix."""
    household_id: str
    tracks: List[FamilyMixTrack]
    generated_at: datetime
    valid_until: datetime
    user_weights: Dict[str, float]


class FamilyMixGenerator:
    """
    Generates family mix playlists.
    
    The family mix algorithm:
    1. Collect recent listening history from all household members
    2. Weight tracks by user listening frequency
    3. Apply diversity rules (no artist domination)
    4. Filter by content settings (explicit content)
    5. Order by blend of tastes
    
    Usage:
        generator = FamilyMixGenerator(db_path)
        await generator.init()
        
        mix = await generator.generate_mix(household_id)
    """
    
    def __init__(self, db_path: Optional[str] = None):
        """Initialize family mix generator."""
        self.db_path = db_path or os.getenv("ZOE_DB_PATH", "/app/data/zoe.db")
        self._conn = None
        self._initialized = False
        
        # Configuration
        self.default_track_count = 50
        self.max_tracks_per_artist = 3
        self.mix_validity_hours = 24
        self.min_tracks_per_member = 5
    
    async def init(self) -> None:
        """Initialize database connection."""
        if self._initialized:
            return
        
        import aiosqlite
        
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        
        self._initialized = True
        logger.info("FamilyMixGenerator initialized")
    
    async def close(self) -> None:
        """Close database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            self._initialized = False
    
    async def generate_mix(
        self,
        household_id: str,
        track_count: int = 50,
        exclude_explicit: bool = False,
        user_weights: Optional[Dict[str, float]] = None
    ) -> FamilyMix:
        """
        Generate a family mix for a household.
        
        Args:
            household_id: Household to generate for
            track_count: Number of tracks to include
            exclude_explicit: Filter out explicit content
            user_weights: Optional per-user weight overrides
        
        Returns:
            FamilyMix object
        """
        # Get household members
        members = await self._get_household_members(household_id)
        
        if not members:
            logger.warning(f"No members found for household {household_id}")
            return FamilyMix(
                household_id=household_id,
                tracks=[],
                generated_at=datetime.now(),
                valid_until=datetime.now() + timedelta(hours=self.mix_validity_hours),
                user_weights={}
            )
        
        # Calculate user weights (equal by default)
        weights = user_weights or {}
        for member_id in members:
            if member_id not in weights:
                weights[member_id] = 1.0
        
        # Normalize weights
        total_weight = sum(weights.values())
        if total_weight > 0:
            weights = {k: v / total_weight for k, v in weights.items()}
        
        # Collect candidate tracks from each user
        all_candidates = []
        for member_id in members:
            user_tracks = await self._get_user_recent_tracks(member_id)
            for track in user_tracks:
                track["user_weight"] = weights.get(member_id, 1.0 / len(members))
                track["contributed_by"] = member_id
                all_candidates.append(track)
        
        # Deduplicate and aggregate weights
        track_map = {}
        for track in all_candidates:
            key = f"{track['track_id']}_{track['provider']}"
            if key in track_map:
                # Track liked by multiple members - boost weight
                track_map[key]["weight"] += track["user_weight"]
                track_map[key]["contributed_by"] = None  # Multiple contributors
            else:
                track_map[key] = {
                    **track,
                    "weight": track["user_weight"]
                }
        
        candidates = list(track_map.values())
        
        # Filter explicit content if needed
        if exclude_explicit:
            candidates = [t for t in candidates if not t.get("is_explicit", False)]
        
        # Apply diversity rules
        selected = self._apply_diversity_rules(candidates, track_count)
        
        # Sort by weighted random for variety
        random.shuffle(selected)
        
        # Convert to FamilyMixTrack objects
        mix_tracks = [
            FamilyMixTrack(
                track_id=t["track_id"],
                provider=t["provider"],
                title=t["track_title"],
                artist=t["artist"],
                album_art_url=t.get("album_art_url"),
                duration_ms=t.get("duration_ms"),
                contributed_by=t.get("contributed_by"),
                weight=t.get("weight", 1.0)
            )
            for t in selected
        ]
        
        # Save to database
        mix = FamilyMix(
            household_id=household_id,
            tracks=mix_tracks,
            generated_at=datetime.now(),
            valid_until=datetime.now() + timedelta(hours=self.mix_validity_hours),
            user_weights=weights
        )
        
        await self._save_mix(mix)
        
        logger.info(f"Generated family mix for {household_id}: {len(mix_tracks)} tracks")
        
        return mix
    
    def _apply_diversity_rules(
        self,
        candidates: List[dict],
        track_count: int
    ) -> List[dict]:
        """
        Apply diversity rules to candidate tracks.
        
        - Limit tracks per artist
        - Ensure representation from multiple users
        - Balance by weight
        """
        selected = []
        artist_counts = {}
        
        # Sort by weight (descending) then shuffle within weight tiers
        candidates.sort(key=lambda t: t.get("weight", 0), reverse=True)
        
        for track in candidates:
            if len(selected) >= track_count:
                break
            
            artist = track.get("artist", "Unknown")
            
            # Check artist limit
            if artist_counts.get(artist, 0) >= self.max_tracks_per_artist:
                continue
            
            selected.append(track)
            artist_counts[artist] = artist_counts.get(artist, 0) + 1
        
        return selected
    
    async def _get_household_members(self, household_id: str) -> List[str]:
        """Get all member user IDs for a household."""
        cursor = await self._conn.execute(
            "SELECT user_id FROM household_members WHERE household_id = ?",
            (household_id,)
        )
        rows = await cursor.fetchall()
        
        return [row["user_id"] for row in rows]
    
    async def _get_user_recent_tracks(
        self,
        user_id: str,
        days: int = 30,
        limit: int = 100
    ) -> List[dict]:
        """Get recent tracks from a user's history."""
        # Try music_history table first
        cursor = await self._conn.execute(
            """
            SELECT 
                track_id, provider, track_title, artist, album,
                COUNT(*) as play_count
            FROM music_history
            WHERE user_id = ?
            AND played_at > datetime('now', '-' || ? || ' days')
            GROUP BY track_id, provider
            ORDER BY play_count DESC
            LIMIT ?
            """,
            (user_id, days, limit)
        )
        rows = await cursor.fetchall()
        
        if rows:
            return [dict(row) for row in rows]
        
        # Fall back to liked tracks
        cursor = await self._conn.execute(
            """
            SELECT track_id, provider, track_title, artist, album_art_url
            FROM music_likes
            WHERE user_id = ?
            ORDER BY liked_at DESC
            LIMIT ?
            """,
            (user_id, limit)
        )
        rows = await cursor.fetchall()
        
        return [dict(row) for row in rows]
    
    async def _save_mix(self, mix: FamilyMix) -> None:
        """Save generated mix to database."""
        mix_data = {
            "tracks": [
                {
                    "track_id": t.track_id,
                    "provider": t.provider,
                    "title": t.title,
                    "artist": t.artist,
                    "album_art_url": t.album_art_url,
                    "contributed_by": t.contributed_by,
                    "weight": t.weight
                }
                for t in mix.tracks
            ]
        }
        
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO family_mix_state
            (household_id, current_mix, user_weights, generated_at, valid_until)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                mix.household_id,
                json.dumps(mix_data),
                json.dumps(mix.user_weights),
                mix.generated_at.isoformat(),
                mix.valid_until.isoformat()
            )
        )
        await self._conn.commit()
    
    async def get_cached_mix(self, household_id: str) -> Optional[FamilyMix]:
        """Get cached family mix if still valid."""
        cursor = await self._conn.execute(
            """
            SELECT * FROM family_mix_state
            WHERE household_id = ?
            AND valid_until > datetime('now')
            """,
            (household_id,)
        )
        row = await cursor.fetchone()
        
        if not row:
            return None
        
        mix_data = json.loads(row["current_mix"])
        
        return FamilyMix(
            household_id=household_id,
            tracks=[
                FamilyMixTrack(
                    track_id=t["track_id"],
                    provider=t["provider"],
                    title=t["title"],
                    artist=t["artist"],
                    album_art_url=t.get("album_art_url"),
                    contributed_by=t.get("contributed_by"),
                    weight=t.get("weight", 1.0)
                )
                for t in mix_data.get("tracks", [])
            ],
            generated_at=datetime.fromisoformat(row["generated_at"]),
            valid_until=datetime.fromisoformat(row["valid_until"]),
            user_weights=json.loads(row["user_weights"] or "{}")
        )
    
    async def get_or_generate_mix(
        self,
        household_id: str,
        force_regenerate: bool = False,
        **kwargs
    ) -> FamilyMix:
        """Get cached mix or generate new one."""
        if not force_regenerate:
            cached = await self.get_cached_mix(household_id)
            if cached:
                return cached
        
        return await self.generate_mix(household_id, **kwargs)
    
    # ========================================
    # Shared Playlists
    # ========================================
    
    async def create_shared_playlist(
        self,
        household_id: str,
        name: str,
        created_by: str,
        playlist_type: str = "collaborative",
        description: Optional[str] = None
    ) -> str:
        """Create a shared playlist for the household."""
        playlist_id = str(uuid.uuid4())
        
        await self._conn.execute(
            """
            INSERT INTO shared_playlists
            (id, household_id, name, description, playlist_type, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (playlist_id, household_id, name, description, playlist_type, created_by)
        )
        await self._conn.commit()
        
        logger.info(f"Created shared playlist: {name} ({playlist_id})")
        
        return playlist_id
    
    async def add_to_shared_playlist(
        self,
        playlist_id: str,
        track: dict,
        added_by: str
    ) -> bool:
        """Add a track to a shared playlist."""
        # Get next position
        cursor = await self._conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM shared_playlist_tracks WHERE playlist_id = ?",
            (playlist_id,)
        )
        row = await cursor.fetchone()
        position = row["next_pos"]
        
        await self._conn.execute(
            """
            INSERT INTO shared_playlist_tracks
            (playlist_id, track_id, provider, track_title, artist, album_art_url, 
             duration_ms, position, added_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                playlist_id,
                track["track_id"],
                track.get("provider", "youtube_music"),
                track.get("title") or track.get("track_title"),
                track.get("artist"),
                track.get("album_art_url"),
                track.get("duration_ms"),
                position,
                added_by
            )
        )
        await self._conn.commit()
        
        return True
    
    async def get_shared_playlists(self, household_id: str) -> List[dict]:
        """Get all shared playlists for a household."""
        cursor = await self._conn.execute(
            """
            SELECT sp.*, COUNT(spt.id) as track_count
            FROM shared_playlists sp
            LEFT JOIN shared_playlist_tracks spt ON sp.id = spt.playlist_id
            WHERE sp.household_id = ?
            GROUP BY sp.id
            ORDER BY sp.updated_at DESC
            """,
            (household_id,)
        )
        rows = await cursor.fetchall()
        
        return [dict(row) for row in rows]
    
    async def get_shared_playlist_tracks(self, playlist_id: str) -> List[dict]:
        """Get tracks in a shared playlist."""
        cursor = await self._conn.execute(
            """
            SELECT * FROM shared_playlist_tracks
            WHERE playlist_id = ?
            ORDER BY position ASC
            """,
            (playlist_id,)
        )
        rows = await cursor.fetchall()
        
        return [dict(row) for row in rows]


# Singleton instance
_family_mix_generator: Optional[FamilyMixGenerator] = None


async def get_family_mix_generator() -> FamilyMixGenerator:
    """Get the singleton family mix generator instance."""
    global _family_mix_generator
    if _family_mix_generator is None:
        _family_mix_generator = FamilyMixGenerator()
        await _family_mix_generator.init()
    return _family_mix_generator

