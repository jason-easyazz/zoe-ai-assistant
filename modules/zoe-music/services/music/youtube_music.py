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
            
            # Create OAuth credentials from environment variables
            # These are the YouTube Music TV client credentials
            oauth_client_id = os.getenv("YTMUSIC_CLIENT_ID", "")
            oauth_client_secret = os.getenv("YTMUSIC_CLIENT_SECRET", "")
            
            if not oauth_client_id or not oauth_client_secret:
                logger.warning("YouTube Music OAuth credentials not configured in environment")
                return None
            
            oauth_creds = OAuthCredentials(
                client_id=oauth_client_id,
                client_secret=oauth_client_secret
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
    
    async def get_stream_url(self, video_id: str, quality: Optional[str] = None) -> Optional[str]:
        """
        Get the stream URL for a track.
        
        Uses caching to avoid repeated extraction (URLs valid ~5 hours).
        
        Args:
            video_id: YouTube video ID
            quality: Optional quality override ('low', 'medium', 'high')
            
        Returns:
            Stream URL or None if extraction fails
        """
        if not YTDLP_AVAILABLE:
            logger.error("yt-dlp not available for stream extraction")
            return None
        
        # Map quality preference to bitrate
        quality_bitrates = {
            "low": "128",
            "medium": "192", 
            "high": "256"
        }
        bitrate = quality_bitrates.get(quality, self.audio_bitrate)
        
        # Use quality-specific cache key
        cache_key = f"{video_id}_{bitrate}"
        
        # Check cache
        if cache_key in self._stream_cache:
            url, expires = self._stream_cache[cache_key]
            if datetime.now() < expires:
                logger.debug(f"Stream URL cache hit for {video_id} at {bitrate}kbps")
                return url
        
        await self._rate_limit()
        
        try:
            # Build yt-dlp options with quality preference
            ydl_opts = {
                'format': f'bestaudio[abr<={bitrate}]/bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'nocheckcertificate': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                url = f"https://music.youtube.com/watch?v={video_id}"
                info = ydl.extract_info(url, download=False)
                stream_url = info.get("url")
                
                if stream_url:
                    # Cache for 4 hours (refresh before expiry)
                    self._stream_cache[cache_key] = (
                        stream_url,
                        datetime.now() + timedelta(hours=4)
                    )
                    logger.debug(f"Extracted and cached stream URL for {video_id} at {bitrate}kbps")
                
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
    
    async def get_liked_songs(self, user_id: str, limit: int = 100) -> List[Dict]:
        """
        Get user's liked songs from YouTube Music.
        
        Args:
            user_id: User identifier
            limit: Maximum number of songs to return
            
        Returns:
            List of liked tracks
        """
        client = await self.get_client(user_id)
        
        if not client:
            return []
        
        await self._rate_limit()
        
        try:
            liked = client.get_liked_songs(limit=limit)
            
            tracks = []
            for track in liked.get("tracks", []):
                if track.get("videoId"):
                    tracks.append(self._normalize_track(track))
            
            logger.info(f"Got {len(tracks)} liked songs for user {user_id}")
            return tracks
            
        except Exception as e:
            logger.error(f"Failed to get liked songs: {e}")
            return []
    
    async def get_library_albums(self, user_id: str, limit: int = 25) -> List[Dict]:
        """Get user's saved albums."""
        client = await self.get_client(user_id)
        
        if not client:
            return []
        
        await self._rate_limit()
        
        try:
            albums = client.get_library_albums(limit=limit)
            
            return [
                {
                    "id": a.get("browseId"),
                    "title": a.get("title"),
                    "artist": a.get("artists", [{}])[0].get("name") if a.get("artists") else "",
                    "thumbnail_url": a.get("thumbnails", [{}])[-1].get("url") if a.get("thumbnails") else None,
                    "year": a.get("year"),
                    "provider": "youtube_music"
                }
                for a in albums
            ]
            
        except Exception as e:
            logger.error(f"Failed to get library albums: {e}")
            return []
    
    async def get_library_artists(self, user_id: str, limit: int = 25) -> List[Dict]:
        """Get user's followed artists."""
        client = await self.get_client(user_id)
        
        if not client:
            return []
        
        await self._rate_limit()
        
        try:
            artists = client.get_library_artists(limit=limit)
            
            return [
                {
                    "id": a.get("browseId"),
                    "name": a.get("artist"),
                    "thumbnail_url": a.get("thumbnails", [{}])[-1].get("url") if a.get("thumbnails") else None,
                    "provider": "youtube_music"
                }
                for a in artists
            ]
            
        except Exception as e:
            logger.error(f"Failed to get library artists: {e}")
            return []
    
    async def sync_library_to_local(self, user_id: str) -> Dict[str, Any]:
        """
        Sync user's YouTube Music library to local database.
        
        This enables offline recommendations and faster browsing.
        
        Returns:
            Sync summary with counts
        """
        import sqlite3
        db_path = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
        
        summary = {
            "liked_songs": 0,
            "playlists": 0,
            "albums": 0,
            "artists": 0,
            "errors": []
        }
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Ensure tables exist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS music_library_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    title TEXT,
                    artist TEXT,
                    album TEXT,
                    thumbnail_url TEXT,
                    duration_seconds INTEGER,
                    provider TEXT DEFAULT 'youtube_music',
                    source TEXT DEFAULT 'liked',
                    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, track_id, provider)
                )
            """)
            
            # Sync liked songs
            try:
                liked = await self.get_liked_songs(user_id, limit=500)
                for track in liked:
                    cursor.execute("""
                        INSERT OR REPLACE INTO music_library_tracks
                        (user_id, track_id, title, artist, album, thumbnail_url, duration_seconds, provider, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'youtube_music', 'liked')
                    """, (
                        user_id,
                        track.get("videoId") or track.get("id"),
                        track.get("title"),
                        track.get("artist"),
                        track.get("album"),
                        track.get("thumbnailUrl"),
                        track.get("duration_seconds")
                    ))
                summary["liked_songs"] = len(liked)
            except Exception as e:
                summary["errors"].append(f"Liked songs: {str(e)}")
            
            # Sync playlists
            try:
                playlists = await self.get_user_playlists(user_id)
                summary["playlists"] = len(playlists)
            except Exception as e:
                summary["errors"].append(f"Playlists: {str(e)}")
            
            conn.commit()
            conn.close()
            
            logger.info(f"Library sync complete for {user_id}: {summary}")
            return summary
            
        except Exception as e:
            logger.error(f"Library sync failed: {e}")
            summary["errors"].append(str(e))
            return summary
    
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
    
    def is_stream_expiring_soon(self, video_id: str, threshold_minutes: int = 30) -> bool:
        """
        Check if a cached stream URL is expiring soon.
        
        Args:
            video_id: Video ID to check
            threshold_minutes: How close to expiry counts as "soon"
            
        Returns:
            True if stream is cached but expiring within threshold
        """
        for cache_key, (_, expires) in self._stream_cache.items():
            if cache_key.startswith(video_id):
                time_until_expiry = (expires - datetime.now()).total_seconds() / 60
                if 0 < time_until_expiry < threshold_minutes:
                    return True
        return False
    
    async def refresh_stream_if_expiring(self, video_id: str, quality: Optional[str] = None) -> Optional[str]:
        """
        Refresh stream URL if it's expiring soon.
        
        This is called proactively to ensure smooth playback.
        
        Args:
            video_id: Video ID to refresh
            quality: Quality preference
            
        Returns:
            Fresh stream URL or None
        """
        if self.is_stream_expiring_soon(video_id, threshold_minutes=45):
            logger.info(f"Proactively refreshing expiring stream URL for {video_id}")
            
            # Remove old cached entry
            quality_bitrates = {"low": "128", "medium": "192", "high": "256"}
            bitrate = quality_bitrates.get(quality, self.audio_bitrate)
            cache_key = f"{video_id}_{bitrate}"
            
            if cache_key in self._stream_cache:
                del self._stream_cache[cache_key]
            
            # Get fresh URL
            return await self.get_stream_url(video_id, quality)
        
        return None


# Singleton instance
_youtube_music: Optional[YouTubeMusicProvider] = None


def get_youtube_music() -> YouTubeMusicProvider:
    """Get the singleton YouTube Music provider instance."""
    global _youtube_music
    if _youtube_music is None:
        from services.music.auth_manager import get_auth_manager
        _youtube_music = YouTubeMusicProvider(get_auth_manager())
    return _youtube_music

