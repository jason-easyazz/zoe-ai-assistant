"""
AirPlay Integration Service
Discovers and controls AirPlay devices for music playback
Uses pyatv for Apple TV and other AirPlay devices
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

logger = logging.getLogger(__name__)

# Try to import pyatv
try:
    import pyatv
    from pyatv.const import Protocol, MediaType, DeviceState
    PYATV_AVAILABLE = True
except ImportError:
    PYATV_AVAILABLE = False
    logger.warning("pyatv not installed - AirPlay features disabled")


@dataclass
class AirPlayDevice:
    """Represents a discovered AirPlay device"""
    id: str                              # Unique identifier
    name: str
    model: str
    ip_address: str
    port: int
    
    # Device capabilities
    device_type: str = 'other'           # appletv, homepod, speaker, other
    supports_video: bool = False
    supports_screen_mirroring: bool = False
    airplay_version: int = 1
    
    # Authentication
    requires_pairing: bool = False
    is_paired: bool = False
    credentials: Optional[str] = None    # Encrypted pairing credentials
    
    # State
    is_available: bool = True
    is_playing: bool = False
    current_media: Optional[str] = None
    last_discovered: datetime = field(default_factory=datetime.now)
    
    # Runtime
    _atv: Any = field(default=None, repr=False)


class AirPlayService:
    """
    AirPlay discovery and control service using pyatv
    
    Features:
    - Discover AirPlay devices on the network
    - Play media on devices
    - Control playback (play, pause, seek, volume)
    - Get playback state
    - Handle pairing for devices that require it
    """
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path
        self.devices: Dict[str, AirPlayDevice] = {}
        self._discovery_callback: Optional[Callable] = None
        self._initialized = False
        self._scan_task: Optional[asyncio.Task] = None
    
    async def init(self, db_path: str = None):
        """Initialize the AirPlay service"""
        if not PYATV_AVAILABLE:
            logger.warning("AirPlay service not available - pyatv not installed")
            return
        
        if db_path:
            self.db_path = db_path
        
        # Load known devices from database
        await self._load_devices_from_db()
        
        # Start discovery
        asyncio.create_task(self._start_discovery())
        
        self._initialized = True
        logger.info("AirPlayService initialized")
    
    async def _load_devices_from_db(self):
        """Load known AirPlay devices from database"""
        if not self.db_path:
            return
        
        import aiosqlite
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    """SELECT id, name, model, ip_address, port, device_type,
                       supports_video, supports_screen_mirroring, airplay_version,
                       requires_pairing, is_paired, credentials
                       FROM airplay_devices"""
                ) as cursor:
                    async for row in cursor:
                        device = AirPlayDevice(
                            id=row[0],
                            name=row[1],
                            model=row[2] or '',
                            ip_address=row[3],
                            port=row[4] or 7000,
                            device_type=row[5] or 'other',
                            supports_video=bool(row[6]),
                            supports_screen_mirroring=bool(row[7]),
                            airplay_version=row[8] or 1,
                            requires_pairing=bool(row[9]),
                            is_paired=bool(row[10]),
                            credentials=row[11],
                            is_available=False  # Will be updated by discovery
                        )
                        self.devices[device.id] = device
        except Exception as e:
            logger.error(f"Failed to load AirPlay devices from database: {e}")
    
    async def _start_discovery(self):
        """Start discovering AirPlay devices"""
        if not PYATV_AVAILABLE:
            return
        
        try:
            # Scan for devices
            devices = await pyatv.scan(asyncio.get_event_loop(), timeout=10)
            
            for device_info in devices:
                device_id = device_info.identifier or str(device_info.address)
                
                # Determine device type
                device_type = 'other'
                model = device_info.device_info.model if device_info.device_info else ''
                if model:
                    model_lower = model.lower()
                    if 'apple tv' in model_lower:
                        device_type = 'appletv'
                    elif 'homepod' in model_lower:
                        device_type = 'homepod'
                    elif 'speaker' in model_lower or 'audio' in model_lower:
                        device_type = 'speaker'
                
                # Check for AirPlay support
                has_airplay = Protocol.AirPlay in device_info.services
                has_mrp = Protocol.MRP in device_info.services
                
                if device_id in self.devices:
                    # Update existing device
                    self.devices[device_id].is_available = True
                    self.devices[device_id].ip_address = str(device_info.address)
                    self.devices[device_id].last_discovered = datetime.now()
                else:
                    # New device
                    device = AirPlayDevice(
                        id=device_id,
                        name=device_info.name,
                        model=model,
                        ip_address=str(device_info.address),
                        port=7000,
                        device_type=device_type,
                        supports_video=device_type == 'appletv',
                        supports_screen_mirroring=has_mrp,
                        airplay_version=2 if has_mrp else 1,
                        requires_pairing=has_mrp,
                        is_available=True
                    )
                    self.devices[device_id] = device
                    logger.info(f"Discovered AirPlay device: {device.name} ({device_id})")
                    
                    # Notify callback
                    if self._discovery_callback:
                        await self._discovery_callback(device)
            
            logger.info(f"AirPlay discovery found {len(self.devices)} devices")
            
        except Exception as e:
            logger.error(f"Failed to scan for AirPlay devices: {e}")
    
    async def discover_devices(self) -> List[AirPlayDevice]:
        """Get all discovered AirPlay devices"""
        return [d for d in self.devices.values() if d.is_available]
    
    async def get_device(self, device_id: str) -> Optional[AirPlayDevice]:
        """Get a specific device by ID"""
        return self.devices.get(device_id)
    
    async def connect_to_device(self, device_id: str) -> Optional[Any]:
        """Connect to an AirPlay device"""
        if not PYATV_AVAILABLE:
            return None
        
        device = self.devices.get(device_id)
        if not device:
            return None
        
        # If already connected, return existing connection
        if device._atv:
            return device._atv
        
        try:
            # Find the device via scan
            devices = await pyatv.scan(asyncio.get_event_loop(), 
                                       identifier=device_id, 
                                       timeout=5)
            
            if not devices:
                logger.warning(f"AirPlay device {device_id} not found")
                return None
            
            device_info = devices[0]
            
            # Connect
            atv = await pyatv.connect(device_info, asyncio.get_event_loop())
            device._atv = atv
            
            logger.info(f"Connected to AirPlay device: {device.name}")
            return atv
            
        except Exception as e:
            logger.error(f"Failed to connect to AirPlay device {device_id}: {e}")
            return None
    
    async def play_on_device(
        self,
        device_id: str,
        media_url: str,
        metadata: Dict[str, Any] = None
    ) -> bool:
        """
        Play media on an AirPlay device
        
        Args:
            device_id: Target device ID
            media_url: URL of media to play
            metadata: Track metadata (title, artist, album, thumbnail)
            
        Returns:
            True if playback started successfully
        """
        if not PYATV_AVAILABLE:
            return False
        
        atv = await self.connect_to_device(device_id)
        if not atv:
            return False
        
        try:
            # Get the stream player
            stream = atv.stream
            
            # Start streaming
            await stream.play_url(media_url)
            
            logger.info(f"Started playback on AirPlay device {device_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to play on AirPlay device {device_id}: {e}")
            return False
    
    async def control(self, device_id: str, command: str, **kwargs) -> bool:
        """
        Send a control command to an AirPlay device
        
        Args:
            device_id: Target device ID
            command: Command (play, pause, stop, next, previous, seek, volume)
            **kwargs: Command-specific arguments
            
        Returns:
            True if command was sent successfully
        """
        if not PYATV_AVAILABLE:
            return False
        
        atv = await self.connect_to_device(device_id)
        if not atv:
            return False
        
        try:
            rc = atv.remote_control
            
            if command == 'play':
                await rc.play()
            elif command == 'pause':
                await rc.pause()
            elif command == 'stop':
                await rc.stop()
            elif command == 'next':
                await rc.next()
            elif command == 'previous':
                await rc.previous()
            elif command == 'seek':
                position = kwargs.get('position_ms', 0) / 1000
                await rc.set_position(position)
            elif command == 'volume_up':
                await rc.volume_up()
            elif command == 'volume_down':
                await rc.volume_down()
            elif command == 'volume':
                # Note: Direct volume control may not be available on all devices
                volume = kwargs.get('volume', 80)
                audio = atv.audio
                if audio:
                    await audio.set_volume(volume / 100.0)
            else:
                logger.warning(f"Unknown AirPlay command: {command}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send command to AirPlay device {device_id}: {e}")
            return False
    
    async def get_state(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get current playback state from an AirPlay device"""
        if not PYATV_AVAILABLE:
            return None
        
        atv = await self.connect_to_device(device_id)
        if not atv:
            return None
        
        try:
            playing = await atv.metadata.playing()
            
            # Map device state to boolean
            is_playing = playing.device_state in [
                DeviceState.Playing, 
                DeviceState.Seeking
            ] if hasattr(playing, 'device_state') else False
            
            is_paused = playing.device_state == DeviceState.Paused \
                if hasattr(playing, 'device_state') else False
            
            return {
                'is_playing': is_playing,
                'is_paused': is_paused,
                'current_time': int(playing.position * 1000) if playing.position else 0,
                'duration': int(playing.total_time * 1000) if playing.total_time else 0,
                'title': playing.title,
                'artist': playing.artist,
                'album': playing.album,
                'media_type': str(playing.media_type) if playing.media_type else None
            }
            
        except Exception as e:
            logger.error(f"Failed to get state from AirPlay device {device_id}: {e}")
            return None
    
    async def start_pairing(self, device_id: str) -> Optional[str]:
        """
        Start pairing process for a device that requires it
        
        Returns:
            Pairing code/instructions or None if failed
        """
        if not PYATV_AVAILABLE:
            return None
        
        device = self.devices.get(device_id)
        if not device:
            return None
        
        try:
            # Find device
            devices = await pyatv.scan(asyncio.get_event_loop(), 
                                       identifier=device_id, 
                                       timeout=5)
            
            if not devices:
                return None
            
            device_info = devices[0]
            
            # Start pairing
            pairing = await pyatv.pair(device_info, Protocol.AirPlay, asyncio.get_event_loop())
            
            await pairing.begin()
            
            if pairing.device_provides_pin:
                return "Enter the PIN shown on your device"
            else:
                pin = pairing.pin(1234)  # Default PIN
                return f"PIN: {pin}"
                
        except Exception as e:
            logger.error(f"Failed to start pairing for {device_id}: {e}")
            return None
    
    async def finish_pairing(self, device_id: str, pin: str) -> bool:
        """
        Finish pairing with a PIN
        
        Returns:
            True if pairing succeeded
        """
        # TODO: Implement full pairing flow
        # This would involve storing credentials in the database
        return False
    
    def set_discovery_callback(self, callback: Callable):
        """Set a callback to be called when devices are discovered"""
        self._discovery_callback = callback
    
    async def disconnect_device(self, device_id: str):
        """Disconnect from a device"""
        device = self.devices.get(device_id)
        if device and device._atv:
            try:
                device._atv.close()
            except Exception:
                pass
            device._atv = None
    
    async def refresh_devices(self):
        """Refresh the device list"""
        await self._start_discovery()
    
    async def save_device_to_db(self, device: AirPlayDevice):
        """Save or update a device in the database"""
        if not self.db_path:
            return
        
        import aiosqlite
        
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT OR REPLACE INTO airplay_devices 
                       (id, name, model, ip_address, port, device_type,
                        supports_video, supports_screen_mirroring, airplay_version,
                        requires_pairing, is_paired, credentials, is_available, last_discovered_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (device.id, device.name, device.model, device.ip_address,
                     device.port, device.device_type, device.supports_video,
                     device.supports_screen_mirroring, device.airplay_version,
                     device.requires_pairing, device.is_paired, device.credentials,
                     device.is_available, datetime.now())
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to save AirPlay device to database: {e}")


# Global instance
airplay_service = AirPlayService()

