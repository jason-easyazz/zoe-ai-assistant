"""
Music Provider Base Class
=========================

Abstract base class for all music streaming providers.
Defines the unified interface that YouTube Music, Spotify, and Apple Music
must implement.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ProviderType(str, Enum):
    """Supported music provider types."""
    YOUTUBE_MUSIC = "youtube_music"
    SPOTIFY = "spotify"
    APPLE_MUSIC = "apple_music"


class AuthStatus(str, Enum):
    """Provider authentication status."""
    NOT_CONNECTED = "not_connected"
    CONNECTED = "connected"
    EXPIRED = "expired"
    ERROR = "error"


@dataclass
class Track:
    """
    Unified track representation across all providers.
    
    All providers must normalize their data to this format.
    """
    id: str  # Provider-specific ID
    title: str
    artist: str
    album: Optional[str] = None
    album_id: Optional[str] = None
    duration_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    provider: ProviderType = ProviderType.YOUTUBE_MUSIC
    
    # Provider-specific IDs (for cross-provider matching)
    isrc: Optional[str] = None  # International Standard Recording Code
    upc: Optional[str] = None   # Universal Product Code
    
    # Extra metadata
    release_year: Optional[int] = None
    genres: List[str] = field(default_factory=list)
    explicit: bool = False
    
    # Playback info (populated when needed)
    stream_url: Optional[str] = None
    stream_expires: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "videoId": self.id,  # Backwards compatibility
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration_seconds": self.duration_seconds,
            "thumbnailUrl": self.thumbnail_url,
            "provider": self.provider.value,
            "isrc": self.isrc,
            "explicit": self.explicit
        }


@dataclass
class Album:
    """Unified album representation."""
    id: str
    title: str
    artist: str
    thumbnail_url: Optional[str] = None
    release_year: Optional[int] = None
    track_count: Optional[int] = None
    provider: ProviderType = ProviderType.YOUTUBE_MUSIC
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "artist": self.artist,
            "thumbnail_url": self.thumbnail_url,
            "release_year": self.release_year,
            "track_count": self.track_count,
            "provider": self.provider.value
        }


@dataclass
class Artist:
    """Unified artist representation."""
    id: str
    name: str
    thumbnail_url: Optional[str] = None
    genres: List[str] = field(default_factory=list)
    provider: ProviderType = ProviderType.YOUTUBE_MUSIC
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "thumbnail_url": self.thumbnail_url,
            "genres": self.genres,
            "provider": self.provider.value
        }


@dataclass
class Playlist:
    """Unified playlist representation."""
    id: str
    title: str
    description: Optional[str] = None
    thumbnail_url: Optional[str] = None
    track_count: Optional[int] = None
    owner: Optional[str] = None
    is_public: bool = True
    provider: ProviderType = ProviderType.YOUTUBE_MUSIC
    tracks: List[Track] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "thumbnail_url": self.thumbnail_url,
            "track_count": self.track_count,
            "owner": self.owner,
            "is_public": self.is_public,
            "provider": self.provider.value
        }


class MusicProvider(ABC):
    """
    Abstract base class for music streaming providers.
    
    All providers (YouTube Music, Spotify, Apple Music) must implement
    this interface to ensure consistent behavior across the system.
    """
    
    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Return the provider type identifier."""
        pass
    
    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable provider name."""
        pass
    
    @property
    @abstractmethod
    def supports_streaming(self) -> bool:
        """Whether provider supports actual audio streaming (not just previews)."""
        pass
    
    # ========================================
    # Authentication
    # ========================================
    
    @abstractmethod
    async def get_auth_status(self, user_id: str) -> AuthStatus:
        """Check if user is authenticated with this provider."""
        pass
    
    @abstractmethod
    async def get_auth_url(self, user_id: str) -> Optional[str]:
        """Get OAuth authorization URL for connecting the provider."""
        pass
    
    @abstractmethod
    async def complete_auth(self, user_id: str, auth_code: str) -> bool:
        """Complete OAuth flow with authorization code."""
        pass
    
    @abstractmethod
    async def disconnect(self, user_id: str) -> bool:
        """Disconnect user from this provider."""
        pass
    
    # ========================================
    # Search
    # ========================================
    
    @abstractmethod
    async def search_tracks(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Track]:
        """Search for tracks."""
        pass
    
    @abstractmethod
    async def search_albums(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Album]:
        """Search for albums."""
        pass
    
    @abstractmethod
    async def search_artists(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Artist]:
        """Search for artists."""
        pass
    
    @abstractmethod
    async def search_playlists(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Playlist]:
        """Search for playlists."""
        pass
    
    # ========================================
    # Playback
    # ========================================
    
    @abstractmethod
    async def get_stream_url(
        self, 
        track_id: str, 
        quality: Optional[str] = None
    ) -> Optional[str]:
        """
        Get audio stream URL for a track.
        
        Args:
            track_id: Provider-specific track ID
            quality: 'low', 'medium', or 'high'
            
        Returns:
            Stream URL or None if unavailable
        """
        pass
    
    @abstractmethod
    async def get_track(self, track_id: str, user_id: str) -> Optional[Track]:
        """Get detailed track information."""
        pass
    
    @abstractmethod
    async def get_album_tracks(
        self, 
        album_id: str, 
        user_id: str
    ) -> List[Track]:
        """Get all tracks in an album."""
        pass
    
    @abstractmethod
    async def get_playlist_tracks(
        self, 
        playlist_id: str, 
        user_id: str
    ) -> List[Track]:
        """Get all tracks in a playlist."""
        pass
    
    # ========================================
    # User Library
    # ========================================
    
    @abstractmethod
    async def get_liked_songs(
        self, 
        user_id: str, 
        limit: int = 100
    ) -> List[Track]:
        """Get user's liked/saved songs."""
        pass
    
    @abstractmethod
    async def get_user_playlists(
        self, 
        user_id: str
    ) -> List[Playlist]:
        """Get user's playlists."""
        pass
    
    @abstractmethod
    async def get_user_albums(
        self, 
        user_id: str
    ) -> List[Album]:
        """Get user's saved albums."""
        pass
    
    @abstractmethod
    async def get_user_artists(
        self, 
        user_id: str
    ) -> List[Artist]:
        """Get user's followed artists."""
        pass
    
    # ========================================
    # Recommendations
    # ========================================
    
    @abstractmethod
    async def get_recommendations(
        self, 
        user_id: str, 
        seed_tracks: Optional[List[str]] = None,
        seed_artists: Optional[List[str]] = None,
        limit: int = 20
    ) -> List[Track]:
        """Get personalized recommendations."""
        pass
    
    @abstractmethod
    async def get_similar_tracks(
        self, 
        track_id: str, 
        user_id: str, 
        limit: int = 20
    ) -> List[Track]:
        """Get tracks similar to a given track."""
        pass
    
    # ========================================
    # Library Management
    # ========================================
    
    async def like_track(self, track_id: str, user_id: str) -> bool:
        """Like/save a track. Override if provider supports it."""
        logger.warning(f"{self.display_name} does not support liking tracks")
        return False
    
    async def unlike_track(self, track_id: str, user_id: str) -> bool:
        """Unlike/unsave a track. Override if provider supports it."""
        logger.warning(f"{self.display_name} does not support unliking tracks")
        return False
    
    async def create_playlist(
        self, 
        name: str, 
        user_id: str, 
        description: Optional[str] = None
    ) -> Optional[Playlist]:
        """Create a new playlist. Override if provider supports it."""
        logger.warning(f"{self.display_name} does not support creating playlists")
        return None
    
    async def add_to_playlist(
        self, 
        playlist_id: str, 
        track_ids: List[str], 
        user_id: str
    ) -> bool:
        """Add tracks to a playlist. Override if provider supports it."""
        logger.warning(f"{self.display_name} does not support adding to playlists")
        return False

