"""
Output Target Base Class
========================

Abstract base class for all audio output destinations.
Defines the unified interface for browser, Chromecast, AirPlay,
and Home Assistant playback.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)


class OutputType(str, Enum):
    """Types of output targets."""
    BROWSER = "browser"
    CHROMECAST = "chromecast"
    AIRPLAY = "airplay"
    HOMEASSISTANT = "homeassistant"


class OutputState(str, Enum):
    """Playback state of an output."""
    IDLE = "idle"
    BUFFERING = "buffering"
    PLAYING = "playing"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class DeviceInfo:
    """Information about an output device."""
    id: str
    name: str
    output_type: OutputType
    model: Optional[str] = None
    manufacturer: Optional[str] = None
    ip_address: Optional[str] = None
    is_available: bool = True
    supports_video: bool = False
    volume_level: int = 100
    is_muted: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.output_type.value,
            "model": self.model,
            "manufacturer": self.manufacturer,
            "ip_address": self.ip_address,
            "is_available": self.is_available,
            "supports_video": self.supports_video,
            "volume_level": self.volume_level,
            "is_muted": self.is_muted
        }


@dataclass
class PlaybackState:
    """Current playback state of an output."""
    state: OutputState
    track_id: Optional[str] = None
    track_title: Optional[str] = None
    track_artist: Optional[str] = None
    position_ms: int = 0
    duration_ms: int = 0
    volume: int = 100
    is_muted: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "is_playing": self.state == OutputState.PLAYING,
            "track_id": self.track_id,
            "track_title": self.track_title,
            "track_artist": self.track_artist,
            "position_ms": self.position_ms,
            "duration_ms": self.duration_ms,
            "volume": self.volume,
            "is_muted": self.is_muted
        }


class OutputTarget(ABC):
    """
    Abstract base class for audio output targets.
    
    All output implementations (Chromecast, AirPlay, etc.) must
    implement this interface for consistent behavior.
    """
    
    @property
    @abstractmethod
    def output_type(self) -> OutputType:
        """Return the output type identifier."""
        pass
    
    @property
    @abstractmethod
    def is_initialized(self) -> bool:
        """Whether the output target is initialized and ready."""
        pass
    
    # ========================================
    # Device Discovery
    # ========================================
    
    @abstractmethod
    async def discover_devices(self) -> List[DeviceInfo]:
        """
        Discover available devices of this type.
        
        Returns:
            List of discovered DeviceInfo objects
        """
        pass
    
    @abstractmethod
    async def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        """
        Get information about a specific device.
        
        Args:
            device_id: Device identifier
            
        Returns:
            DeviceInfo or None if not found
        """
        pass
    
    async def refresh_devices(self) -> int:
        """
        Refresh the device list.
        
        Returns:
            Number of devices found
        """
        devices = await self.discover_devices()
        return len(devices)
    
    # ========================================
    # Playback Control
    # ========================================
    
    @abstractmethod
    async def play(
        self,
        device_id: str,
        stream_url: str,
        track_info: Optional[Dict[str, Any]] = None,
        content_type: str = "audio/mp4"
    ) -> bool:
        """
        Play audio on a device.
        
        Args:
            device_id: Target device ID
            stream_url: URL of the audio stream
            track_info: Optional track metadata (title, artist, album, thumbnail)
            content_type: MIME type of the stream
            
        Returns:
            True if playback started successfully
        """
        pass
    
    @abstractmethod
    async def pause(self, device_id: str) -> bool:
        """
        Pause playback on a device.
        
        Args:
            device_id: Target device ID
            
        Returns:
            True if successful
        """
        pass
    
    @abstractmethod
    async def resume(self, device_id: str) -> bool:
        """
        Resume playback on a device.
        
        Args:
            device_id: Target device ID
            
        Returns:
            True if successful
        """
        pass
    
    @abstractmethod
    async def stop(self, device_id: str) -> bool:
        """
        Stop playback on a device.
        
        Args:
            device_id: Target device ID
            
        Returns:
            True if successful
        """
        pass
    
    @abstractmethod
    async def seek(self, device_id: str, position_ms: int) -> bool:
        """
        Seek to a position in the current track.
        
        Args:
            device_id: Target device ID
            position_ms: Position in milliseconds
            
        Returns:
            True if successful
        """
        pass
    
    # ========================================
    # Volume Control
    # ========================================
    
    @abstractmethod
    async def set_volume(self, device_id: str, volume: int) -> bool:
        """
        Set volume level on a device.
        
        Args:
            device_id: Target device ID
            volume: Volume level (0-100)
            
        Returns:
            True if successful
        """
        pass
    
    @abstractmethod
    async def set_mute(self, device_id: str, muted: bool) -> bool:
        """
        Mute or unmute a device.
        
        Args:
            device_id: Target device ID
            muted: True to mute, False to unmute
            
        Returns:
            True if successful
        """
        pass
    
    # ========================================
    # State
    # ========================================
    
    @abstractmethod
    async def get_state(self, device_id: str) -> Optional[PlaybackState]:
        """
        Get current playback state of a device.
        
        Args:
            device_id: Target device ID
            
        Returns:
            PlaybackState or None if unavailable
        """
        pass
    
    # ========================================
    # Lifecycle
    # ========================================
    
    async def initialize(self) -> bool:
        """
        Initialize the output target.
        
        Override this for setup that needs to happen before use.
        
        Returns:
            True if initialization successful
        """
        return True
    
    async def shutdown(self) -> None:
        """
        Clean up resources.
        
        Override this for cleanup on shutdown.
        """
        pass

