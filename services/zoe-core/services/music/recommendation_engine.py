"""
Music Recommendation Engine
===========================

Dual-implementation recommendation system:
- MetadataRecommendationEngine: Uses YouTube Music search + affinity (Pi5 + Jetson)
- MLRecommendationEngine: Uses audio embeddings + FAISS (Jetson only)

Both engines share the same interface, allowing seamless platform switching.

Recommendation Types:
- Similar: Find tracks similar to a given track
- Radio: Generate personalized radio from seed track or user taste
- Discover: Find new music matching user's taste (not in history)
- Mood: Match current listening mood (ML only, falls back on Pi5)
"""

import sqlite3
import logging
import os
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any, Set
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")


class BaseRecommendationEngine(ABC):
    """
    Abstract base class for recommendation engines.
    
    Platform-specific implementations inherit from this and implement
    the abstract methods with appropriate algorithms.
    """
    
    @abstractmethod
    async def get_similar(
        self,
        track_id: str,
        user_id: str,
        limit: int = 10
    ) -> List[Dict]:
        """
        Find tracks similar to the given track.
        
        Args:
            track_id: Seed track ID
            user_id: User for personalization
            limit: Max tracks to return
            
        Returns:
            List of similar track dicts
        """
        pass
    
    @abstractmethod
    async def get_radio(
        self,
        user_id: str,
        seed_track_id: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict]:
        """
        Generate a personal radio queue.
        
        Args:
            user_id: User identifier
            seed_track_id: Optional seed track (uses user taste if None)
            limit: Max tracks to return
            
        Returns:
            List of track dicts for radio queue
        """
        pass
    
    @abstractmethod
    async def get_discover(
        self,
        user_id: str,
        limit: int = 20
    ) -> List[Dict]:
        """
        Find new music matching user's taste.
        
        Args:
            user_id: User identifier
            limit: Max tracks to return
            
        Returns:
            List of novel track dicts
        """
        pass
    
    @abstractmethod
    async def get_mood_match(
        self,
        user_id: str,
        limit: int = 20
    ) -> List[Dict]:
        """
        Match current listening mood.
        
        Args:
            user_id: User identifier
            limit: Max tracks to return
            
        Returns:
            List of mood-matched track dicts
        """
        pass


class MetadataRecommendationEngine(BaseRecommendationEngine):
    """
    Recommendation engine using YouTube Music search + affinity scoring.
    
    Works on all platforms (Pi5 and Jetson). Uses YouTube Music's own
    recommendation APIs (watch playlist, related tracks) combined with
    user affinity scoring for personalization.
    """
    
    def __init__(self):
        """Initialize with lazy-loaded dependencies."""
        self._youtube = None
        self._affinity = None
    
    @property
    def youtube(self):
        """Lazy load YouTube Music provider."""
        if self._youtube is None:
            from .youtube_music import get_youtube_music
            self._youtube = get_youtube_music()
        return self._youtube
    
    @property
    def affinity(self):
        """Lazy load affinity engine."""
        if self._affinity is None:
            from .affinity_engine import get_affinity_engine
            self._affinity = get_affinity_engine()
        return self._affinity
    
    async def get_similar(
        self,
        track_id: str,
        user_id: str,
        limit: int = 10
    ) -> List[Dict]:
        """
        Find similar tracks using YouTube Music's watch playlist.
        
        Falls back to search if watch playlist fails.
        """
        related = []
        
        # Try YouTube Music's related tracks feature
        try:
            client = await self.youtube.get_client(user_id)
            if client:
                watch = client.get_watch_playlist(track_id)
                if watch and "tracks" in watch:
                    # Skip first track (current track)
                    related = [
                        self.youtube._normalize_track(t) 
                        for t in watch["tracks"][1:limit+5]
                    ]
        except Exception as e:
            logger.warning(f"Watch playlist failed: {e}")
        
        # Fallback: search for similar
        if not related:
            track_info = await self._get_track_info(track_id)
            if track_info and (track_info.get('title') or track_info.get('artist')):
                queries = [
                    f"songs like {track_info.get('title', '')}",
                    f"{track_info.get('artist', '')} similar",
                ]
                for query in queries:
                    results = await self.youtube.search(query, user_id, limit=5)
                    related.extend(results)
        
        # Ultimate fallback: search for trending music
        if not related:
            logger.info(f"No similar tracks found for {track_id}, using trending fallback")
            related = await self.youtube.search("top hits 2024", user_id, limit=limit)
        
        # Dedupe and rank by affinity
        unique = self._dedupe_tracks(related)
        ranked = self.affinity.rank_tracks_by_affinity(unique, user_id)
        
        return ranked[:limit]
    
    async def get_radio(
        self,
        user_id: str,
        seed_track_id: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict]:
        """
        Generate personal radio.
        
        If seed_track_id provided, builds radio around that track.
        Otherwise uses user's top tracks as seeds.
        """
        if seed_track_id:
            # Radio based on specific track
            return await self.get_similar(seed_track_id, user_id, limit)
        
        # Radio based on user's top tracks
        top_tracks = self.affinity.get_top_tracks(user_id, limit=5)
        
        if not top_tracks:
            # New user - return trending/popular
            return await self.youtube.search("popular music 2024", user_id, limit=limit)
        
        # Get similar tracks for each top track
        all_similar = []
        for track_id, score in top_tracks[:3]:
            similar = await self.get_similar(track_id, user_id, limit=10)
            all_similar.extend(similar)
        
        # Dedupe, rank, and apply diversity filter
        unique = self._dedupe_tracks(all_similar)
        ranked = self.affinity.rank_tracks_by_affinity(unique, user_id)
        diverse = self._apply_diversity_filter(ranked)
        
        return diverse[:limit]
    
    async def get_discover(
        self,
        user_id: str,
        limit: int = 20
    ) -> List[Dict]:
        """
        Find new music user hasn't heard that matches their taste.
        """
        # Get user's top artists
        top_artists = self.affinity.get_top_artists(user_id, limit=5)
        
        if not top_artists:
            # New user - return new releases
            return await self.youtube.search("new music 2024", user_id, limit=limit)
        
        # Search for similar artists and new music
        discoveries = []
        for artist, score in top_artists[:3]:
            results = await self.youtube.search(
                f"artists similar to {artist}", user_id, limit=10
            )
            discoveries.extend(results)
        
        # Filter out already-played tracks
        played = await self._get_played_tracks(user_id)
        novel = [t for t in discoveries if t.get("videoId") not in played]
        
        # Dedupe and add exploration bonus
        unique = self._dedupe_tracks(novel)
        ranked = self.affinity.rank_tracks_by_affinity(
            unique, user_id, exploration_bonus=1.0  # Higher bonus for discovery
        )
        
        return ranked[:limit]
    
    async def get_mood_match(
        self,
        user_id: str,
        limit: int = 20
    ) -> List[Dict]:
        """
        Match current listening mood.
        
        On metadata engine, uses recent tracks as context.
        """
        # Get recent tracks to infer mood
        recent = await self._get_recent_tracks(user_id, limit=3)
        
        if not recent:
            # No recent tracks, return radio
            return await self.get_radio(user_id, limit=limit)
        
        # Use most recent track as mood anchor
        return await self.get_similar(recent[0], user_id, limit)
    
    def _dedupe_tracks(self, tracks: List[Dict]) -> List[Dict]:
        """Remove duplicate tracks by videoId."""
        seen = set()
        unique = []
        for track in tracks:
            track_id = track.get("videoId") or track.get("id")
            if track_id and track_id not in seen:
                seen.add(track_id)
                unique.append(track)
        return unique
    
    def _apply_diversity_filter(
        self,
        tracks: List[Dict],
        max_per_artist: int = 2
    ) -> List[Dict]:
        """
        Apply diversity filter to avoid same artist back-to-back.
        
        Limits tracks per artist and spreads them out.
        """
        artist_counts: Dict[str, int] = {}
        diverse = []
        
        for track in tracks:
            artist = track.get("artist", "Unknown")
            
            if artist_counts.get(artist, 0) < max_per_artist:
                diverse.append(track)
                artist_counts[artist] = artist_counts.get(artist, 0) + 1
        
        return diverse
    
    async def _get_track_info(self, track_id: str) -> Optional[Dict]:
        """Get track metadata from cache."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT track_title, artist, album FROM music_playback_state
                WHERE track_id = ? LIMIT 1
            """, (track_id,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return {"title": row[0], "artist": row[1], "album": row[2]}
        except Exception as e:
            logger.warning(f"Failed to get track info: {e}")
        
        return None
    
    async def _get_played_tracks(self, user_id: str) -> Set[str]:
        """Get set of track IDs user has played."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT track_id FROM music_events WHERE user_id = ?
            """, (user_id,))
            tracks = {row[0] for row in cursor.fetchall()}
            conn.close()
            return tracks
        except Exception as e:
            logger.warning(f"Failed to get played tracks: {e}")
            return set()
    
    async def _get_recent_tracks(self, user_id: str, limit: int = 5) -> List[str]:
        """Get recently played track IDs."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT track_id 
                FROM music_events 
                WHERE user_id = ? AND event_type IN ('play_start', 'play_end')
                ORDER BY timestamp DESC
                LIMIT ?
            """, (user_id, limit))
            tracks = [row[0] for row in cursor.fetchall()]
            conn.close()
            return tracks
        except Exception as e:
            logger.warning(f"Failed to get recent tracks: {e}")
            return []


class MLRecommendationEngine(MetadataRecommendationEngine):
    """
    ML-enhanced recommendation engine using audio embeddings + FAISS.
    
    Jetson only. Falls back to metadata recommendations if ML components
    are not available or fail.
    
    Enhancements over metadata engine:
    - Audio-based similarity via CLAP embeddings
    - True "vibe" matching from audio features
    - User taste profile vector
    - FAISS k-NN for fast similarity search
    """
    
    def __init__(self):
        """Initialize with ML components (Jetson only)."""
        super().__init__()
        self._embedding_service = None
        self._vector_index = None
        self._ml_available = False
        
        # Try to load ML components
        self._init_ml_components()
    
    def _init_ml_components(self):
        """Initialize ML components if available."""
        try:
            from model_config import detect_hardware
            if detect_hardware() != "jetson":
                logger.info("MLRecommendationEngine: Not on Jetson, using metadata fallback")
                return
            
            # Check memory headroom
            if not self._check_memory():
                logger.warning("MLRecommendationEngine: Low memory, using metadata fallback")
                return
            
            # Lazy load - actual loading happens on first use
            self._ml_available = True
            logger.info("MLRecommendationEngine: ML features enabled")
            
        except Exception as e:
            logger.warning(f"MLRecommendationEngine: ML init failed: {e}")
            self._ml_available = False
    
    def _check_memory(self, min_gb: float = 3.0) -> bool:
        """Check if enough memory is available for ML components."""
        try:
            import psutil
            available_gb = psutil.virtual_memory().available / (1024**3)
            return available_gb >= min_gb
        except ImportError:
            return True  # Assume OK if psutil not available
    
    @property
    def embedding_service(self):
        """Lazy load embedding service."""
        if self._embedding_service is None and self._ml_available:
            try:
                from .embedding_service import get_embedding_service
                self._embedding_service = get_embedding_service()
            except Exception as e:
                logger.warning(f"Failed to load embedding service: {e}")
                self._ml_available = False
        return self._embedding_service
    
    @property
    def vector_index(self):
        """Lazy load vector index."""
        if self._vector_index is None and self._ml_available:
            try:
                from .vector_index import get_vector_index
                self._vector_index = get_vector_index()
            except Exception as e:
                logger.warning(f"Failed to load vector index: {e}")
                self._ml_available = False
        return self._vector_index
    
    async def get_similar(
        self,
        track_id: str,
        user_id: str,
        limit: int = 10
    ) -> List[Dict]:
        """
        Find similar tracks using embedding similarity.
        
        Falls back to metadata search if embeddings not available.
        """
        if not self._ml_available or not self.vector_index:
            return await super().get_similar(track_id, user_id, limit)
        
        try:
            # Get or create embedding for track
            embedding = await self._get_or_create_embedding(track_id, user_id)
            
            if embedding is None:
                return await super().get_similar(track_id, user_id, limit)
            
            # FAISS k-NN search
            similar_ids = self.vector_index.search(embedding, k=limit * 2)
            
            # Get track info for results
            similar_tracks = []
            for sim_id in similar_ids:
                if sim_id != track_id:  # Exclude seed track
                    track_info = await self._get_track_info(sim_id)
                    if track_info:
                        track_info["videoId"] = sim_id
                        similar_tracks.append(track_info)
            
            # Rank by affinity
            ranked = self.affinity.rank_tracks_by_affinity(similar_tracks, user_id)
            
            return ranked[:limit]
            
        except Exception as e:
            logger.warning(f"ML similarity failed: {e}, falling back to metadata")
            return await super().get_similar(track_id, user_id, limit)
    
    async def get_mood_match(
        self,
        user_id: str,
        limit: int = 20
    ) -> List[Dict]:
        """
        Match current listening mood using embedding average.
        
        Calculates mood vector from recent tracks and finds similar.
        """
        if not self._ml_available:
            return await super().get_mood_match(user_id, limit)
        
        try:
            import numpy as np
            
            # Get recent tracks
            recent_ids = await self._get_recent_tracks(user_id, limit=5)
            
            if not recent_ids:
                return await self.get_radio(user_id, limit=limit)
            
            # Get embeddings for recent tracks
            embeddings = []
            for track_id in recent_ids:
                emb = await self._get_or_create_embedding(track_id, user_id)
                if emb is not None:
                    embeddings.append(emb)
            
            if not embeddings:
                return await super().get_mood_match(user_id, limit)
            
            # Average embeddings = mood vector
            mood_vector = np.mean(embeddings, axis=0)
            
            # Find similar tracks
            similar_ids = self.vector_index.search(mood_vector, k=limit * 2)
            
            # Get track info and filter out recent
            mood_tracks = []
            for sim_id in similar_ids:
                if sim_id not in recent_ids:
                    track_info = await self._get_track_info(sim_id)
                    if track_info:
                        track_info["videoId"] = sim_id
                        mood_tracks.append(track_info)
            
            return mood_tracks[:limit]
            
        except Exception as e:
            logger.warning(f"ML mood match failed: {e}, falling back to metadata")
            return await super().get_mood_match(user_id, limit)
    
    async def _get_or_create_embedding(
        self,
        track_id: str,
        user_id: str
    ) -> Optional[Any]:
        """
        Get embedding for track, creating if needed.
        
        Returns numpy array or None.
        """
        if not self.embedding_service:
            return None
        
        try:
            # Check cache first
            embedding = await self.embedding_service.get_cached_embedding(track_id)
            
            if embedding is not None:
                return embedding
            
            # Get track info for metadata embedding
            track_info = await self._get_track_info(track_id)
            
            if not track_info:
                # Try to get from YouTube
                track = await self.youtube.get_track(track_id, user_id)
                if track:
                    track_info = {
                        "title": track.get("title"),
                        "artist": track.get("artist"),
                        "album": track.get("album")
                    }
            
            if not track_info:
                return None
            
            # Create embedding (audio + metadata fusion)
            embedding = await self.embedding_service.create_embedding(
                track_id=track_id,
                title=track_info.get("title", ""),
                artist=track_info.get("artist", ""),
                album=track_info.get("album")
            )
            
            return embedding
            
        except Exception as e:
            logger.warning(f"Failed to get/create embedding for {track_id}: {e}")
            return None


# ============================================================
# Factory function for platform-appropriate engine
# ============================================================

_recommendation_engine: Optional[BaseRecommendationEngine] = None


def get_recommendation_engine() -> BaseRecommendationEngine:
    """
    Get the platform-appropriate recommendation engine.
    
    Returns MLRecommendationEngine on Jetson (with fallback),
    MetadataRecommendationEngine on Pi5.
    """
    global _recommendation_engine
    
    if _recommendation_engine is None:
        try:
            from model_config import detect_hardware
            platform = detect_hardware()
            
            if platform == "jetson":
                _recommendation_engine = MLRecommendationEngine()
                logger.info("Using MLRecommendationEngine (Jetson)")
            else:
                _recommendation_engine = MetadataRecommendationEngine()
                logger.info("Using MetadataRecommendationEngine (Pi5)")
                
        except Exception as e:
            logger.warning(f"Platform detection failed: {e}, using metadata engine")
            _recommendation_engine = MetadataRecommendationEngine()
    
    return _recommendation_engine

