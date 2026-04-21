"""
Spotify Music Provider
======================

Integration with Spotify using spotipy library.
Supports OAuth authentication and most Spotify features.

NOTE: Spotify requires a Premium subscription for full audio streaming.
Free tier only provides 30-second previews.
"""

import os
import logging
import sqlite3
from typing import List, Optional
from datetime import datetime

from .base import (
    MusicProvider, ProviderType, AuthStatus,
    Track, Album, Artist, Playlist
)

logger = logging.getLogger(__name__)

# Check for spotipy
try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    SPOTIPY_AVAILABLE = True
except ImportError:
    SPOTIPY_AVAILABLE = False
    logger.warning("spotipy not installed - Spotify provider unavailable")

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8000/api/music/auth/spotify/callback")


class SpotifyProvider(MusicProvider):
    """
    Spotify implementation of MusicProvider.
    
    Uses spotipy library for API access.
    OAuth tokens are stored per-user in the database.
    """
    
    def __init__(self):
        if not SPOTIPY_AVAILABLE:
            raise ImportError("spotipy library is required for Spotify support")
        
        if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
            logger.warning("Spotify credentials not configured")
        
        self._clients = {}  # Per-user Spotify clients
        self._init_db()
    
    def _init_db(self):
        """Ensure Spotify credentials table exists."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS spotify_credentials (
                    user_id TEXT PRIMARY KEY,
                    access_token TEXT,
                    refresh_token TEXT,
                    token_type TEXT DEFAULT 'Bearer',
                    expires_at INTEGER,
                    scope TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to init Spotify DB: {e}")
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.SPOTIFY
    
    @property
    def display_name(self) -> str:
        return "Spotify"
    
    @property
    def supports_streaming(self) -> bool:
        # Only Premium accounts can stream full tracks
        return True
    
    def _get_client(self, user_id: str) -> Optional[spotipy.Spotify]:
        """Get or create Spotify client for user."""
        if user_id in self._clients:
            return self._clients[user_id]
        
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT access_token, refresh_token, expires_at, scope
                FROM spotify_credentials
                WHERE user_id = ?
            """, (user_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return None
            
            access_token, refresh_token, expires_at, scope = row
            
            # Check if token is expired
            if expires_at and datetime.now().timestamp() >= expires_at:
                # Refresh the token
                auth = SpotifyOAuth(
                    client_id=SPOTIFY_CLIENT_ID,
                    client_secret=SPOTIFY_CLIENT_SECRET,
                    redirect_uri=SPOTIFY_REDIRECT_URI,
                    scope=scope
                )
                
                token_info = auth.refresh_access_token(refresh_token)
                if token_info:
                    self._save_token(user_id, token_info)
                    access_token = token_info.get("access_token")
            
            # Create client
            sp = spotipy.Spotify(auth=access_token)
            self._clients[user_id] = sp
            return sp
            
        except Exception as e:
            logger.error(f"Failed to get Spotify client: {e}")
            return None
    
    def _save_token(self, user_id: str, token_info: dict):
        """Save OAuth token to database."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO spotify_credentials
                (user_id, access_token, refresh_token, token_type, expires_at, scope, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                user_id,
                token_info.get("access_token"),
                token_info.get("refresh_token"),
                token_info.get("token_type", "Bearer"),
                token_info.get("expires_at"),
                token_info.get("scope")
            ))
            
            conn.commit()
            conn.close()
            
            # Clear cached client
            if user_id in self._clients:
                del self._clients[user_id]
                
        except Exception as e:
            logger.error(f"Failed to save Spotify token: {e}")
    
    # ========================================
    # Authentication
    # ========================================
    
    async def get_auth_status(self, user_id: str) -> AuthStatus:
        """Check if user is authenticated with Spotify."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT access_token, expires_at FROM spotify_credentials
                WHERE user_id = ?
            """, (user_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row or not row[0]:
                return AuthStatus.NOT_CONNECTED
            
            # Check if expired (with buffer)
            if row[1] and datetime.now().timestamp() >= row[1] - 300:
                # Try to refresh
                client = self._get_client(user_id)
                if not client:
                    return AuthStatus.EXPIRED
            
            return AuthStatus.CONNECTED
            
        except Exception as e:
            logger.error(f"Spotify auth status check failed: {e}")
            return AuthStatus.ERROR
    
    async def get_auth_url(self, user_id: str) -> Optional[str]:
        """Get Spotify OAuth authorization URL."""
        if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
            logger.error("Spotify credentials not configured")
            return None
        
        try:
            auth = SpotifyOAuth(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope="user-library-read user-read-playback-state user-modify-playback-state playlist-read-private playlist-modify-private",
                state=user_id  # Pass user_id as state for callback
            )
            return auth.get_authorize_url()
        except Exception as e:
            logger.error(f"Failed to get Spotify auth URL: {e}")
            return None
    
    async def complete_auth(self, user_id: str, auth_code: str) -> bool:
        """Complete OAuth flow with authorization code."""
        try:
            auth = SpotifyOAuth(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
                redirect_uri=SPOTIFY_REDIRECT_URI
            )
            
            token_info = auth.get_access_token(auth_code)
            if token_info:
                self._save_token(user_id, token_info)
                logger.info(f"Spotify auth completed for {user_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Spotify auth failed: {e}")
            return False
    
    async def disconnect(self, user_id: str) -> bool:
        """Disconnect user from Spotify."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM spotify_credentials WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            
            if user_id in self._clients:
                del self._clients[user_id]
            
            logger.info(f"Spotify disconnected for {user_id}")
            return True
        except Exception as e:
            logger.error(f"Spotify disconnect failed: {e}")
            return False
    
    # ========================================
    # Helper Methods
    # ========================================
    
    def _normalize_track(self, data: dict) -> Track:
        """Convert Spotify track data to Track."""
        artists = data.get("artists", [])
        artist_name = ", ".join(a.get("name", "") for a in artists)
        
        album = data.get("album", {})
        images = album.get("images", [])
        thumbnail = images[0].get("url") if images else None
        
        return Track(
            id=data.get("id", ""),
            title=data.get("name", "Unknown"),
            artist=artist_name,
            album=album.get("name"),
            album_id=album.get("id"),
            duration_seconds=data.get("duration_ms", 0) // 1000,
            thumbnail_url=thumbnail,
            provider=ProviderType.SPOTIFY,
            isrc=data.get("external_ids", {}).get("isrc"),
            explicit=data.get("explicit", False)
        )
    
    def _normalize_album(self, data: dict) -> Album:
        """Convert Spotify album data to Album."""
        artists = data.get("artists", [])
        artist_name = artists[0].get("name", "") if artists else ""
        
        images = data.get("images", [])
        thumbnail = images[0].get("url") if images else None
        
        release_year = None
        if data.get("release_date"):
            try:
                release_year = int(data["release_date"][:4])
            except (ValueError, TypeError):
                pass
        
        return Album(
            id=data.get("id", ""),
            title=data.get("name", "Unknown"),
            artist=artist_name,
            thumbnail_url=thumbnail,
            release_year=release_year,
            track_count=data.get("total_tracks"),
            provider=ProviderType.SPOTIFY
        )
    
    def _normalize_artist(self, data: dict) -> Artist:
        """Convert Spotify artist data to Artist."""
        images = data.get("images", [])
        thumbnail = images[0].get("url") if images else None
        
        return Artist(
            id=data.get("id", ""),
            name=data.get("name", "Unknown"),
            thumbnail_url=thumbnail,
            genres=data.get("genres", []),
            provider=ProviderType.SPOTIFY
        )
    
    def _normalize_playlist(self, data: dict) -> Playlist:
        """Convert Spotify playlist data to Playlist."""
        images = data.get("images", [])
        thumbnail = images[0].get("url") if images else None
        
        owner = data.get("owner", {}).get("display_name")
        
        return Playlist(
            id=data.get("id", ""),
            title=data.get("name", "Unknown"),
            description=data.get("description"),
            thumbnail_url=thumbnail,
            track_count=data.get("tracks", {}).get("total"),
            owner=owner,
            is_public=data.get("public", True),
            provider=ProviderType.SPOTIFY
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
        """Search for tracks on Spotify."""
        client = self._get_client(user_id)
        if not client:
            return []
        
        try:
            results = client.search(q=query, type="track", limit=limit)
            return [
                self._normalize_track(t) 
                for t in results.get("tracks", {}).get("items", [])
            ]
        except Exception as e:
            logger.error(f"Spotify track search failed: {e}")
            return []
    
    async def search_albums(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Album]:
        """Search for albums on Spotify."""
        client = self._get_client(user_id)
        if not client:
            return []
        
        try:
            results = client.search(q=query, type="album", limit=limit)
            return [
                self._normalize_album(a) 
                for a in results.get("albums", {}).get("items", [])
            ]
        except Exception as e:
            logger.error(f"Spotify album search failed: {e}")
            return []
    
    async def search_artists(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Artist]:
        """Search for artists on Spotify."""
        client = self._get_client(user_id)
        if not client:
            return []
        
        try:
            results = client.search(q=query, type="artist", limit=limit)
            return [
                self._normalize_artist(a) 
                for a in results.get("artists", {}).get("items", [])
            ]
        except Exception as e:
            logger.error(f"Spotify artist search failed: {e}")
            return []
    
    async def search_playlists(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Playlist]:
        """Search for playlists on Spotify."""
        client = self._get_client(user_id)
        if not client:
            return []
        
        try:
            results = client.search(q=query, type="playlist", limit=limit)
            return [
                self._normalize_playlist(p) 
                for p in results.get("playlists", {}).get("items", [])
            ]
        except Exception as e:
            logger.error(f"Spotify playlist search failed: {e}")
            return []
    
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
        
        NOTE: Spotify doesn't provide direct stream URLs via API.
        For playback, use Spotify Connect or Web Playback SDK.
        This returns the preview URL (30 seconds) for free tier.
        """
        # Spotify doesn't expose stream URLs directly
        # Return None - playback should use Spotify Connect
        logger.info(f"Spotify stream URL requested - use Spotify Connect for playback")
        return None
    
    async def get_track(self, track_id: str, user_id: str) -> Optional[Track]:
        """Get detailed track information."""
        client = self._get_client(user_id)
        if not client:
            return None
        
        try:
            data = client.track(track_id)
            return self._normalize_track(data)
        except Exception as e:
            logger.error(f"Failed to get Spotify track: {e}")
            return None
    
    async def get_album_tracks(
        self, 
        album_id: str, 
        user_id: str
    ) -> List[Track]:
        """Get all tracks in an album."""
        client = self._get_client(user_id)
        if not client:
            return []
        
        try:
            results = client.album_tracks(album_id)
            tracks = []
            for item in results.get("items", []):
                # Album tracks don't include album info, add it
                item["album"] = {"id": album_id, "name": "", "images": []}
                tracks.append(self._normalize_track(item))
            return tracks
        except Exception as e:
            logger.error(f"Failed to get Spotify album tracks: {e}")
            return []
    
    async def get_playlist_tracks(
        self, 
        playlist_id: str, 
        user_id: str
    ) -> List[Track]:
        """Get all tracks in a playlist."""
        client = self._get_client(user_id)
        if not client:
            return []
        
        try:
            results = client.playlist_tracks(playlist_id)
            return [
                self._normalize_track(item.get("track", {}))
                for item in results.get("items", [])
                if item.get("track")
            ]
        except Exception as e:
            logger.error(f"Failed to get Spotify playlist tracks: {e}")
            return []
    
    # ========================================
    # User Library
    # ========================================
    
    async def get_liked_songs(
        self, 
        user_id: str, 
        limit: int = 100
    ) -> List[Track]:
        """Get user's liked songs."""
        client = self._get_client(user_id)
        if not client:
            return []
        
        try:
            results = client.current_user_saved_tracks(limit=min(limit, 50))
            return [
                self._normalize_track(item.get("track", {}))
                for item in results.get("items", [])
                if item.get("track")
            ]
        except Exception as e:
            logger.error(f"Failed to get Spotify liked songs: {e}")
            return []
    
    async def get_user_playlists(
        self, 
        user_id: str
    ) -> List[Playlist]:
        """Get user's playlists."""
        client = self._get_client(user_id)
        if not client:
            return []
        
        try:
            results = client.current_user_playlists()
            return [
                self._normalize_playlist(p)
                for p in results.get("items", [])
            ]
        except Exception as e:
            logger.error(f"Failed to get Spotify playlists: {e}")
            return []
    
    async def get_user_albums(
        self, 
        user_id: str
    ) -> List[Album]:
        """Get user's saved albums."""
        client = self._get_client(user_id)
        if not client:
            return []
        
        try:
            results = client.current_user_saved_albums()
            return [
                self._normalize_album(item.get("album", {}))
                for item in results.get("items", [])
                if item.get("album")
            ]
        except Exception as e:
            logger.error(f"Failed to get Spotify albums: {e}")
            return []
    
    async def get_user_artists(
        self, 
        user_id: str
    ) -> List[Artist]:
        """Get user's followed artists."""
        client = self._get_client(user_id)
        if not client:
            return []
        
        try:
            results = client.current_user_followed_artists()
            return [
                self._normalize_artist(a)
                for a in results.get("artists", {}).get("items", [])
            ]
        except Exception as e:
            logger.error(f"Failed to get Spotify artists: {e}")
            return []
    
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
        client = self._get_client(user_id)
        if not client:
            return []
        
        try:
            results = client.recommendations(
                seed_tracks=seed_tracks[:5] if seed_tracks else None,
                seed_artists=seed_artists[:5] if seed_artists else None,
                limit=limit
            )
            return [
                self._normalize_track(t)
                for t in results.get("tracks", [])
            ]
        except Exception as e:
            logger.error(f"Failed to get Spotify recommendations: {e}")
            return []
    
    async def get_similar_tracks(
        self, 
        track_id: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Track]:
        """Get tracks similar to a given track."""
        return await self.get_recommendations(
            user_id, 
            seed_tracks=[track_id], 
            limit=limit
        )
    
    # ========================================
    # Library Management
    # ========================================
    
    async def like_track(self, track_id: str, user_id: str) -> bool:
        """Save a track to user's library."""
        client = self._get_client(user_id)
        if not client:
            return False
        
        try:
            client.current_user_saved_tracks_add([track_id])
            return True
        except Exception as e:
            logger.error(f"Failed to save Spotify track: {e}")
            return False
    
    async def unlike_track(self, track_id: str, user_id: str) -> bool:
        """Remove track from user's library."""
        client = self._get_client(user_id)
        if not client:
            return False
        
        try:
            client.current_user_saved_tracks_delete([track_id])
            return True
        except Exception as e:
            logger.error(f"Failed to unsave Spotify track: {e}")
            return False
    
    async def create_playlist(
        self, 
        name: str, 
        user_id: str, 
        description: Optional[str] = None
    ) -> Optional[Playlist]:
        """Create a new playlist."""
        client = self._get_client(user_id)
        if not client:
            return None
        
        try:
            # Get current user's Spotify ID
            me = client.me()
            spotify_user_id = me.get("id")
            
            result = client.user_playlist_create(
                spotify_user_id,
                name,
                public=False,
                description=description or ""
            )
            
            if result:
                return self._normalize_playlist(result)
            return None
        except Exception as e:
            logger.error(f"Failed to create Spotify playlist: {e}")
            return None
    
    async def add_to_playlist(
        self, 
        playlist_id: str, 
        track_ids: List[str], 
        user_id: str
    ) -> bool:
        """Add tracks to a playlist."""
        client = self._get_client(user_id)
        if not client:
            return False
        
        try:
            # Convert track IDs to URIs if needed
            uris = [
                f"spotify:track:{tid}" if not tid.startswith("spotify:") else tid
                for tid in track_ids
            ]
            client.playlist_add_items(playlist_id, uris)
            return True
        except Exception as e:
            logger.error(f"Failed to add to Spotify playlist: {e}")
            return False

