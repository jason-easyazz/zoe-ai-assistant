"""
Music Providers Package
=======================

Unified interface for music streaming services.
"""

from .base import MusicProvider, Track, Album, Artist, Playlist
from .registry import ProviderRegistry, get_provider_registry

__all__ = [
    "MusicProvider",
    "Track",
    "Album", 
    "Artist",
    "Playlist",
    "ProviderRegistry",
    "get_provider_registry"
]

