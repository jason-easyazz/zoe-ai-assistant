"""
Output Manager
==============

Central manager for all audio output targets.
Handles device discovery, routing, and playback across multiple output types.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any

from .base import OutputTarget, OutputType, DeviceInfo, PlaybackState, OutputState
from .chromecast import get_chromecast_output
from .airplay import get_airplay_output
from .homeassistant import get_homeassistant_output

logger = logging.getLogger(__name__)


class OutputManager:
    """
    Central manager for all audio output targets.
    
    Provides a unified interface for:
    - Device discovery across all output types
    - Routing playback to specific devices
    - Managing device state and volume
    - Handling device switching mid-playback
    """
    
    def __init__(self):
        self._outputs: Dict[OutputType, OutputTarget] = {}
        self._current_device: Optional[str] = None
        self._current_track_info: Optional[Dict] = None
        self._initialized = False
    
    async def initialize(self) -> bool:
        """Initialize all available output targets."""
        if self._initialized:
            return True
        
        # Register Chromecast
        chromecast = get_chromecast_output()
        if chromecast:
            try:
                if await chromecast.initialize():
                    self._outputs[OutputType.CHROMECAST] = chromecast
                    logger.info("Chromecast output initialized")
            except Exception as e:
                logger.warning(f"Chromecast init failed: {e}")
        
        # Register AirPlay
        airplay = get_airplay_output()
        if airplay:
            try:
                if await airplay.initialize():
                    self._outputs[OutputType.AIRPLAY] = airplay
                    logger.info("AirPlay output initialized")
            except Exception as e:
                logger.warning(f"AirPlay init failed: {e}")
        
        # Register Home Assistant
        ha = get_homeassistant_output()
        if ha:
            try:
                if await ha.initialize():
                    self._outputs[OutputType.HOMEASSISTANT] = ha
                    logger.info("Home Assistant output initialized")
            except Exception as e:
                logger.warning(f"Home Assistant init failed: {e}")
        
        self._initialized = True
        logger.info(f"Output manager initialized with {len(self._outputs)} output types")
        return True
    
    async def shutdown(self) -> None:
        """Shutdown all output targets."""
        for output in self._outputs.values():
            try:
                await output.shutdown()
            except Exception as e:
                logger.warning(f"Error shutting down output: {e}")
        
        self._outputs.clear()
        self._initialized = False
    
    # ========================================
    # Device Discovery
    # ========================================
    
    async def get_all_devices(self) -> List[DeviceInfo]:
        """Get all available devices from all output types."""
        if not self._initialized:
            await self.initialize()
        
        devices = []
        for output in self._outputs.values():
            try:
                output_devices = await output.discover_devices()
                devices.extend(output_devices)
            except Exception as e:
                logger.warning(f"Failed to get devices from {output.output_type}: {e}")
        
        return devices
    
    async def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        """Get information about a specific device."""
        output = self._get_output_for_device(device_id)
        if output:
            return await output.get_device(device_id)
        return None
    
    async def refresh_all_devices(self) -> Dict[OutputType, int]:
        """Refresh device lists for all output types."""
        results = {}
        
        for output_type, output in self._outputs.items():
            try:
                count = await output.refresh_devices()
                results[output_type] = count
            except Exception as e:
                logger.warning(f"Failed to refresh {output_type}: {e}")
                results[output_type] = 0
        
        return results
    
    def _get_output_for_device(self, device_id: str) -> Optional[OutputTarget]:
        """Get the appropriate output target for a device ID."""
        if device_id.startswith("chromecast:"):
            return self._outputs.get(OutputType.CHROMECAST)
        elif device_id.startswith("airplay:"):
            return self._outputs.get(OutputType.AIRPLAY)
        elif device_id.startswith("ha:"):
            return self._outputs.get(OutputType.HOMEASSISTANT)
        return None
    
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
        """
        Play audio on a device.
        
        Args:
            device_id: Target device ID
            stream_url: URL of the audio stream
            track_info: Optional track metadata
            content_type: MIME type of the stream
            
        Returns:
            True if playback started successfully
        """
        output = self._get_output_for_device(device_id)
        if not output:
            logger.warning(f"No output handler for device: {device_id}")
            return False
        
        success = await output.play(device_id, stream_url, track_info, content_type)
        
        if success:
            self._current_device = device_id
            self._current_track_info = track_info
        
        return success
    
    async def pause(self, device_id: Optional[str] = None) -> bool:
        """Pause playback on a device (or current device if not specified)."""
        target = device_id or self._current_device
        if not target:
            return False
        
        output = self._get_output_for_device(target)
        if not output:
            return False
        
        return await output.pause(target)
    
    async def resume(self, device_id: Optional[str] = None) -> bool:
        """Resume playback on a device."""
        target = device_id or self._current_device
        if not target:
            return False
        
        output = self._get_output_for_device(target)
        if not output:
            return False
        
        return await output.resume(target)
    
    async def stop(self, device_id: Optional[str] = None) -> bool:
        """Stop playback on a device."""
        target = device_id or self._current_device
        if not target:
            return False
        
        output = self._get_output_for_device(target)
        if not output:
            return False
        
        success = await output.stop(target)
        if success and target == self._current_device:
            self._current_device = None
            self._current_track_info = None
        
        return success
    
    async def seek(self, position_ms: int, device_id: Optional[str] = None) -> bool:
        """Seek to a position on a device."""
        target = device_id or self._current_device
        if not target:
            return False
        
        output = self._get_output_for_device(target)
        if not output:
            return False
        
        return await output.seek(target, position_ms)
    
    # ========================================
    # Volume Control
    # ========================================
    
    async def set_volume(self, volume: int, device_id: Optional[str] = None) -> bool:
        """Set volume on a device."""
        target = device_id or self._current_device
        if not target:
            return False
        
        output = self._get_output_for_device(target)
        if not output:
            return False
        
        return await output.set_volume(target, volume)
    
    async def set_mute(self, muted: bool, device_id: Optional[str] = None) -> bool:
        """Mute or unmute a device."""
        target = device_id or self._current_device
        if not target:
            return False
        
        output = self._get_output_for_device(target)
        if not output:
            return False
        
        return await output.set_mute(target, muted)
    
    # ========================================
    # State
    # ========================================
    
    async def get_state(self, device_id: Optional[str] = None) -> Optional[PlaybackState]:
        """Get playback state of a device."""
        target = device_id or self._current_device
        if not target:
            return PlaybackState(state=OutputState.IDLE)
        
        output = self._get_output_for_device(target)
        if not output:
            return None
        
        return await output.get_state(target)
    
    @property
    def current_device(self) -> Optional[str]:
        """Get the currently active device ID."""
        return self._current_device
    
    @property
    def current_track(self) -> Optional[Dict]:
        """Get the currently playing track info."""
        return self._current_track_info
    
    # ========================================
    # Device Switching
    # ========================================
    
    async def switch_device(
        self,
        new_device_id: str,
        stream_url: Optional[str] = None
    ) -> bool:
        """
        Switch playback to a different device.
        
        Stops playback on current device and starts on new device.
        If stream_url not provided, will attempt to continue current track.
        
        Args:
            new_device_id: Device to switch to
            stream_url: Optional stream URL (uses current if not provided)
            
        Returns:
            True if switch was successful
        """
        # Stop current playback
        if self._current_device:
            await self.stop(self._current_device)
        
        # Get stream URL from current track if not provided
        if not stream_url and self._current_track_info:
            # Would need to re-fetch stream URL from provider
            logger.warning("Device switch without stream URL - playback will restart")
            return False
        
        if not stream_url:
            logger.warning("No stream URL for device switch")
            return False
        
        # Start playback on new device
        return await self.play(
            new_device_id,
            stream_url,
            self._current_track_info
        )


# Singleton instance
_output_manager: Optional[OutputManager] = None


def get_output_manager() -> OutputManager:
    """Get the singleton output manager instance."""
    global _output_manager
    if _output_manager is None:
        _output_manager = OutputManager()
    return _output_manager

