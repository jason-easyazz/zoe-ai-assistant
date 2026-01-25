"""
Home Assistant Output Target
============================

Implementation of OutputTarget for Home Assistant media_player entities.
Uses the Home Assistant REST API for control.
"""

import os
import logging
from typing import Dict, List, Optional, Any

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from .base import OutputTarget, OutputType, DeviceInfo, PlaybackState, OutputState

logger = logging.getLogger(__name__)

HA_URL = os.getenv("HOMEASSISTANT_URL")
HA_TOKEN = os.getenv("HOMEASSISTANT_TOKEN")


class HomeAssistantOutput(OutputTarget):
    """
    Home Assistant implementation of OutputTarget.
    
    Controls media_player entities via the HA REST API.
    """
    
    def __init__(self):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx library is required for Home Assistant support")
        
        if not HA_URL or not HA_TOKEN:
            logger.warning("Home Assistant credentials not configured")
        
        self._device_info: Dict[str, DeviceInfo] = {}
        self._initialized = False
    
    @property
    def output_type(self) -> OutputType:
        return OutputType.HOMEASSISTANT
    
    @property
    def is_initialized(self) -> bool:
        return self._initialized
    
    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json"
        }
    
    async def _api_request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[Dict] = None
    ) -> Optional[Any]:
        """Make a request to the Home Assistant API."""
        if not HA_URL or not HA_TOKEN:
            logger.error("Home Assistant not configured")
            return None
        
        try:
            async with httpx.AsyncClient() as client:
                if method.upper() == "GET":
                    resp = await client.get(
                        f"{HA_URL}{endpoint}",
                        headers=self._get_headers(),
                        timeout=10.0
                    )
                elif method.upper() == "POST":
                    resp = await client.post(
                        f"{HA_URL}{endpoint}",
                        headers=self._get_headers(),
                        json=json_data,
                        timeout=10.0
                    )
                else:
                    return None
                
                if resp.status_code in (200, 201):
                    return resp.json() if resp.content else {}
                else:
                    logger.warning(f"HA API error {resp.status_code}: {resp.text[:200]}")
                    return None
                    
        except Exception as e:
            logger.error(f"HA API request failed: {e}")
            return None
    
    async def initialize(self) -> bool:
        """Discover media_player entities."""
        try:
            await self._discover_devices()
            self._initialized = True
            return True
        except Exception as e:
            logger.error(f"Failed to initialize HA output: {e}")
            return False
    
    async def _discover_devices(self):
        """Discover media_player entities from Home Assistant."""
        states = await self._api_request("GET", "/api/states")
        if not states:
            return
        
        for state in states:
            entity_id = state.get("entity_id", "")
            if not entity_id.startswith("media_player."):
                continue
            
            attrs = state.get("attributes", {})
            device_id = f"ha:{entity_id}"
            
            self._device_info[device_id] = DeviceInfo(
                id=device_id,
                name=attrs.get("friendly_name", entity_id),
                output_type=OutputType.HOMEASSISTANT,
                model=attrs.get("device_class"),
                manufacturer=attrs.get("manufacturer"),
                is_available=state.get("state") not in ("unavailable", "unknown"),
                supports_video=attrs.get("supported_features", 0) & 0x8000 != 0,  # SUPPORT_PLAY_MEDIA
                volume_level=int((attrs.get("volume_level") or 0) * 100),
                is_muted=attrs.get("is_volume_muted", False)
            )
        
        logger.info(f"Discovered {len(self._device_info)} HA media players")
    
    async def shutdown(self) -> None:
        """Clean up resources."""
        self._device_info.clear()
        self._initialized = False
    
    def _entity_id(self, device_id: str) -> str:
        """Extract entity_id from device_id."""
        return device_id.replace("ha:", "", 1)
    
    # ========================================
    # Device Discovery
    # ========================================
    
    async def discover_devices(self) -> List[DeviceInfo]:
        """Get available Home Assistant media players."""
        if not self._initialized:
            await self.initialize()
        return list(self._device_info.values())
    
    async def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        """Get information about a specific device."""
        return self._device_info.get(device_id)
    
    async def refresh_devices(self) -> int:
        """Refresh the device list."""
        await self._discover_devices()
        return len(self._device_info)
    
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
        """Play audio on a Home Assistant media player."""
        entity_id = self._entity_id(device_id)
        
        # Map content type to HA media content type
        ha_content_type = "music"
        if "video" in content_type:
            ha_content_type = "video"
        
        result = await self._api_request(
            "POST",
            "/api/services/media_player/play_media",
            {
                "entity_id": entity_id,
                "media_content_id": stream_url,
                "media_content_type": ha_content_type
            }
        )
        
        if result is not None:
            logger.info(f"Playing on HA {entity_id}")
            return True
        return False
    
    async def pause(self, device_id: str) -> bool:
        """Pause playback on a Home Assistant media player."""
        entity_id = self._entity_id(device_id)
        
        result = await self._api_request(
            "POST",
            "/api/services/media_player/media_pause",
            {"entity_id": entity_id}
        )
        return result is not None
    
    async def resume(self, device_id: str) -> bool:
        """Resume playback on a Home Assistant media player."""
        entity_id = self._entity_id(device_id)
        
        result = await self._api_request(
            "POST",
            "/api/services/media_player/media_play",
            {"entity_id": entity_id}
        )
        return result is not None
    
    async def stop(self, device_id: str) -> bool:
        """Stop playback on a Home Assistant media player."""
        entity_id = self._entity_id(device_id)
        
        result = await self._api_request(
            "POST",
            "/api/services/media_player/media_stop",
            {"entity_id": entity_id}
        )
        return result is not None
    
    async def seek(self, device_id: str, position_ms: int) -> bool:
        """Seek to a position on a Home Assistant media player."""
        entity_id = self._entity_id(device_id)
        
        result = await self._api_request(
            "POST",
            "/api/services/media_player/media_seek",
            {
                "entity_id": entity_id,
                "seek_position": position_ms / 1000
            }
        )
        return result is not None
    
    # ========================================
    # Volume Control
    # ========================================
    
    async def set_volume(self, device_id: str, volume: int) -> bool:
        """Set volume on a Home Assistant media player."""
        entity_id = self._entity_id(device_id)
        
        result = await self._api_request(
            "POST",
            "/api/services/media_player/volume_set",
            {
                "entity_id": entity_id,
                "volume_level": volume / 100
            }
        )
        return result is not None
    
    async def set_mute(self, device_id: str, muted: bool) -> bool:
        """Mute or unmute a Home Assistant media player."""
        entity_id = self._entity_id(device_id)
        
        result = await self._api_request(
            "POST",
            "/api/services/media_player/volume_mute",
            {
                "entity_id": entity_id,
                "is_volume_muted": muted
            }
        )
        return result is not None
    
    # ========================================
    # State
    # ========================================
    
    async def get_state(self, device_id: str) -> Optional[PlaybackState]:
        """Get current playback state of a Home Assistant media player."""
        entity_id = self._entity_id(device_id)
        
        state_data = await self._api_request("GET", f"/api/states/{entity_id}")
        if not state_data:
            return None
        
        ha_state = state_data.get("state", "idle")
        attrs = state_data.get("attributes", {})
        
        # Map HA state to OutputState
        state_map = {
            "playing": OutputState.PLAYING,
            "paused": OutputState.PAUSED,
            "buffering": OutputState.BUFFERING,
            "idle": OutputState.IDLE,
            "off": OutputState.IDLE,
            "standby": OutputState.IDLE,
            "unavailable": OutputState.ERROR
        }
        state = state_map.get(ha_state, OutputState.IDLE)
        
        # Calculate position in ms
        position_ms = 0
        if attrs.get("media_position"):
            position_ms = int(attrs["media_position"] * 1000)
        
        duration_ms = 0
        if attrs.get("media_duration"):
            duration_ms = int(attrs["media_duration"] * 1000)
        
        return PlaybackState(
            state=state,
            track_id=attrs.get("media_content_id"),
            track_title=attrs.get("media_title"),
            track_artist=attrs.get("media_artist"),
            position_ms=position_ms,
            duration_ms=duration_ms,
            volume=int((attrs.get("volume_level") or 0) * 100),
            is_muted=attrs.get("is_volume_muted", False)
        )


# Singleton instance
_ha_output: Optional[HomeAssistantOutput] = None


def get_homeassistant_output() -> Optional[HomeAssistantOutput]:
    """Get the singleton Home Assistant output instance."""
    global _ha_output
    
    if not HTTPX_AVAILABLE or not HA_URL or not HA_TOKEN:
        return None
    
    if _ha_output is None:
        try:
            _ha_output = HomeAssistantOutput()
        except ImportError:
            return None
    
    return _ha_output

