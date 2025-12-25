"""
YouTube Music Provider
======================

Integration with YouTube Music using ytmusicapi.
Provides search, playback URLs, playlists, and user library access.

Features:
- Rate limiting to avoid throttling
- Stream URL caching (URLs valid ~5 hours)
- Platform-aware audio quality
- Per-user authentication
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from functools import lru_cache

logger = logging.getLogger(__name__)

# Check for optional dependencies
try:
    from ytmusicapi import YTMusic
    YTMUSIC_AVAILABLE = True
except ImportError:
    YTMUSIC_AVAILABLE = False
    logger.warning("ytmusicapi not installed - YouTube Music will be unavailable")

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False
    logger.warning("yt-dlp not installed - stream URLs will be unavailable")


class YouTubeMusicProvider:
    """
    YouTube Music integration provider.
    
    Handles:
    - Searching tracks, albums, playlists
    - Getting stream URLs for playback
    - User library and playlists
    - Rate limiting and caching
    """
    
    def __init__(self, auth_manager):
        """
        Initialize the YouTube Music provider.
        
        Args:
            auth_manager: MusicAuthManager instance for credential storage
        """
        self.auth_manager = auth_manager
        self._clients: Dict[str, Any] = {}  # Per-user YTMusic clients
        self._stream_cache: Dict[str, tuple] = {}  # {video_id: (url, expires_at)}
        self._last_request = datetime.min
        self._min_interval = 0.5  # Rate limit: 2 requests/second max
        
        # Platform-aware quality settings
        self._init_quality_settings()
        
        # yt-dlp options
        self.ydl_opts = {
            'format': f'bestaudio[abr<={self.audio_bitrate}]/bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'nocheckcertificate': True,
        }
    
    def _init_quality_settings(self):
        """Set audio quality based on platform capabilities."""
        try:
            from model_config import detect_hardware
            platform = detect_hardware()
            
            if platform == "pi5":
                self.audio_bitrate = "128"
                self.audio_format = "opus"
                logger.info("YouTube Music: Using 128kbps for Pi5")
            elif platform == "jetson":
                self.audio_bitrate = "256"
                self.audio_format = "opus"
                logger.info("YouTube Music: Using 256kbps for Jetson")
            else:
                self.audio_bitrate = "192"
                self.audio_format = "opus"
        except ImportError:
            self.audio_bitrate = "192"
            self.audio_format = "opus"
    
    async def _rate_limit(self):
        """Apply rate limiting between requests."""
        elapsed = (datetime.now() - self._last_request).total_seconds()
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request = datetime.now()
    
    async def get_client(self, user_id: str) -> Optional[Any]:
        """
        Get or create a YTMusic client for a user.
        
        Args:
            user_id: User identifier
            
        Returns:
            YTMusic client or None if not authenticated
        """
        if not YTMUSIC_AVAILABLE:
            logger.error("ytmusicapi not available")
            return None
        
        if user_id in self._clients:
            return self._clients[user_id]
        
        # Get auth data from storage
        auth_data = await self.auth_manager.get_auth(user_id, "youtube_music")
        
        if not auth_data:
            logger.warning(f"No YouTube Music auth for user {user_id}")
            return None
        
        try:
            # ytmusicapi 1.5.0+ requires OAuthCredentials when using OAuth JSON
            from ytmusicapi.auth.oauth import OAuthCredentials
            
            # Create OAuth credentials with Google's client info (used by ytmusicapi)
            oauth_creds = OAuthCredentials(
                client_id="REDACTED_CLIENT_ID",
                client_secret="REDACTED_CLIENT_SECRET"
            )
            
            # Create YTMusic client with both auth dict and oauth_credentials
            client = YTMusic(auth=auth_data, oauth_credentials=oauth_creds)
            
            self._clients[user_id] = client
            logger.info(f"Created YTMusic client for user {user_id}")
            return client
                    
        except Exception as e:
            logger.error(f"Failed to create YTMusic client: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def is_authenticated(self, user_id: str) -> bool:
        """Check if user has valid YouTube Music authentication."""
        client = await self.get_client(user_id)
        return client is not None
    
    async def search(
        self,
        query: str,
        user_id: str,
        filter_type: str = "songs",
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search YouTube Music.
        
        Search works without authentication using public API.
        User auth is only needed for library/playlist access.
        
        Args:
            query: Search query
            user_id: User identifier
            filter_type: Type filter (songs, albums, artists, playlists, videos)
            limit: Max results to return
            
        Returns:
            List of search results
        """
        await self._rate_limit()
        
        try:
            # Use unauthenticated client for search (works better)
            if not hasattr(self, '_public_client'):
                self._public_client = YTMusic()
                logger.info("Created public YTMusic client for search")
            
            results = self._public_client.search(query, filter=filter_type, limit=limit)
            
            # Normalize results
            normalized = []
            for item in results:
                normalized.append(self._normalize_track(item))
            
            return normalized
            
        except Exception as e:
            logger.error(f"YouTube Music search failed: {e}")
            return self._mock_search_results(query, filter_type, limit)
    
    def _normalize_track(self, item: Dict) -> Dict[str, Any]:
        """Normalize a YouTube Music item to standard format."""
        # Handle different result types
        video_id = item.get("videoId") or item.get("id")
        
        # Get artist info
        artists = item.get("artists", [])
        artist_name = artists[0]["name"] if artists else "Unknown Artist"
        
        # Get album info
        album = item.get("album", {})
        album_name = album.get("name") if isinstance(album, dict) else album
        
        # Get thumbnail
        thumbnails = item.get("thumbnails", [])
        thumbnail_url = thumbnails[-1]["url"] if thumbnails else None
        
        return {
            "id": video_id,
            "videoId": video_id,
            "title": item.get("title", "Unknown"),
            "artist": artist_name,
            "artists": artists,
            "album": album_name,
            "duration": item.get("duration"),
            "duration_seconds": item.get("duration_seconds"),
            "thumbnail_url": thumbnail_url,
            "is_explicit": item.get("isExplicit", False),
            "result_type": item.get("resultType", "song"),
            "provider": "youtube_music"
        }
    
    def _mock_search_results(self, query: str, filter_type: str, limit: int) -> List[Dict]:
        """Return mock results when not authenticated."""
        return [
            {
                "id": f"mock_{i}",
                "videoId": f"mock_{i}",
                "title": f"Mock Result {i}: {query}",
                "artist": "Demo Artist",
                "album": "Demo Album",
                "duration": "3:30",
                "duration_seconds": 210,
                "thumbnail_url": None,
                "provider": "youtube_music",
                "is_mock": True
            }
            for i in range(min(3, limit))
        ]
    
    async def get_stream_url(self, video_id: str) -> Optional[str]:
        """
        Get the stream URL for a track.
        
        Uses caching to avoid repeated extraction (URLs valid ~5 hours).
        
        Args:
            video_id: YouTube video ID
            
        Returns:
            Stream URL or None if extraction fails
        """
        if not YTDLP_AVAILABLE:
            logger.error("yt-dlp not available for stream extraction")
            return None
        
        # Check cache
        if video_id in self._stream_cache:
            url, expires = self._stream_cache[video_id]
            if datetime.now() < expires:
                logger.debug(f"Stream URL cache hit for {video_id}")
                return url
        
        await self._rate_limit()
        
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                url = f"https://music.youtube.com/watch?v={video_id}"
                info = ydl.extract_info(url, download=False)
                stream_url = info.get("url")
                
                if stream_url:
                    # Cache for 5 hours
                    self._stream_cache[video_id] = (
                        stream_url,
                        datetime.now() + timedelta(hours=5)
                    )
                    logger.debug(f"Extracted and cached stream URL for {video_id}")
                
                return stream_url
                
        except Exception as e:
            logger.error(f"Failed to extract stream URL for {video_id}: {e}")
            return None
    
    async def get_stream_url_video(self, video_id: str, track_info: Optional[Dict] = None) -> Optional[Dict[str, str]]:
        """
        Get video stream URL for a track (for video playback).
        
        YouTube Music tracks are often audio-only with album art.
        For actual music videos, we search YouTube for the official video.
        
        Args:
            video_id: YouTube video ID (from YouTube Music)
            track_info: Optional track metadata (title, artist) to search for video
            
        Returns:
            Dict with stream URLs or None if extraction fails
        """
        if not YTDLP_AVAILABLE:
            logger.error("yt-dlp not available for video stream extraction")
            return None
        
        cache_key = f"{video_id}_video"
        
        # Check cache
        if cache_key in self._stream_cache:
            data, expires = self._stream_cache[cache_key]
            if datetime.now() < expires:
                logger.debug(f"Video stream URL cache hit for {video_id}")
                return data
        
        await self._rate_limit()
        
        try:
            # Options for video stream - use format with video+audio in single stream
            # YouTube only provides combined video+audio at 360p (format 18)
            # Higher quality formats are video-only (require HLS which has CORS issues)
            # Use format 18 for browser compatibility, fall back to best available
            video_opts = {
                'format': '18/best[height<=480][vcodec!=none][acodec!=none]/best[vcodec!=none][acodec!=none]',
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'nocheckcertificate': True,
            }
            
            # First, try to search YouTube for the official music video
            search_query = None
            if track_info:
                title = track_info.get('title', '')
                artist = track_info.get('artist', '')
                if title and artist:
                    search_query = f"{artist} - {title} official music video"
            
            actual_video_id = video_id
            
            if search_query:
                # Search YouTube for the music video
                search_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': True,
                    'default_search': 'ytsearch1',  # Get first result
                }
                try:
                    with yt_dlp.YoutubeDL(search_opts) as ydl:
                        search_result = ydl.extract_info(f"ytsearch1:{search_query}", download=False)
                        if search_result and 'entries' in search_result and search_result['entries']:
                            found_id = search_result['entries'][0].get('id')
                            if found_id:
                                actual_video_id = found_id
                                logger.info(f"Found music video for '{search_query}': {actual_video_id}")
                except Exception as e:
                    logger.warning(f"Music video search failed, using original ID: {e}")
            
            with yt_dlp.YoutubeDL(video_opts) as ydl:
                url = f"https://www.youtube.com/watch?v={actual_video_id}"
                info = ydl.extract_info(url, download=False)
                
                result = {
                    'url': info.get('url'),
                    'title': info.get('title'),
                    'duration': info.get('duration'),
                    'thumbnail': info.get('thumbnail'),
                    'webpage_url': info.get('webpage_url', f"https://www.youtube.com/watch?v={actual_video_id}"),
                    'video_id': actual_video_id,
                    'is_music_video': actual_video_id != video_id  # True if we found an actual music video
                }
                
                if result['url']:
                    # Cache for 5 hours
                    self._stream_cache[cache_key] = (
                        result,
                        datetime.now() + timedelta(hours=5)
                    )
                    logger.debug(f"Extracted video stream URL for {video_id}")
                
                return result
                
        except Exception as e:
            logger.error(f"Failed to extract video stream URL for {video_id}: {e}")
            return None
    
    async def get_track(self, video_id: str, user_id: str) -> Optional[Dict]:
        """Get detailed track information."""
        client = await self.get_client(user_id)
        
        if not client:
            return None
        
        await self._rate_limit()
        
        try:
            # Get song info
            info = client.get_song(video_id)
            return self._normalize_track(info.get("videoDetails", info))
        except Exception as e:
            logger.error(f"Failed to get track {video_id}: {e}")
            return None
    
    async def get_playlist(self, playlist_id: str, user_id: str) -> Optional[Dict]:
        """Get playlist with tracks."""
        client = await self.get_client(user_id)
        
        if not client:
            return None
        
        await self._rate_limit()
        
        try:
            playlist = client.get_playlist(playlist_id)
            
            # Normalize tracks
            tracks = []
            for track in playlist.get("tracks", []):
                tracks.append(self._normalize_track(track))
            
            return {
                "id": playlist_id,
                "title": playlist.get("title"),
                "description": playlist.get("description"),
                "author": playlist.get("author", {}).get("name"),
                "track_count": len(tracks),
                "tracks": tracks,
                "thumbnail_url": playlist.get("thumbnails", [{}])[-1].get("url"),
                "provider": "youtube_music"
            }
            
        except Exception as e:
            logger.error(f"Failed to get playlist {playlist_id}: {e}")
            return None
    
    async def get_user_playlists(self, user_id: str) -> List[Dict]:
        """Get user's library playlists."""
        client = await self.get_client(user_id)
        
        if not client:
            return []
        
        await self._rate_limit()
        
        try:
            playlists = client.get_library_playlists()
            
            return [
                {
                    "id": p.get("playlistId"),
                    "title": p.get("title"),
                    "track_count": p.get("count"),
                    "thumbnail_url": p.get("thumbnails", [{}])[-1].get("url"),
                    "provider": "youtube_music"
                }
                for p in playlists
            ]
            
        except Exception as e:
            logger.error(f"Failed to get user playlists: {e}")
            return []
    
    async def get_recommendations(self, user_id: str, limit: int = 20) -> List[Dict]:
        """Get personalized recommendations."""
        client = await self.get_client(user_id)
        
        if not client:
            return []
        
        await self._rate_limit()
        
        try:
            home = client.get_home(limit=limit)
            
            tracks = []
            for section in home:
                for item in section.get("contents", []):
                    if item.get("videoId"):
                        tracks.append(self._normalize_track(item))
                    if len(tracks) >= limit:
                        break
                if len(tracks) >= limit:
                    break
            
            return tracks
            
        except Exception as e:
            logger.error(f"Failed to get recommendations: {e}")
            return []
    
    def clear_cache(self):
        """Clear the stream URL cache."""
        self._stream_cache.clear()
        logger.info("Cleared YouTube Music stream cache")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        now = datetime.now()
        valid_entries = sum(
            1 for _, (_, expires) in self._stream_cache.items()
            if expires > now
        )
        
        return {
            "total_entries": len(self._stream_cache),
            "valid_entries": valid_entries,
            "expired_entries": len(self._stream_cache) - valid_entries
        }


# Singleton instance
_youtube_music: Optional[YouTubeMusicProvider] = None


def get_youtube_music() -> YouTubeMusicProvider:
    """Get the singleton YouTube Music provider instance."""
    global _youtube_music
    if _youtube_music is None:
        from services.music.auth_manager import get_auth_manager
        _youtube_music = YouTubeMusicProvider(get_auth_manager())
    return _youtube_music

