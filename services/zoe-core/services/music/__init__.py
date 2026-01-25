"""
Music Service Module - DEPRECATED
===================================

⚠️  DEPRECATION NOTICE:
This music service has been extracted to the zoe-music module.
Location: modules/zoe-music/
This code will be removed in a future version.
Please use the module via MCP tools instead.

Platform-aware music system with:
- YouTube Music provider (all platforms)
- Auth management with encryption (all platforms)
- Media controller for device routing (all platforms)
- Event tracking for behavioral learning (all platforms)
- Affinity scoring with temporal decay (all platforms)
- Recommendation engine (metadata or ML based on platform)

Jetson-only ML features (lazy loaded):
- Audio analyzer (Essentia)
- Embedding service (CLAP + text)
- Vector index (FAISS-GPU)
"""

import logging
import warnings

logger = logging.getLogger(__name__)

# Issue deprecation warning
warnings.warn(
    "services.music has been moved to the zoe-music module. "
    "This import will be removed in a future version. "
    "Update to use MCP tools via zoe-mcp-server instead.",
    DeprecationWarning,
    stacklevel=2
)

# ============================================================
# Platform Detection
# ============================================================

try:
    from model_config import detect_hardware
    PLATFORM = detect_hardware()
except ImportError:
    PLATFORM = "unknown"

# Check memory for ML features
def _check_memory_headroom(min_gb: float = 3.0) -> bool:
    """Check if enough memory is available for ML components."""
    try:
        import psutil
        available_gb = psutil.virtual_memory().available / (1024**3)
        return available_gb >= min_gb
    except ImportError:
        return True  # Assume OK if psutil not available

ML_ENABLED = PLATFORM == "jetson" and _check_memory_headroom()

logger.info(f"🎵 Music module initialized: platform={PLATFORM}, ml_enabled={ML_ENABLED}")

# ============================================================
# Always Available (All Platforms)
# ============================================================

from .auth_manager import MusicAuthManager, get_auth_manager
from .youtube_music import YouTubeMusicProvider, get_youtube_music
from .media_controller import MediaController, get_media_controller
from .event_tracker import MusicEventTracker, get_event_tracker
from .affinity_engine import AffinityEngine, get_affinity_engine
from .recommendation_engine import (
    BaseRecommendationEngine,
    MetadataRecommendationEngine,
    get_recommendation_engine,
)

# ============================================================
# Lazy-Loaded ML Components (Jetson Only)
# ============================================================

_audio_analyzer = None
_embedding_service = None
_vector_index = None


def get_audio_analyzer():
    """
    Get the audio analyzer (Jetson only).
    
    Returns None on Pi5 or if memory is low.
    """
    global _audio_analyzer
    
    if not ML_ENABLED:
        return None
    
    if _audio_analyzer is None:
        try:
            from .audio_analyzer import AudioAnalyzer
            _audio_analyzer = AudioAnalyzer()
            logger.info("✅ AudioAnalyzer loaded")
        except Exception as e:
            logger.warning(f"AudioAnalyzer not available: {e}")
            return None
    
    return _audio_analyzer


def get_embedding_service():
    """
    Get the embedding service (Jetson only).
    
    Returns None on Pi5 or if memory is low.
    """
    global _embedding_service
    
    if not ML_ENABLED:
        return None
    
    if _embedding_service is None:
        try:
            from .embedding_service import EmbeddingService
            _embedding_service = EmbeddingService()
            logger.info("✅ EmbeddingService loaded")
        except Exception as e:
            logger.warning(f"EmbeddingService not available: {e}")
            return None
    
    return _embedding_service


def get_vector_index():
    """
    Get the vector index (Jetson only).
    
    Returns None on Pi5 or if memory is low.
    """
    global _vector_index
    
    if not ML_ENABLED:
        return None
    
    if _vector_index is None:
        try:
            from .vector_index import VectorIndex
            _vector_index = VectorIndex()
            logger.info("✅ VectorIndex loaded")
        except Exception as e:
            logger.warning(f"VectorIndex not available: {e}")
            return None
    
    return _vector_index


def get_capabilities() -> dict:
    """
    Get music system capabilities for this platform.
    
    Returns:
        Dict with platform info and available features
    """
    try:
        import psutil
        memory_available_gb = round(psutil.virtual_memory().available / (1024**3), 1)
    except:
        memory_available_gb = 0
    
    return {
        "platform": PLATFORM,
        "ml_enabled": ML_ENABLED,
        "recommendation_quality": "ml" if ML_ENABLED else "metadata",
        "memory_available_gb": memory_available_gb,
        "features": {
            "event_tracking": True,
            "affinity_scoring": True,
            "metadata_recommendations": True,
            "audio_analysis": ML_ENABLED,
            "embedding_similarity": ML_ENABLED,
            "mood_detection": ML_ENABLED,
        }
    }


# ============================================================
# Exports
# ============================================================

__all__ = [
    # Platform info
    "PLATFORM",
    "ML_ENABLED",
    "get_capabilities",
    
    # Core services (all platforms)
    "MusicAuthManager",
    "get_auth_manager",
    "YouTubeMusicProvider",
    "get_youtube_music",
    "MediaController",
    "get_media_controller",
    
    # Intelligence (all platforms)
    "MusicEventTracker",
    "get_event_tracker",
    "AffinityEngine",
    "get_affinity_engine",
    "BaseRecommendationEngine",
    "MetadataRecommendationEngine",
    "get_recommendation_engine",
    
    # ML components (Jetson only, lazy loaded)
    "get_audio_analyzer",
    "get_embedding_service",
    "get_vector_index",
]
