"""
Chromecast Output Target
========================

Implementation of OutputTarget for Google Chromecast devices.
Uses pychromecast library for device discovery and control.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from .base import OutputTarget, OutputType, DeviceInfo, PlaybackState, OutputState

logger = logging.getLogger(__name__)

# Check for pychromecast
try:
    import pychromecast
    from pychromecast.controllers.media import MediaController
    PYCHROMECAST_AVAILABLE = True
except ImportError:
    PYCHROMECAST_AVAILABLE = False
    logger.warning("pychromecast not installed - Chromecast support unavailable")


class ChromecastOutput(OutputTarget):
    """
    Chromecast implementation of OutputTarget.
    
    Discovers and controls Chromecast devices on the local network.
    """
    
    def __init__(self):
        if not PYCHROMECAST_AVAILABLE:
            raise ImportError("pychromecast library is required for Chromecast support")
        
        self._devices: Dict[str, Any] = {}  # device_id -> Chromecast object
        self._device_info: Dict[str, DeviceInfo] = {}
        self._initialized = False
        self._browser = None
        self._discovery_lock = asyncio.Lock()
    
    @property
    def output_type(self) -> OutputType:
        return OutputType.CHROMECAST
    
    @property
    def is_initialized(self) -> bool:
        return self._initialized
    
    async def initialize(self) -> bool:
        """Start Chromecast discovery."""
        try:
            await self._start_discovery()
            self._initialized = True
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Chromecast: {e}")
            return False
    
    async def _start_discovery(self):
        """Start discovering Chromecast devices."""
        async with self._discovery_lock:
            try:
                # Run discovery in thread pool
                loop = asyncio.get_event_loop()
                chromecasts, browser = await loop.run_in_executor(
                    None,
                    lambda: pychromecast.get_chromecasts(timeout=10)
                )
                
                self._browser = browser
                
                for cc in chromecasts:
                    device_id = f"chromecast:{cc.uuid}"
                    self._devices[device_id] = cc
                    
                    self._device_info[device_id] = DeviceInfo(
                        id=device_id,
                        name=cc.cast_info.friendly_name,
                        output_type=OutputType.CHROMECAST,
                        model=cc.cast_info.model_name,
                        manufacturer="Google",
                        ip_address=str(cc.cast_info.host),
                        is_available=True,
                        supports_video=True
                    )
                
                logger.info(f"Discovered {len(self._devices)} Chromecast devices")
                
            except Exception as e:
                logger.error(f"Chromecast discovery failed: {e}")
                raise
    
    async def shutdown(self) -> None:
        """Stop discovery and disconnect devices."""
        if self._browser:
            try:
                self._browser.stop_discovery()
            except Exception as e:
                logger.warning(f"Error stopping Chromecast discovery: {e}")
        
        for cc in self._devices.values():
            try:
                cc.disconnect()
            except Exception:
                pass
        
        self._devices.clear()
        self._device_info.clear()
        self._initialized = False
    
    # ========================================
    # Device Discovery
    # ========================================
    
    async def discover_devices(self) -> List[DeviceInfo]:
        """Discover available Chromecast devices."""
        if not self._initialized:
            await self.initialize()
        return list(self._device_info.values())
    
    async def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        """Get information about a specific device."""
        return self._device_info.get(device_id)
    
    def _get_chromecast(self, device_id: str) -> Optional[Any]:
        """Get Chromecast object by device ID."""
        return self._devices.get(device_id)
    
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
        """Play audio on a Chromecast device."""
        cc = self._get_chromecast(device_id)
        if not cc:
            logger.warning(f"Chromecast device not found: {device_id}")
            return False
        
        try:
            # Wait for device connection
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, cc.wait)
            
            # Get media controller
            mc = cc.media_controller
            
            # Build metadata
            title = track_info.get("title", "Unknown") if track_info else "Unknown"
            artist = track_info.get("artist", "") if track_info else ""
            thumbnail = track_info.get("thumbnailUrl") or track_info.get("thumbnail_url") if track_info else None
            
            # Play media
            await loop.run_in_executor(
                None,
                lambda: mc.play_media(
                    stream_url,
                    content_type,
                    title=title,
                    thumb=thumbnail,
                    metadata={
                        "metadataType": 3,  # MusicTrackMediaMetadata
                        "artist": artist,
                        "title": title,
                        "images": [{"url": thumbnail}] if thumbnail else []
                    }
                )
            )
            
            await loop.run_in_executor(None, mc.block_until_active)
            
            logger.info(f"Playing on Chromecast {device_id}: {title}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to play on Chromecast {device_id}: {e}")
            return False
    
    async def pause(self, device_id: str) -> bool:
        """Pause playback on a Chromecast device."""
        cc = self._get_chromecast(device_id)
        if not cc:
            return False
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, cc.media_controller.pause)
            return True
        except Exception as e:
            logger.error(f"Failed to pause Chromecast {device_id}: {e}")
            return False
    
    async def resume(self, device_id: str) -> bool:
        """Resume playback on a Chromecast device."""
        cc = self._get_chromecast(device_id)
        if not cc:
            return False
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, cc.media_controller.play)
            return True
        except Exception as e:
            logger.error(f"Failed to resume Chromecast {device_id}: {e}")
            return False
    
    async def stop(self, device_id: str) -> bool:
        """Stop playback on a Chromecast device."""
        cc = self._get_chromecast(device_id)
        if not cc:
            return False
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, cc.media_controller.stop)
            return True
        except Exception as e:
            logger.error(f"Failed to stop Chromecast {device_id}: {e}")
            return False
    
    async def seek(self, device_id: str, position_ms: int) -> bool:
        """Seek to a position on a Chromecast device."""
        cc = self._get_chromecast(device_id)
        if not cc:
            return False
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, 
                lambda: cc.media_controller.seek(position_ms / 1000)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to seek on Chromecast {device_id}: {e}")
            return False
    
    # ========================================
    # Volume Control
    # ========================================
    
    async def set_volume(self, device_id: str, volume: int) -> bool:
        """Set volume on a Chromecast device."""
        cc = self._get_chromecast(device_id)
        if not cc:
            return False
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: cc.set_volume(volume / 100)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to set volume on Chromecast {device_id}: {e}")
            return False
    
    async def set_mute(self, device_id: str, muted: bool) -> bool:
        """Mute or unmute a Chromecast device."""
        cc = self._get_chromecast(device_id)
        if not cc:
            return False
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: cc.set_volume_muted(muted)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to mute Chromecast {device_id}: {e}")
            return False
    
    # ========================================
    # State
    # ========================================
    
    async def get_state(self, device_id: str) -> Optional[PlaybackState]:
        """Get current playback state of a Chromecast device."""
        cc = self._get_chromecast(device_id)
        if not cc:
            return None
        
        try:
            mc = cc.media_controller
            status = mc.status
            
            if not status:
                return PlaybackState(state=OutputState.IDLE)
            
            # Map Chromecast state to OutputState
            if status.player_is_playing:
                state = OutputState.PLAYING
            elif status.player_is_paused:
                state = OutputState.PAUSED
            elif status.player_state == "BUFFERING":
                state = OutputState.BUFFERING
            else:
                state = OutputState.IDLE
            
            return PlaybackState(
                state=state,
                track_id=None,  # Chromecast doesn't track our IDs
                track_title=status.title,
                track_artist=status.artist,
                position_ms=int((status.current_time or 0) * 1000),
                duration_ms=int((status.duration or 0) * 1000),
                volume=int(cc.status.volume_level * 100) if cc.status else 100,
                is_muted=cc.status.volume_muted if cc.status else False
            )
            
        except Exception as e:
            logger.error(f"Failed to get Chromecast state {device_id}: {e}")
            return None


# Singleton instance
_chromecast_output: Optional[ChromecastOutput] = None


def get_chromecast_output() -> Optional[ChromecastOutput]:
    """Get the singleton Chromecast output instance."""
    global _chromecast_output
    
    if not PYCHROMECAST_AVAILABLE:
        return None
    
    if _chromecast_output is None:
        try:
            _chromecast_output = ChromecastOutput()
        except ImportError:
            return None
    
    return _chromecast_output

