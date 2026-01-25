"""
YouTube Music Provider Adapter
==============================

Adapts the existing YouTubeMusicProvider to the unified MusicProvider interface.
"""

import logging
from typing import List, Optional

from .base import (
    MusicProvider, ProviderType, AuthStatus,
    Track, Album, Artist, Playlist
)

logger = logging.getLogger(__name__)


class YouTubeMusicAdapter(MusicProvider):
    """
    Adapter that wraps the existing YouTubeMusicProvider
    to implement the unified MusicProvider interface.
    """
    
    def __init__(self):
        """Initialize the YouTube Music adapter."""
        self._provider = None
        self._auth_manager = None
    
    def _get_provider(self):
        """Lazy load the underlying provider."""
        if self._provider is None:
            from services.music.youtube_music import get_youtube_music
            self._provider = get_youtube_music()
        return self._provider
    
    def _get_auth_manager(self):
        """Lazy load the auth manager."""
        if self._auth_manager is None:
            from services.music.auth_manager import get_auth_manager
            self._auth_manager = get_auth_manager()
        return self._auth_manager
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.YOUTUBE_MUSIC
    
    @property
    def display_name(self) -> str:
        return "YouTube Music"
    
    @property
    def supports_streaming(self) -> bool:
        return True
    
    # ========================================
    # Authentication
    # ========================================
    
    async def get_auth_status(self, user_id: str) -> AuthStatus:
        """Check if user is authenticated with YouTube Music."""
        auth_manager = self._get_auth_manager()
        
        try:
            if await auth_manager.is_authenticated(user_id):
                return AuthStatus.CONNECTED
            return AuthStatus.NOT_CONNECTED
        except Exception as e:
            logger.error(f"Auth status check failed: {e}")
            return AuthStatus.ERROR
    
    async def get_auth_url(self, user_id: str) -> Optional[str]:
        """Get OAuth authorization URL."""
        auth_manager = self._get_auth_manager()
        
        try:
            result = await auth_manager.start_youtube_auth(user_id)
            return result.get("auth_url")
        except Exception as e:
            logger.error(f"Failed to get auth URL: {e}")
            return None
    
    async def complete_auth(self, user_id: str, auth_code: str) -> bool:
        """Complete OAuth flow with authorization code."""
        auth_manager = self._get_auth_manager()
        
        try:
            result = await auth_manager.complete_youtube_auth(user_id, auth_code)
            return result.get("success", False)
        except Exception as e:
            logger.error(f"Auth completion failed: {e}")
            return False
    
    async def disconnect(self, user_id: str) -> bool:
        """Disconnect user from YouTube Music."""
        auth_manager = self._get_auth_manager()
        
        try:
            return await auth_manager.disconnect_youtube(user_id)
        except Exception as e:
            logger.error(f"Disconnect failed: {e}")
            return False
    
    # ========================================
    # Search
    # ========================================
    
    def _normalize_to_track(self, data: dict) -> Track:
        """Convert provider-specific track data to Track dataclass."""
        artists = data.get("artists", [])
        artist_name = artists[0].get("name", "") if artists else data.get("artist", "")
        
        album_data = data.get("album", {})
        album_name = album_data.get("name") if isinstance(album_data, dict) else album_data
        
        thumbnails = data.get("thumbnails", [])
        thumbnail_url = thumbnails[-1].get("url") if thumbnails else data.get("thumbnailUrl")
        
        return Track(
            id=data.get("videoId") or data.get("id", ""),
            title=data.get("title", "Unknown"),
            artist=artist_name,
            album=album_name,
            duration_seconds=data.get("duration_seconds"),
            thumbnail_url=thumbnail_url,
            provider=ProviderType.YOUTUBE_MUSIC,
            explicit=data.get("isExplicit", False)
        )
    
    def _normalize_to_album(self, data: dict) -> Album:
        """Convert provider-specific album data to Album dataclass."""
        artists = data.get("artists", [])
        artist_name = artists[0].get("name", "") if artists else ""
        
        thumbnails = data.get("thumbnails", [])
        thumbnail_url = thumbnails[-1].get("url") if thumbnails else None
        
        return Album(
            id=data.get("browseId") or data.get("id", ""),
            title=data.get("title", "Unknown"),
            artist=artist_name,
            thumbnail_url=thumbnail_url,
            release_year=data.get("year"),
            track_count=data.get("trackCount"),
            provider=ProviderType.YOUTUBE_MUSIC
        )
    
    def _normalize_to_artist(self, data: dict) -> Artist:
        """Convert provider-specific artist data to Artist dataclass."""
        thumbnails = data.get("thumbnails", [])
        thumbnail_url = thumbnails[-1].get("url") if thumbnails else None
        
        return Artist(
            id=data.get("browseId") or data.get("id", ""),
            name=data.get("artist") or data.get("name", "Unknown"),
            thumbnail_url=thumbnail_url,
            provider=ProviderType.YOUTUBE_MUSIC
        )
    
    def _normalize_to_playlist(self, data: dict) -> Playlist:
        """Convert provider-specific playlist data to Playlist dataclass."""
        thumbnails = data.get("thumbnails", [])
        thumbnail_url = thumbnails[-1].get("url") if thumbnails else None
        
        return Playlist(
            id=data.get("playlistId") or data.get("id", ""),
            title=data.get("title", "Unknown"),
            description=data.get("description"),
            thumbnail_url=thumbnail_url,
            track_count=data.get("count") or data.get("trackCount"),
            owner=data.get("author"),
            provider=ProviderType.YOUTUBE_MUSIC
        )
    
    async def search_tracks(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Track]:
        """Search for tracks."""
        provider = self._get_provider()
        
        try:
            results = await provider.search(query, user_id, limit, filter_type="songs")
            return [self._normalize_to_track(r) for r in results]
        except Exception as e:
            logger.error(f"Track search failed: {e}")
            return []
    
    async def search_albums(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Album]:
        """Search for albums."""
        provider = self._get_provider()
        
        try:
            results = await provider.search(query, user_id, limit, filter_type="albums")
            return [self._normalize_to_album(r) for r in results]
        except Exception as e:
            logger.error(f"Album search failed: {e}")
            return []
    
    async def search_artists(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Artist]:
        """Search for artists."""
        provider = self._get_provider()
        
        try:
            results = await provider.search(query, user_id, limit, filter_type="artists")
            return [self._normalize_to_artist(r) for r in results]
        except Exception as e:
            logger.error(f"Artist search failed: {e}")
            return []
    
    async def search_playlists(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Playlist]:
        """Search for playlists."""
        provider = self._get_provider()
        
        try:
            results = await provider.search(query, user_id, limit, filter_type="playlists")
            return [self._normalize_to_playlist(r) for r in results]
        except Exception as e:
            logger.error(f"Playlist search failed: {e}")
            return []
    
    # ========================================
    # Playback
    # ========================================
    
    async def get_stream_url(
        self, 
        track_id: str, 
        quality: Optional[str] = None
    ) -> Optional[str]:
        """Get audio stream URL for a track."""
        provider = self._get_provider()
        
        try:
            return await provider.get_stream_url(track_id, quality)
        except Exception as e:
            logger.error(f"Failed to get stream URL: {e}")
            return None
    
    async def get_track(self, track_id: str, user_id: str) -> Optional[Track]:
        """Get detailed track information."""
        provider = self._get_provider()
        
        try:
            data = await provider.get_track(track_id, user_id)
            if data:
                return self._normalize_to_track(data)
            return None
        except Exception as e:
            logger.error(f"Failed to get track: {e}")
            return None
    
    async def get_album_tracks(
        self, 
        album_id: str, 
        user_id: str
    ) -> List[Track]:
        """Get all tracks in an album."""
        provider = self._get_provider()
        
        try:
            album_data = await provider.get_album(album_id, user_id)
            if album_data and "tracks" in album_data:
                return [self._normalize_to_track(t) for t in album_data["tracks"]]
            return []
        except Exception as e:
            logger.error(f"Failed to get album tracks: {e}")
            return []
    
    async def get_playlist_tracks(
        self, 
        playlist_id: str, 
        user_id: str
    ) -> List[Track]:
        """Get all tracks in a playlist."""
        provider = self._get_provider()
        
        try:
            playlist_data = await provider.get_playlist(playlist_id, user_id)
            if playlist_data and "tracks" in playlist_data:
                return [self._normalize_to_track(t) for t in playlist_data["tracks"]]
            return []
        except Exception as e:
            logger.error(f"Failed to get playlist tracks: {e}")
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
        provider = self._get_provider()
        
        try:
            tracks = await provider.get_liked_songs(user_id, limit)
            return [self._normalize_to_track(t) for t in tracks]
        except Exception as e:
            logger.error(f"Failed to get liked songs: {e}")
            return []
    
    async def get_user_playlists(
        self, 
        user_id: str
    ) -> List[Playlist]:
        """Get user's playlists."""
        provider = self._get_provider()
        
        try:
            playlists = await provider.get_user_playlists(user_id)
            return [self._normalize_to_playlist(p) for p in playlists]
        except Exception as e:
            logger.error(f"Failed to get user playlists: {e}")
            return []
    
    async def get_user_albums(
        self, 
        user_id: str
    ) -> List[Album]:
        """Get user's saved albums."""
        provider = self._get_provider()
        
        try:
            albums = await provider.get_library_albums(user_id)
            return [self._normalize_to_album(a) for a in albums]
        except Exception as e:
            logger.error(f"Failed to get user albums: {e}")
            return []
    
    async def get_user_artists(
        self, 
        user_id: str
    ) -> List[Artist]:
        """Get user's followed artists."""
        provider = self._get_provider()
        
        try:
            artists = await provider.get_library_artists(user_id)
            return [self._normalize_to_artist(a) for a in artists]
        except Exception as e:
            logger.error(f"Failed to get user artists: {e}")
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
        provider = self._get_provider()
        
        try:
            tracks = await provider.get_recommendations(user_id, limit)
            return [self._normalize_to_track(t) for t in tracks]
        except Exception as e:
            logger.error(f"Failed to get recommendations: {e}")
            return []
    
    async def get_similar_tracks(
        self, 
        track_id: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Track]:
        """Get tracks similar to a given track."""
        from services.music.recommendation_engine import get_recommendation_engine
        
        try:
            engine = get_recommendation_engine()
            tracks = await engine.get_similar(track_id, user_id, limit)
            return [self._normalize_to_track(t) for t in tracks]
        except Exception as e:
            logger.error(f"Failed to get similar tracks: {e}")
            return []
    
    # ========================================
    # Library Management
    # ========================================
    
    async def like_track(self, track_id: str, user_id: str) -> bool:
        """Like a track on YouTube Music."""
        provider = self._get_provider()
        
        try:
            client = await provider.get_client(user_id)
            if client:
                client.rate_song(track_id, "LIKE")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to like track: {e}")
            return False
    
    async def unlike_track(self, track_id: str, user_id: str) -> bool:
        """Remove like from a track."""
        provider = self._get_provider()
        
        try:
            client = await provider.get_client(user_id)
            if client:
                client.rate_song(track_id, "INDIFFERENT")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to unlike track: {e}")
            return False
    
    async def create_playlist(
        self, 
        name: str, 
        user_id: str, 
        description: Optional[str] = None
    ) -> Optional[Playlist]:
        """Create a new playlist on YouTube Music."""
        provider = self._get_provider()
        
        try:
            client = await provider.get_client(user_id)
            if client:
                playlist_id = client.create_playlist(
                    name, 
                    description or "",
                    privacy_status="PRIVATE"
                )
                if playlist_id:
                    return Playlist(
                        id=playlist_id,
                        title=name,
                        description=description,
                        track_count=0,
                        provider=ProviderType.YOUTUBE_MUSIC
                    )
            return None
        except Exception as e:
            logger.error(f"Failed to create playlist: {e}")
            return None
    
    async def add_to_playlist(
        self, 
        playlist_id: str, 
        track_ids: List[str], 
        user_id: str
    ) -> bool:
        """Add tracks to a playlist on YouTube Music."""
        provider = self._get_provider()
        
        try:
            client = await provider.get_client(user_id)
            if client:
                result = client.add_playlist_items(playlist_id, track_ids)
                return result is not None
            return False
        except Exception as e:
            logger.error(f"Failed to add to playlist: {e}")
            return False

