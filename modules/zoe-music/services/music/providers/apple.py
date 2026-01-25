"""
Apple Music Provider
====================

Integration with Apple Music using the Apple Music API.
Requires an Apple Developer account and MusicKit configuration.

NOTE: Full playback requires the MusicKit JS SDK in the browser.
Server-side API provides search, metadata, and user library access.
"""

import os
import time
import logging
import sqlite3
from typing import List, Optional
from datetime import datetime

from .base import (
    MusicProvider, ProviderType, AuthStatus,
    Track, Album, Artist, Playlist
)

logger = logging.getLogger(__name__)

# Check for jwt (required for Apple Music API)
try:
    import jwt
    import httpx
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False
    logger.warning("PyJWT/httpx not installed - Apple Music provider unavailable")

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
APPLE_TEAM_ID = os.getenv("APPLE_TEAM_ID")
APPLE_KEY_ID = os.getenv("APPLE_KEY_ID")
APPLE_PRIVATE_KEY_PATH = os.getenv("APPLE_PRIVATE_KEY_PATH")
APPLE_STOREFRONT = os.getenv("APPLE_STOREFRONT", "us")  # Default to US


class AppleMusicProvider(MusicProvider):
    """
    Apple Music implementation of MusicProvider.
    
    Uses the Apple Music API with MusicKit authentication.
    User tokens are obtained via MusicKit JS and stored per-user.
    """
    
    API_BASE = "https://api.music.apple.com/v1"
    
    def __init__(self):
        if not JWT_AVAILABLE:
            raise ImportError("PyJWT and httpx are required for Apple Music support")
        
        if not all([APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_PRIVATE_KEY_PATH]):
            logger.warning("Apple Music credentials not fully configured")
        
        self._developer_token = None
        self._developer_token_expires = 0
        self._init_db()
    
    def _init_db(self):
        """Ensure Apple Music credentials table exists."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS apple_music_credentials (
                    user_id TEXT PRIMARY KEY,
                    music_user_token TEXT,
                    storefront TEXT DEFAULT 'us',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to init Apple Music DB: {e}")
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.APPLE_MUSIC
    
    @property
    def display_name(self) -> str:
        return "Apple Music"
    
    @property
    def supports_streaming(self) -> bool:
        # Streaming requires MusicKit JS in browser
        return True
    
    def _get_developer_token(self) -> Optional[str]:
        """Generate or return cached developer token."""
        if self._developer_token and time.time() < self._developer_token_expires:
            return self._developer_token
        
        if not all([APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_PRIVATE_KEY_PATH]):
            logger.error("Apple Music credentials not configured")
            return None
        
        try:
            # Read private key
            with open(APPLE_PRIVATE_KEY_PATH, 'r') as f:
                private_key = f.read()
            
            # Generate JWT
            now = int(time.time())
            exp = now + (6 * 30 * 24 * 60 * 60)  # 6 months
            
            headers = {
                "alg": "ES256",
                "kid": APPLE_KEY_ID
            }
            
            payload = {
                "iss": APPLE_TEAM_ID,
                "iat": now,
                "exp": exp
            }
            
            token = jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
            
            self._developer_token = token
            self._developer_token_expires = exp - 300  # Refresh 5 min before expiry
            
            return token
            
        except Exception as e:
            logger.error(f"Failed to generate Apple developer token: {e}")
            return None
    
    def _get_user_token(self, user_id: str) -> Optional[str]:
        """Get user's Music User Token from database."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT music_user_token FROM apple_music_credentials
                WHERE user_id = ?
            """, (user_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            return row[0] if row else None
        except Exception as e:
            logger.error(f"Failed to get Apple user token: {e}")
            return None
    
    async def _api_request(
        self, 
        endpoint: str, 
        user_id: Optional[str] = None,
        params: Optional[dict] = None
    ) -> Optional[dict]:
        """Make authenticated API request."""
        dev_token = self._get_developer_token()
        if not dev_token:
            return None
        
        headers = {
            "Authorization": f"Bearer {dev_token}",
            "Content-Type": "application/json"
        }
        
        # Add user token for personalized requests
        if user_id:
            user_token = self._get_user_token(user_id)
            if user_token:
                headers["Music-User-Token"] = user_token
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.API_BASE}{endpoint}",
                    headers=headers,
                    params=params,
                    timeout=15.0
                )
                
                if resp.status_code == 200:
                    return resp.json()
                else:
                    logger.warning(f"Apple Music API error {resp.status_code}: {resp.text[:200]}")
                    return None
        except Exception as e:
            logger.error(f"Apple Music API request failed: {e}")
            return None
    
    # ========================================
    # Authentication
    # ========================================
    
    async def get_auth_status(self, user_id: str) -> AuthStatus:
        """Check if user is authenticated with Apple Music."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT music_user_token FROM apple_music_credentials
                WHERE user_id = ?
            """, (user_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row and row[0]:
                return AuthStatus.CONNECTED
            return AuthStatus.NOT_CONNECTED
            
        except Exception as e:
            logger.error(f"Apple Music auth status check failed: {e}")
            return AuthStatus.ERROR
    
    async def get_auth_url(self, user_id: str) -> Optional[str]:
        """
        Get developer token for MusicKit JS authentication.
        
        Apple Music uses MusicKit JS for user authorization.
        Returns the developer token for use with MusicKit.authorize().
        """
        dev_token = self._get_developer_token()
        if dev_token:
            # Return a special URL that frontend handles with MusicKit JS
            return f"musickit://authorize?developerToken={dev_token}"
        return None
    
    async def complete_auth(self, user_id: str, auth_code: str) -> bool:
        """
        Complete auth with Music User Token from MusicKit JS.
        
        The auth_code here is actually the Music User Token obtained
        from MusicKit.authorize() in the browser.
        """
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO apple_music_credentials
                (user_id, music_user_token, storefront, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (user_id, auth_code, APPLE_STOREFRONT))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Apple Music auth completed for {user_id}")
            return True
        except Exception as e:
            logger.error(f"Apple Music auth failed: {e}")
            return False
    
    async def disconnect(self, user_id: str) -> bool:
        """Disconnect user from Apple Music."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM apple_music_credentials WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            
            logger.info(f"Apple Music disconnected for {user_id}")
            return True
        except Exception as e:
            logger.error(f"Apple Music disconnect failed: {e}")
            return False
    
    # ========================================
    # Helper Methods
    # ========================================
    
    def _normalize_track(self, data: dict) -> Track:
        """Convert Apple Music track data to Track."""
        attrs = data.get("attributes", {})
        artwork = attrs.get("artwork", {})
        
        # Build artwork URL
        thumbnail = None
        if artwork.get("url"):
            thumbnail = artwork["url"].replace("{w}", "300").replace("{h}", "300")
        
        return Track(
            id=data.get("id", ""),
            title=attrs.get("name", "Unknown"),
            artist=attrs.get("artistName", ""),
            album=attrs.get("albumName"),
            duration_seconds=attrs.get("durationInMillis", 0) // 1000,
            thumbnail_url=thumbnail,
            provider=ProviderType.APPLE_MUSIC,
            isrc=attrs.get("isrc"),
            release_year=int(attrs.get("releaseDate", "0000")[:4]) if attrs.get("releaseDate") else None,
            explicit=attrs.get("contentRating") == "explicit"
        )
    
    def _normalize_album(self, data: dict) -> Album:
        """Convert Apple Music album data to Album."""
        attrs = data.get("attributes", {})
        artwork = attrs.get("artwork", {})
        
        thumbnail = None
        if artwork.get("url"):
            thumbnail = artwork["url"].replace("{w}", "300").replace("{h}", "300")
        
        release_year = None
        if attrs.get("releaseDate"):
            try:
                release_year = int(attrs["releaseDate"][:4])
            except (ValueError, TypeError):
                pass
        
        return Album(
            id=data.get("id", ""),
            title=attrs.get("name", "Unknown"),
            artist=attrs.get("artistName", ""),
            thumbnail_url=thumbnail,
            release_year=release_year,
            track_count=attrs.get("trackCount"),
            provider=ProviderType.APPLE_MUSIC
        )
    
    def _normalize_artist(self, data: dict) -> Artist:
        """Convert Apple Music artist data to Artist."""
        attrs = data.get("attributes", {})
        artwork = attrs.get("artwork", {})
        
        thumbnail = None
        if artwork.get("url"):
            thumbnail = artwork["url"].replace("{w}", "300").replace("{h}", "300")
        
        return Artist(
            id=data.get("id", ""),
            name=attrs.get("name", "Unknown"),
            thumbnail_url=thumbnail,
            genres=attrs.get("genreNames", []),
            provider=ProviderType.APPLE_MUSIC
        )
    
    def _normalize_playlist(self, data: dict) -> Playlist:
        """Convert Apple Music playlist data to Playlist."""
        attrs = data.get("attributes", {})
        artwork = attrs.get("artwork", {})
        
        thumbnail = None
        if artwork.get("url"):
            thumbnail = artwork["url"].replace("{w}", "300").replace("{h}", "300")
        
        return Playlist(
            id=data.get("id", ""),
            title=attrs.get("name", "Unknown"),
            description=attrs.get("description", {}).get("standard"),
            thumbnail_url=thumbnail,
            owner=attrs.get("curatorName"),
            provider=ProviderType.APPLE_MUSIC
        )
    
    # ========================================
    # Search
    # ========================================
    
    async def search_tracks(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Track]:
        """Search for tracks on Apple Music."""
        data = await self._api_request(
            f"/catalog/{APPLE_STOREFRONT}/search",
            params={"term": query, "types": "songs", "limit": limit}
        )
        
        if not data:
            return []
        
        songs = data.get("results", {}).get("songs", {}).get("data", [])
        return [self._normalize_track(s) for s in songs]
    
    async def search_albums(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Album]:
        """Search for albums on Apple Music."""
        data = await self._api_request(
            f"/catalog/{APPLE_STOREFRONT}/search",
            params={"term": query, "types": "albums", "limit": limit}
        )
        
        if not data:
            return []
        
        albums = data.get("results", {}).get("albums", {}).get("data", [])
        return [self._normalize_album(a) for a in albums]
    
    async def search_artists(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Artist]:
        """Search for artists on Apple Music."""
        data = await self._api_request(
            f"/catalog/{APPLE_STOREFRONT}/search",
            params={"term": query, "types": "artists", "limit": limit}
        )
        
        if not data:
            return []
        
        artists = data.get("results", {}).get("artists", {}).get("data", [])
        return [self._normalize_artist(a) for a in artists]
    
    async def search_playlists(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Playlist]:
        """Search for playlists on Apple Music."""
        data = await self._api_request(
            f"/catalog/{APPLE_STOREFRONT}/search",
            params={"term": query, "types": "playlists", "limit": limit}
        )
        
        if not data:
            return []
        
        playlists = data.get("results", {}).get("playlists", {}).get("data", [])
        return [self._normalize_playlist(p) for p in playlists]
    
    # ========================================
    # Playback
    # ========================================
    
    async def get_stream_url(
        self, 
        track_id: str, 
        quality: Optional[str] = None
    ) -> Optional[str]:
        """
        Get audio stream URL for a track.
        
        NOTE: Apple Music doesn't provide direct stream URLs.
        Playback must use MusicKit JS in the browser.
        """
        logger.info(f"Apple Music stream URL requested - use MusicKit JS for playback")
        return None
    
    async def get_track(self, track_id: str, user_id: str) -> Optional[Track]:
        """Get detailed track information."""
        data = await self._api_request(
            f"/catalog/{APPLE_STOREFRONT}/songs/{track_id}"
        )
        
        if data and data.get("data"):
            return self._normalize_track(data["data"][0])
        return None
    
    async def get_album_tracks(
        self, 
        album_id: str, 
        user_id: str
    ) -> List[Track]:
        """Get all tracks in an album."""
        data = await self._api_request(
            f"/catalog/{APPLE_STOREFRONT}/albums/{album_id}/tracks"
        )
        
        if not data:
            return []
        
        return [self._normalize_track(t) for t in data.get("data", [])]
    
    async def get_playlist_tracks(
        self, 
        playlist_id: str, 
        user_id: str
    ) -> List[Track]:
        """Get all tracks in a playlist."""
        data = await self._api_request(
            f"/catalog/{APPLE_STOREFRONT}/playlists/{playlist_id}/tracks"
        )
        
        if not data:
            return []
        
        return [self._normalize_track(t) for t in data.get("data", [])]
    
    # ========================================
    # User Library
    # ========================================
    
    async def get_liked_songs(
        self, 
        user_id: str, 
        limit: int = 100
    ) -> List[Track]:
        """Get user's library songs."""
        data = await self._api_request(
            "/me/library/songs",
            user_id=user_id,
            params={"limit": min(limit, 100)}
        )
        
        if not data:
            return []
        
        return [self._normalize_track(t) for t in data.get("data", [])]
    
    async def get_user_playlists(
        self, 
        user_id: str
    ) -> List[Playlist]:
        """Get user's playlists."""
        data = await self._api_request(
            "/me/library/playlists",
            user_id=user_id
        )
        
        if not data:
            return []
        
        return [self._normalize_playlist(p) for p in data.get("data", [])]
    
    async def get_user_albums(
        self, 
        user_id: str
    ) -> List[Album]:
        """Get user's saved albums."""
        data = await self._api_request(
            "/me/library/albums",
            user_id=user_id
        )
        
        if not data:
            return []
        
        return [self._normalize_album(a) for a in data.get("data", [])]
    
    async def get_user_artists(
        self, 
        user_id: str
    ) -> List[Artist]:
        """Get user's followed artists."""
        data = await self._api_request(
            "/me/library/artists",
            user_id=user_id
        )
        
        if not data:
            return []
        
        return [self._normalize_artist(a) for a in data.get("data", [])]
    
    # ========================================
    # Recommendations
    # ========================================
    
    async def get_recommendations(
        self, 
        user_id: str, 
        seed_tracks: Optional[List[str]] = None,
        seed_artists: Optional[List[str]] = None,
        limit: int = 20
    ) -> List[Track]:
        """Get personalized recommendations."""
        data = await self._api_request(
            "/me/recommendations",
            user_id=user_id,
            params={"limit": min(limit, 30)}
        )
        
        if not data:
            return []
        
        tracks = []
        for item in data.get("data", []):
            relationships = item.get("relationships", {})
            contents = relationships.get("contents", {}).get("data", [])
            for content in contents:
                if content.get("type") == "songs":
                    tracks.append(self._normalize_track(content))
        
        return tracks[:limit]
    
    async def get_similar_tracks(
        self, 
        track_id: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Track]:
        """Get tracks similar to a given track (not directly supported)."""
        # Apple Music doesn't have a direct "similar tracks" API
        # Get the track's artist and search for more of their songs
        track = await self.get_track(track_id, user_id)
        if track:
            return await self.search_tracks(track.artist, user_id, limit)
        return []

