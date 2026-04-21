"""
Audio Analyzer
==============

Extracts audio features from music tracks using Essentia.
Jetson-only - requires GPU for efficient processing.

Features extracted:
- Rhythm: BPM, danceability, beat strength
- Tonal: Key, scale, key strength
- Energy: Loudness, dynamic range
- Mood: Valence (sad-happy), arousal (calm-energetic)
- Timbral: MFCCs, spectral features

Only analyzes a 60-second segment (0:30-1:30) for efficiency.
"""

import os
import json
import logging
import sqlite3
import asyncio
from typing import Dict, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")
AUDIO_CACHE_DIR = os.getenv("AUDIO_CACHE_DIR", "/app/data/audio_cache")

# Check platform
try:
    from model_config import detect_hardware
    if detect_hardware() != "jetson":
        raise ImportError("AudioAnalyzer requires Jetson GPU")
except ImportError as e:
    logger.warning(f"AudioAnalyzer: {e}")


class AudioAnalyzer:
    """
    Extract audio features using Essentia.
    
    Jetson-only component for analyzing music audio and extracting
    features like BPM, key, energy, mood, etc.
    """
    
    def __init__(self):
        """Initialize the audio analyzer with Essentia."""
        self._essentia_available = False
        self._init_essentia()
        self._init_db()
        
        # Ensure cache directory exists
        os.makedirs(AUDIO_CACHE_DIR, exist_ok=True)
    
    def _init_essentia(self):
        """Initialize Essentia library."""
        try:
            import essentia
            import essentia.standard as es
            
            self.es = es
            self._essentia_available = True
            
            # Pre-load commonly used extractors for faster analysis
            self.rhythm_extractor = es.RhythmExtractor2013()
            self.key_extractor = es.KeyExtractor()
            self.loudness_extractor = es.Loudness()
            self.mfcc_extractor = es.MFCC()
            self.spectral_centroid = es.SpectralCentroidTime()
            
            logger.info("✅ Essentia initialized successfully")
            
        except ImportError:
            logger.warning("Essentia not available - audio analysis disabled")
            self._essentia_available = False
    
    def _init_db(self):
        """Ensure audio features table exists."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS music_audio_features (
                    track_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL DEFAULT 'youtube_music',
                    bpm REAL,
                    danceability REAL,
                    beat_strength REAL,
                    key TEXT,
                    scale TEXT,
                    key_strength REAL,
                    energy REAL,
                    loudness REAL,
                    dynamic_range REAL,
                    valence REAL,
                    arousal REAL,
                    mfccs TEXT,
                    spectral_centroid REAL,
                    spectral_rolloff REAL,
                    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    analysis_version TEXT DEFAULT '1.0'
                )
            """)
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to init audio features table: {e}")
    
    @property
    def is_available(self) -> bool:
        """Check if audio analysis is available."""
        return self._essentia_available
    
    async def analyze(self, track_id: str, audio_path: str = None) -> Optional[Dict[str, Any]]:
        """
        Analyze a track and extract audio features.
        
        Args:
            track_id: Track identifier
            audio_path: Path to audio file (will fetch if not provided)
            
        Returns:
            Dict of audio features or None if analysis fails
        """
        if not self._essentia_available:
            logger.warning("Essentia not available")
            return None
        
        # Check cache first
        cached = await self.get_cached_features(track_id)
        if cached:
            return cached
        
        # Get audio file if not provided
        if not audio_path:
            audio_path = await self._fetch_audio_segment(track_id)
            if not audio_path:
                return None
        
        # Run analysis in thread pool to avoid blocking
        try:
            loop = asyncio.get_event_loop()
            features = await loop.run_in_executor(
                None, self._analyze_sync, audio_path
            )
            
            if features:
                # Cache the results
                await self._save_features(track_id, features)
            
            return features
            
        except Exception as e:
            logger.error(f"Audio analysis failed for {track_id}: {e}")
            return None
    
    def _analyze_sync(self, audio_path: str) -> Optional[Dict[str, Any]]:
        """
        Synchronous audio analysis (runs in thread pool).
        
        Args:
            audio_path: Path to audio file
            
        Returns:
            Dict of extracted features
        """
        if not self._essentia_available:
            return None
        
        try:
            # Load audio
            loader = self.es.MonoLoader(filename=audio_path)
            audio = loader()
            
            if len(audio) == 0:
                logger.warning(f"Empty audio file: {audio_path}")
                return None
            
            # Extract rhythm features
            bpm, beats, beats_confidence, _, _ = self.rhythm_extractor(audio)
            
            # Calculate danceability (simplified estimation)
            beat_strength = float(beats_confidence) if beats_confidence else 0.0
            danceability = min(1.0, (bpm / 130) * beat_strength) if bpm > 0 else 0.0
            
            # Extract key/scale
            key, scale, key_strength = self.key_extractor(audio)
            
            # Extract loudness
            loudness = self.loudness_extractor(audio)
            
            # Estimate dynamic range
            windowed_loudness = [
                self.loudness_extractor(audio[i:i+22050])
                for i in range(0, len(audio)-22050, 22050)
            ]
            if windowed_loudness:
                dynamic_range = max(windowed_loudness) - min(windowed_loudness)
            else:
                dynamic_range = 0.0
            
            # Extract MFCCs
            spectrum = self.es.Spectrum()(audio[:22050])  # First second
            mfcc_bands, mfccs = self.mfcc_extractor(spectrum)
            
            # Spectral features
            spectral_centroid_value = self.spectral_centroid(audio)
            
            # Estimate mood (simplified - would use trained model in production)
            # Higher BPM + major key = higher valence
            # Higher energy + higher BPM = higher arousal
            energy = min(1.0, loudness / 10.0 + 0.5) if loudness > -50 else 0.0
            valence = self._estimate_valence(scale, bpm, energy)
            arousal = self._estimate_arousal(bpm, energy, beat_strength)
            
            return {
                "bpm": float(bpm),
                "danceability": float(danceability),
                "beat_strength": float(beat_strength),
                "key": key,
                "scale": scale,
                "key_strength": float(key_strength),
                "energy": float(energy),
                "loudness": float(loudness),
                "dynamic_range": float(dynamic_range),
                "valence": float(valence),
                "arousal": float(arousal),
                "mfccs": mfccs.tolist()[:13],  # First 13 MFCCs
                "spectral_centroid": float(spectral_centroid_value),
            }
            
        except Exception as e:
            logger.error(f"Audio analysis error: {e}")
            return None
    
    def _estimate_valence(self, scale: str, bpm: float, energy: float) -> float:
        """
        Estimate valence (sad-happy) from audio features.
        
        Simple heuristic - major keys and faster BPM tend to sound happier.
        """
        valence = 0.5  # Neutral baseline
        
        # Major keys tend to sound happier
        if scale == "major":
            valence += 0.2
        elif scale == "minor":
            valence -= 0.15
        
        # Faster BPM tends to sound more positive
        if bpm > 120:
            valence += 0.1
        elif bpm < 80:
            valence -= 0.1
        
        # Higher energy can sound more positive
        valence += (energy - 0.5) * 0.2
        
        return max(0.0, min(1.0, valence))
    
    def _estimate_arousal(self, bpm: float, energy: float, beat_strength: float) -> float:
        """
        Estimate arousal (calm-energetic) from audio features.
        
        Higher BPM, energy, and beat strength = more arousing.
        """
        arousal = 0.0
        
        # BPM contribution (normalized to 60-180 range)
        bpm_norm = (bpm - 60) / 120 if bpm > 60 else 0
        arousal += bpm_norm * 0.4
        
        # Energy contribution
        arousal += energy * 0.4
        
        # Beat strength contribution
        arousal += beat_strength * 0.2
        
        return max(0.0, min(1.0, arousal))
    
    async def _fetch_audio_segment(self, track_id: str) -> Optional[str]:
        """
        Fetch audio segment for analysis.
        
        Downloads 60-second segment (0:30-1:30) of the track.
        """
        cache_path = os.path.join(AUDIO_CACHE_DIR, f"{track_id}.opus")
        
        if os.path.exists(cache_path):
            return cache_path
        
        try:
            import yt_dlp
            
            url = f"https://music.youtube.com/watch?v={track_id}"
            
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': cache_path.replace('.opus', '.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
                'extract_audio': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'opus',
                }],
                # Download only 30-90 second segment
                'download_ranges': lambda info_dict, ydl: [{"start_time": 30, "end_time": 90}],
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            
            if os.path.exists(cache_path):
                logger.debug(f"Downloaded audio segment for {track_id}")
                return cache_path
            
            # Check for alternative extension
            for ext in ['.opus', '.m4a', '.webm']:
                alt_path = cache_path.replace('.opus', ext)
                if os.path.exists(alt_path):
                    return alt_path
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to fetch audio for {track_id}: {e}")
            return None
    
    async def get_cached_features(self, track_id: str) -> Optional[Dict[str, Any]]:
        """Get cached audio features from database."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM music_audio_features WHERE track_id = ?
            """, (track_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                features = dict(row)
                # Parse MFCCs from JSON
                if features.get("mfccs"):
                    features["mfccs"] = json.loads(features["mfccs"])
                return features
            
            return None
            
        except Exception as e:
            logger.warning(f"Failed to get cached features: {e}")
            return None
    
    async def _save_features(self, track_id: str, features: Dict[str, Any]):
        """Save audio features to database."""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO music_audio_features
                (track_id, provider, bpm, danceability, beat_strength,
                 key, scale, key_strength, energy, loudness, dynamic_range,
                 valence, arousal, mfccs, spectral_centroid, analyzed_at)
                VALUES (?, 'youtube_music', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                track_id,
                features.get("bpm"),
                features.get("danceability"),
                features.get("beat_strength"),
                features.get("key"),
                features.get("scale"),
                features.get("key_strength"),
                features.get("energy"),
                features.get("loudness"),
                features.get("dynamic_range"),
                features.get("valence"),
                features.get("arousal"),
                json.dumps(features.get("mfccs", [])),
                features.get("spectral_centroid"),
            ))
            
            conn.commit()
            conn.close()
            
            logger.debug(f"Saved audio features for {track_id}")
            
        except Exception as e:
            logger.error(f"Failed to save features: {e}")


# Singleton
_audio_analyzer: Optional[AudioAnalyzer] = None


def get_audio_analyzer() -> AudioAnalyzer:
    """Get the singleton audio analyzer instance."""
    global _audio_analyzer
    if _audio_analyzer is None:
        _audio_analyzer = AudioAnalyzer()
    return _audio_analyzer

