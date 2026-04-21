"""
Music Provider Registry
=======================

Singleton registry that manages all music provider instances.
Handles provider selection based on user preferences or track source.
"""

import logging
from typing import Dict, List, Optional, Type
from .base import MusicProvider, ProviderType, AuthStatus, Track

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """
    Central registry for music providers.
    
    Manages provider instances and handles intelligent routing
    of requests based on user preferences and track source.
    """
    
    def __init__(self):
        self._providers: Dict[ProviderType, MusicProvider] = {}
        self._initialized = False
    
    def register(self, provider: MusicProvider) -> None:
        """
        Register a music provider.
        
        Args:
            provider: MusicProvider instance to register
        """
        self._providers[provider.provider_type] = provider
        logger.info(f"Registered music provider: {provider.display_name}")
    
    def get(self, provider_type: ProviderType) -> Optional[MusicProvider]:
        """
        Get a specific provider by type.
        
        Args:
            provider_type: The provider type to get
            
        Returns:
            MusicProvider instance or None if not registered
        """
        return self._providers.get(provider_type)
    
    def get_all(self) -> List[MusicProvider]:
        """Get all registered providers."""
        return list(self._providers.values())
    
    def get_available(self) -> List[MusicProvider]:
        """Get providers that support streaming."""
        return [p for p in self._providers.values() if p.supports_streaming]
    
    async def get_preferred_provider(self, user_id: str) -> Optional[MusicProvider]:
        """
        Get user's preferred music provider.
        
        Falls back to first available if preference not set or unavailable.
        
        Args:
            user_id: User identifier
            
        Returns:
            MusicProvider instance or None
        """
        # Get user preference
        try:
            from preference_learner import preference_learner
            prefs = await preference_learner.get_music_preferences(user_id)
            preferred = prefs.get("preferred_provider", "youtube_music")
            
            # Map string to enum
            provider_type = ProviderType(preferred)
            provider = self._providers.get(provider_type)
            
            if provider:
                # Check if authenticated
                status = await provider.get_auth_status(user_id)
                if status == AuthStatus.CONNECTED:
                    return provider
                else:
                    logger.info(f"Preferred provider {preferred} not authenticated")
        except Exception as e:
            logger.warning(f"Failed to get preferred provider: {e}")
        
        # Fall back to first connected provider
        for provider in self._providers.values():
            try:
                status = await provider.get_auth_status(user_id)
                if status == AuthStatus.CONNECTED:
                    return provider
            except Exception:
                continue
        
        return None
    
    async def get_provider_for_track(
        self, 
        track_id: str, 
        user_id: str
    ) -> Optional[MusicProvider]:
        """
        Get the appropriate provider for a track.
        
        Uses track ID prefix or metadata to determine source provider.
        
        Args:
            track_id: Track identifier (may include provider prefix)
            user_id: User identifier
            
        Returns:
            Appropriate MusicProvider or None
        """
        # Check for provider prefix (e.g., "spotify:track:xxx")
        if ":" in track_id:
            prefix = track_id.split(":")[0].lower()
            
            if prefix == "spotify":
                return self._providers.get(ProviderType.SPOTIFY)
            elif prefix == "apple":
                return self._providers.get(ProviderType.APPLE_MUSIC)
            elif prefix in ("youtube", "yt"):
                return self._providers.get(ProviderType.YOUTUBE_MUSIC)
        
        # No prefix - use preferred provider
        return await self.get_preferred_provider(user_id)
    
    async def search_all_providers(
        self, 
        query: str, 
        user_id: str, 
        limit: int = 10
    ) -> Dict[ProviderType, List[Track]]:
        """
        Search across all connected providers.
        
        Args:
            query: Search query
            user_id: User identifier
            limit: Results per provider
            
        Returns:
            Dict mapping provider types to search results
        """
        results = {}
        
        for provider in self._providers.values():
            try:
                status = await provider.get_auth_status(user_id)
                if status == AuthStatus.CONNECTED:
                    tracks = await provider.search_tracks(query, user_id, limit)
                    results[provider.provider_type] = tracks
            except Exception as e:
                logger.warning(f"Search failed for {provider.display_name}: {e}")
        
        return results
    
    async def get_auth_status_all(
        self, 
        user_id: str
    ) -> Dict[ProviderType, AuthStatus]:
        """
        Get authentication status for all providers.
        
        Args:
            user_id: User identifier
            
        Returns:
            Dict mapping provider types to auth status
        """
        statuses = {}
        
        for provider in self._providers.values():
            try:
                status = await provider.get_auth_status(user_id)
                statuses[provider.provider_type] = status
            except Exception as e:
                logger.warning(f"Auth check failed for {provider.display_name}: {e}")
                statuses[provider.provider_type] = AuthStatus.ERROR
        
        return statuses
    
    def initialize(self) -> None:
        """
        Initialize all available providers.
        
        Called on startup to register default providers.
        """
        if self._initialized:
            return
        
        # Register YouTube Music (always available)
        try:
            from .youtube import YouTubeMusicAdapter
            self.register(YouTubeMusicAdapter())
            logger.info("YouTube Music provider initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize YouTube Music: {e}")
        
        # Register Spotify (if configured)
        try:
            from .spotify import SpotifyProvider
            self.register(SpotifyProvider())
            logger.info("Spotify provider initialized")
        except ImportError:
            logger.debug("Spotify provider not available (spotipy not installed)")
        except Exception as e:
            logger.warning(f"Failed to initialize Spotify: {e}")
        
        # Register Apple Music (if configured)
        try:
            from .apple import AppleMusicProvider
            self.register(AppleMusicProvider())
            logger.info("Apple Music provider initialized")
        except ImportError:
            logger.debug("Apple Music provider not available")
        except Exception as e:
            logger.warning(f"Failed to initialize Apple Music: {e}")
        
        self._initialized = True
        logger.info(f"Provider registry initialized with {len(self._providers)} providers")


# Singleton instance
_registry: Optional[ProviderRegistry] = None


def get_provider_registry() -> ProviderRegistry:
    """Get the singleton provider registry instance."""
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
        _registry.initialize()
    return _registry

