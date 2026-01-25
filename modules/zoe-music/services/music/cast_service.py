"""
Chromecast Integration Service
Discovers and controls Chromecast devices for music playback
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable
import threading

logger = logging.getLogger(__name__)

# Try to import pychromecast
try:
    import pychromecast
    from pychromecast.controllers.media import MediaController
    PYCHROMECAST_AVAILABLE = True
except ImportError:
    PYCHROMECAST_AVAILABLE = False
    logger.warning("pychromecast not installed - Chromecast features disabled")


@dataclass
class CastDevice:
    """Represents a discovered Chromecast device"""
    id: str                           # UUID
    friendly_name: str
    model_name: str
    ip_address: str
    port: int = 8009
    cast_type: str = 'audio'         # audio, video, group
    supports_video: bool = True
    is_available: bool = True
    current_app: Optional[str] = None
    last_discovered: datetime = field(default_factory=datetime.now)
    
    # Runtime state
    _cast: Any = field(default=None, repr=False)


class CastService:
    """
    Chromecast discovery and control service
    
    Features:
    - Discover Chromecast devices on the network
    - Play media (audio/video) on devices
    - Control playback (play, pause, seek, volume)
    - Get playback state
    """
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path
        self.devices: Dict[str, CastDevice] = {}
        self._browser = None
        self._discovery_callback: Optional[Callable] = None
        self._lock = threading.Lock()
        self._initialized = False
    
    async def init(self, db_path: str = None):
        """Initialize the Cast service"""
        if not PYCHROMECAST_AVAILABLE:
            logger.warning("Chromecast service not available - pychromecast not installed")
            return
        
        if db_path:
            self.db_path = db_path
        
        # Load known devices from database
        await self._load_devices_from_db()
        
        # Start discovery in background
        asyncio.create_task(self._start_discovery())
        
        self._initialized = True
        logger.info("CastService initialized")
    
    async def _load_devices_from_db(self):
        """Load known Cast devices from database"""
        if not self.db_path:
            return
        
        import aiosqlite
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    """SELECT id, friendly_name, model_name, ip_address, port,
                       cast_type, supports_video, is_available, current_app
                       FROM cast_devices"""
                ) as cursor:
                    async for row in cursor:
                        device = CastDevice(
                            id=row[0],
                            friendly_name=row[1],
                            model_name=row[2] or '',
                            ip_address=row[3],
                            port=row[4] or 8009,
                            cast_type=row[5] or 'audio',
                            supports_video=bool(row[6]),
                            is_available=False,  # Will be updated by discovery
                            current_app=row[7]
                        )
                        self.devices[device.id] = device
        except Exception as e:
            logger.error(f"Failed to load Cast devices from database: {e}")
    
    async def _start_discovery(self):
        """Start discovering Chromecast devices using the new API"""
        if not PYCHROMECAST_AVAILABLE:
            return
        
        try:
            # Use the simple discovery method that works across versions
            chromecasts, browser = pychromecast.get_chromecasts()
            
            # Process discovered devices
            for cast in chromecasts:
                try:
                    device_info = cast.cast_info if hasattr(cast, 'cast_info') else cast.device
                    device_id = str(device_info.uuid) if hasattr(device_info, 'uuid') else cast.uuid
                    
                    with self._lock:
                        device = CastDevice(
                            id=device_id,
                            friendly_name=device_info.friendly_name if hasattr(device_info, 'friendly_name') else cast.name,
                            model_name=getattr(device_info, 'model_name', '') or '',
                            ip_address=str(device_info.host) if hasattr(device_info, 'host') else str(cast.host),
                            port=getattr(device_info, 'port', 8009) or 8009,
                            cast_type=getattr(device_info, 'cast_type', 'audio') or 'audio',
                            supports_video=getattr(device_info, 'cast_type', 'audio') != 'audio',
                            is_available=True,
                            _cast=cast
                        )
                        self.devices[device_id] = device
                        logger.info(f"Discovered Chromecast: {device.friendly_name} ({device_id})")
                        
                except Exception as e:
                    logger.error(f"Error processing Chromecast device: {e}")
            
            # Store browser reference for later stop
            self._browser = browser
            
            logger.info(f"Chromecast discovery found {len(self.devices)} devices")
            
        except Exception as e:
            logger.error(f"Failed to start Chromecast discovery: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    async def discover_devices(self) -> List[CastDevice]:
        """Get all discovered Chromecast devices"""
        with self._lock:
            return [d for d in self.devices.values() if d.is_available]
    
    async def get_device(self, device_id: str) -> Optional[CastDevice]:
        """Get a specific device by ID"""
        return self.devices.get(device_id)
    
    async def play_on_device(
        self,
        device_id: str,
        media_url: str,
        metadata: Dict[str, Any] = None,
        media_type: str = "audio/mp4"
    ) -> bool:
        """
        Play media on a Chromecast device
        
        Args:
            device_id: Target device UUID
            media_url: URL of media to play
            metadata: Track metadata (title, artist, album, thumbnail)
            media_type: MIME type of media
            
        Returns:
            True if playback started successfully
        """
        if not PYCHROMECAST_AVAILABLE:
            return False
        
        device = self.devices.get(device_id)
        if not device or not device._cast:
            logger.warning(f"Device {device_id} not available")
            return False
        
        try:
            cast = device._cast
            
            # Wait for cast to be ready
            cast.wait()
            
            # Get media controller
            mc = cast.media_controller
            
            # Build metadata
            chrome_metadata = {
                'title': metadata.get('title', 'Unknown'),
                'artist': metadata.get('artist', ''),
                'albumName': metadata.get('album', ''),
            }
            
            thumbnail = metadata.get('thumbnail') or metadata.get('thumbnail_url')
            if thumbnail:
                chrome_metadata['images'] = [{'url': thumbnail}]
            
            # Determine media type
            if 'video' in media_type or metadata.get('is_video'):
                content_type = "video/mp4"
            else:
                content_type = "audio/mp4"
            
            # Start playback
            mc.play_media(
                media_url,
                content_type,
                title=chrome_metadata.get('title'),
                thumb=thumbnail,
                metadata=chrome_metadata
            )
            
            mc.block_until_active()
            
            logger.info(f"Started playback on {device.friendly_name}: {chrome_metadata.get('title')}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to play on Chromecast {device_id}: {e}")
            return False
    
    async def control(self, device_id: str, command: str, **kwargs) -> bool:
        """
        Send a control command to a Chromecast
        
        Args:
            device_id: Target device UUID
            command: Command (play, pause, stop, seek, volume, mute)
            **kwargs: Command-specific arguments
            
        Returns:
            True if command was sent successfully
        """
        if not PYCHROMECAST_AVAILABLE:
            return False
        
        device = self.devices.get(device_id)
        if not device or not device._cast:
            return False
        
        try:
            cast = device._cast
            mc = cast.media_controller
            
            if command == 'play':
                mc.play()
            elif command == 'pause':
                mc.pause()
            elif command == 'stop':
                mc.stop()
            elif command == 'seek':
                position = kwargs.get('position_ms', 0) / 1000
                mc.seek(position)
            elif command == 'volume':
                volume = kwargs.get('volume', 80) / 100
                cast.set_volume(volume)
            elif command == 'mute':
                cast.set_volume_muted(kwargs.get('muted', True))
            else:
                logger.warning(f"Unknown command: {command}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send command to Chromecast {device_id}: {e}")
            return False
    
    async def get_state(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get current playback state from a Chromecast"""
        if not PYCHROMECAST_AVAILABLE:
            return None
        
        device = self.devices.get(device_id)
        if not device or not device._cast:
            return None
        
        try:
            cast = device._cast
            mc = cast.media_controller
            status = mc.status
            
            if not status:
                return {
                    'is_playing': False,
                    'track_id': None
                }
            
            return {
                'is_playing': status.player_state == 'PLAYING',
                'is_paused': status.player_state == 'PAUSED',
                'current_time': int(status.current_time * 1000) if status.current_time else 0,
                'duration': int(status.duration * 1000) if status.duration else 0,
                'volume': int((cast.status.volume_level or 0) * 100),
                'muted': cast.status.volume_muted,
                'title': status.title,
                'artist': status.artist,
                'album': status.album_name,
                'thumbnail': status.images[0].url if status.images else None
            }
            
        except Exception as e:
            logger.error(f"Failed to get state from Chromecast {device_id}: {e}")
            return None
    
    def set_discovery_callback(self, callback: Callable):
        """Set a callback to be called when devices are discovered"""
        self._discovery_callback = callback
    
    async def stop_discovery(self):
        """Stop the discovery browser"""
        if self._browser:
            try:
                pychromecast.discovery.stop_discovery(self._browser)
            except Exception:
                pass
            self._browser = None
    
    async def save_device_to_db(self, device: CastDevice):
        """Save or update a device in the database"""
        if not self.db_path:
            return
        
        import aiosqlite
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT OR REPLACE INTO cast_devices 
                       (id, friendly_name, model_name, ip_address, port,
                        cast_type, supports_video, is_available, current_app, last_discovered_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (device.id, device.friendly_name, device.model_name,
                     device.ip_address, device.port, device.cast_type,
                     device.supports_video, device.is_available,
                     device.current_app, datetime.now())
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to save Cast device to database: {e}")


# Global instance
cast_service = CastService()

