"""
AirPlay Output Target
=====================

Implementation of OutputTarget for Apple AirPlay devices.
Uses pyatv library for device discovery and control.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from .base import OutputTarget, OutputType, DeviceInfo, PlaybackState, OutputState

logger = logging.getLogger(__name__)

# Check for pyatv
try:
    import pyatv
    from pyatv.const import Protocol, DeviceState
    PYATV_AVAILABLE = True
except ImportError:
    PYATV_AVAILABLE = False
    logger.warning("pyatv not installed - AirPlay support unavailable")


class AirPlayOutput(OutputTarget):
    """
    AirPlay implementation of OutputTarget.
    
    Discovers and controls AirPlay devices (HomePod, Apple TV, etc.)
    on the local network.
    """
    
    def __init__(self):
        if not PYATV_AVAILABLE:
            raise ImportError("pyatv library is required for AirPlay support")
        
        self._devices: Dict[str, Any] = {}  # device_id -> pyatv device config
        self._connections: Dict[str, Any] = {}  # device_id -> connected device
        self._device_info: Dict[str, DeviceInfo] = {}
        self._initialized = False
    
    @property
    def output_type(self) -> OutputType:
        return OutputType.AIRPLAY
    
    @property
    def is_initialized(self) -> bool:
        return self._initialized
    
    async def initialize(self) -> bool:
        """Start AirPlay device discovery."""
        try:
            await self._discover()
            self._initialized = True
            return True
        except Exception as e:
            logger.error(f"Failed to initialize AirPlay: {e}")
            return False
    
    async def _discover(self):
        """Discover AirPlay devices on the network."""
        try:
            # Scan for 5 seconds
            devices = await pyatv.scan(asyncio.get_event_loop(), timeout=5)
            
            for device in devices:
                device_id = f"airplay:{device.identifier}"
                self._devices[device_id] = device
                
                # Check for AirPlay support
                supports_airplay = any(
                    service.protocol == Protocol.AirPlay 
                    for service in device.services
                )
                
                self._device_info[device_id] = DeviceInfo(
                    id=device_id,
                    name=device.name,
                    output_type=OutputType.AIRPLAY,
                    model=device.device_info.model if device.device_info else None,
                    manufacturer="Apple",
                    ip_address=str(device.address),
                    is_available=supports_airplay,
                    supports_video=device.device_info.output_device_id is None if device.device_info else True
                )
            
            logger.info(f"Discovered {len(self._devices)} AirPlay devices")
            
        except Exception as e:
            logger.error(f"AirPlay discovery failed: {e}")
            raise
    
    async def shutdown(self) -> None:
        """Disconnect from all devices."""
        for device_id, connection in list(self._connections.items()):
            try:
                connection.close()
            except Exception:
                pass
        
        self._connections.clear()
        self._devices.clear()
        self._device_info.clear()
        self._initialized = False
    
    async def _get_connection(self, device_id: str) -> Optional[Any]:
        """Get or create connection to a device."""
        if device_id in self._connections:
            return self._connections[device_id]
        
        device_config = self._devices.get(device_id)
        if not device_config:
            return None
        
        try:
            connection = await pyatv.connect(device_config, asyncio.get_event_loop())
            self._connections[device_id] = connection
            return connection
        except Exception as e:
            logger.error(f"Failed to connect to AirPlay device {device_id}: {e}")
            return None
    
    # ========================================
    # Device Discovery
    # ========================================
    
    async def discover_devices(self) -> List[DeviceInfo]:
        """Discover available AirPlay devices."""
        if not self._initialized:
            await self.initialize()
        return list(self._device_info.values())
    
    async def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        """Get information about a specific device."""
        return self._device_info.get(device_id)
    
    async def refresh_devices(self) -> int:
        """Refresh the device list."""
        await self._discover()
        return len(self._devices)
    
    # ========================================
    # Playback Control
    # ========================================
    
    async def play(
        self,
        device_id: str,
        stream_url: str,
        track_info: Optional[Dict[str, Any]] = None,
        content_type: str = "audio/mp4"
    ) -> bool:
        """Play audio on an AirPlay device."""
        connection = await self._get_connection(device_id)
        if not connection:
            return False
        
        try:
            # AirPlay audio streaming
            # Note: pyatv primarily supports controlling existing playback
            # For streaming audio, we'd need to use a different approach
            # (like setting up an AirPlay server or using stream_file)
            
            await connection.stream.stream_file(stream_url)
            
            logger.info(f"Playing on AirPlay {device_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to play on AirPlay {device_id}: {e}")
            return False
    
    async def pause(self, device_id: str) -> bool:
        """Pause playback on an AirPlay device."""
        connection = await self._get_connection(device_id)
        if not connection or not connection.remote_control:
            return False
        
        try:
            await connection.remote_control.pause()
            return True
        except Exception as e:
            logger.error(f"Failed to pause AirPlay {device_id}: {e}")
            return False
    
    async def resume(self, device_id: str) -> bool:
        """Resume playback on an AirPlay device."""
        connection = await self._get_connection(device_id)
        if not connection or not connection.remote_control:
            return False
        
        try:
            await connection.remote_control.play()
            return True
        except Exception as e:
            logger.error(f"Failed to resume AirPlay {device_id}: {e}")
            return False
    
    async def stop(self, device_id: str) -> bool:
        """Stop playback on an AirPlay device."""
        connection = await self._get_connection(device_id)
        if not connection or not connection.remote_control:
            return False
        
        try:
            await connection.remote_control.stop()
            return True
        except Exception as e:
            logger.error(f"Failed to stop AirPlay {device_id}: {e}")
            return False
    
    async def seek(self, device_id: str, position_ms: int) -> bool:
        """Seek to a position on an AirPlay device."""
        connection = await self._get_connection(device_id)
        if not connection or not connection.remote_control:
            return False
        
        try:
            await connection.remote_control.set_position(position_ms // 1000)
            return True
        except Exception as e:
            logger.error(f"Failed to seek on AirPlay {device_id}: {e}")
            return False
    
    # ========================================
    # Volume Control
    # ========================================
    
    async def set_volume(self, device_id: str, volume: int) -> bool:
        """Set volume on an AirPlay device."""
        connection = await self._get_connection(device_id)
        if not connection or not connection.audio:
            return False
        
        try:
            await connection.audio.set_volume(volume / 100)
            return True
        except Exception as e:
            logger.error(f"Failed to set volume on AirPlay {device_id}: {e}")
            return False
    
    async def set_mute(self, device_id: str, muted: bool) -> bool:
        """Mute or unmute an AirPlay device."""
        # pyatv doesn't have direct mute control
        # Implement as volume 0 / restore
        if muted:
            return await self.set_volume(device_id, 0)
        return await self.set_volume(device_id, 50)  # Restore to 50%
    
    # ========================================
    # State
    # ========================================
    
    async def get_state(self, device_id: str) -> Optional[PlaybackState]:
        """Get current playback state of an AirPlay device."""
        connection = await self._get_connection(device_id)
        if not connection:
            return None
        
        try:
            playing = await connection.metadata.playing()
            
            # Map pyatv state to OutputState
            if playing.device_state == DeviceState.Playing:
                state = OutputState.PLAYING
            elif playing.device_state == DeviceState.Paused:
                state = OutputState.PAUSED
            elif playing.device_state == DeviceState.Loading:
                state = OutputState.BUFFERING
            else:
                state = OutputState.IDLE
            
            return PlaybackState(
                state=state,
                track_id=None,
                track_title=playing.title,
                track_artist=playing.artist,
                position_ms=int(playing.position * 1000) if playing.position else 0,
                duration_ms=int(playing.total_time * 1000) if playing.total_time else 0,
                volume=100  # Would need audio.volume if available
            )
            
        except Exception as e:
            logger.error(f"Failed to get AirPlay state {device_id}: {e}")
            return None


# Singleton instance
_airplay_output: Optional[AirPlayOutput] = None


def get_airplay_output() -> Optional[AirPlayOutput]:
    """Get the singleton AirPlay output instance."""
    global _airplay_output
    
    if not PYATV_AVAILABLE:
        return None
    
    if _airplay_output is None:
        try:
            _airplay_output = AirPlayOutput()
        except ImportError:
            return None
    
    return _airplay_output

