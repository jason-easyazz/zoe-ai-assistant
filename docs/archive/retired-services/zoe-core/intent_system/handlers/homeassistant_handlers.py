"""
Home Assistant Intent Handlers
==============================

Handles Home Assistant smart home intents:
- HassTurnOn: Turn on devices/lights
- HassTurnOff: Turn off devices/lights
- HassToggle: Toggle device state
- HassSetBrightness: Set light brightness
- HassSetColor: Set light color
- HassClimateSetTemperature: Set thermostat temperature
- HassCoverOpen: Open blinds/curtains
- HassCoverClose: Close blinds/curtains
- HassLockDoor: Lock a door
- HassUnlockDoor: Unlock a door

Room-Aware Features:
- When no specific device is named, uses room from source device context
- Commands like "turn off the lights" from kitchen panel turn off kitchen lights
"""

import logging
import httpx
import sqlite3
import os
from typing import Dict, Any, Optional, List

from intent_system.classifiers import ZoeIntent

logger = logging.getLogger(__name__)

# Home Assistant configuration
HA_API_URL = os.getenv("HOMEASSISTANT_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.getenv("HOMEASSISTANT_TOKEN", "")
DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")

# Internal API for Home Assistant operations
INTERNAL_HA_API = "http://localhost:8000/api/homeassistant"


# ============================================================
# Room Context Resolution
# ============================================================

async def get_device_room(device_id: str) -> Optional[str]:
    """
    Get the room associated with a device.
    
    Args:
        device_id: Device identifier
        
    Returns:
        Room name or None
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT room FROM devices WHERE id = ?", (device_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0]:
            return row[0]
    except Exception as e:
        logger.warning(f"Could not get device room: {e}")
    
    return None


async def resolve_room_context(intent: ZoeIntent, context: Dict, user_id: str) -> Optional[str]:
    """
    Resolve target room from intent slots or device context.
    
    Priority:
    1. Explicit area/room in intent slots
    2. Room from source device (where command was issued)
    
    Args:
        intent: The intent with slots
        context: Request context (includes device_id)
        user_id: User identifier
        
    Returns:
        Room name or None
    """
    # Priority 1: Explicit area in intent
    area = intent.slots.get("area") or intent.slots.get("room")
    if area:
        return area.lower()
    
    # Priority 2: Room from source device
    source_device_id = context.get("device_id")
    if source_device_id:
        room = await get_device_room(source_device_id)
        if room:
            logger.debug(f"Resolved room '{room}' from device {source_device_id}")
            return room.lower()
    
    return None


async def get_entities_by_room(room: str, domain: str = "light") -> List[Dict]:
    """
    Get Home Assistant entities in a specific room.
    
    Args:
        room: Room name (e.g., "kitchen", "living room")
        domain: Entity domain (e.g., "light", "switch")
        
    Returns:
        List of matching entities
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{INTERNAL_HA_API}/entities",
                params={"domain": domain},
                headers={"X-Auth-Token": "internal"},
                timeout=5.0
            )
            
            if response.status_code != 200:
                return []
            
            data = response.json()
            entities = data.get("entities", [])
            
            # Filter by room - match room name in entity_id or friendly_name
            room_normalized = room.lower().replace(" ", "_")
            matching = []
            
            for entity in entities:
                entity_id = entity.get("entity_id", "")
                friendly_name = entity.get("attributes", {}).get("friendly_name", "") if isinstance(entity, dict) and "attributes" in entity else entity.get("friendly_name", "")
                
                # Check if room name appears in entity_id or friendly_name
                if room_normalized in entity_id.lower() or room.lower() in friendly_name.lower():
                    matching.append(entity)
            
            return matching
            
    except Exception as e:
        logger.error(f"Failed to get entities by room: {e}")
        return []


async def control_room_entities(
    room: str, 
    domain: str, 
    service: str, 
    data: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    Control all entities of a domain in a room.
    
    Args:
        room: Room name
        domain: Entity domain (light, switch, etc.)
        service: Service to call (turn_on, turn_off, toggle)
        data: Additional service data
        
    Returns:
        Result with success status and controlled entities
    """
    entities = await get_entities_by_room(room, domain)
    
    if not entities:
        return {"success": False, "error": f"No {domain}s found in {room}"}
    
    controlled = []
    failed = []
    
    for entity in entities:
        entity_id = entity.get("entity_id")
        result = await _call_ha_service(domain, service, entity_id, data)
        
        if result.get("success"):
            controlled.append(entity_id)
        else:
            failed.append(entity_id)
    
    return {
        "success": len(controlled) > 0,
        "controlled": controlled,
        "failed": failed,
        "room": room
    }


async def _call_ha_service(
    domain: str,
    service: str,
    entity_id: Optional[str] = None,
    data: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    Call a Home Assistant service through the internal API.
    
    Args:
        domain: Service domain (e.g., "light", "switch", "cover")
        service: Service name (e.g., "turn_on", "turn_off", "toggle")
        entity_id: Entity to control
        data: Additional service data
        
    Returns:
        Dict with success status and response
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{INTERNAL_HA_API}/service",
                json={
                    "service": f"{domain}.{service}",
                    "entity_id": entity_id,
                    "data": data or {}
                },
                headers={"X-Auth-Token": "internal"},
                timeout=10.0
            )
            
            if response.status_code == 200:
                return {"success": True, "data": response.json()}
            else:
                return {"success": False, "error": response.text}
                
    except Exception as e:
        logger.error(f"HA service call failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


def _resolve_entity_id(name: str, domain: str = "light") -> str:
    """
    Resolve a friendly name to an entity_id.
    
    Args:
        name: Friendly name like "living room" or "kitchen light"
        domain: Entity domain (light, switch, cover, etc.)
        
    Returns:
        Entity ID like "light.living_room"
    """
    # Normalize the name
    normalized = name.lower().strip()
    
    # Remove common suffixes
    for suffix in [" light", " lights", " lamp", " switch", " fan"]:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]
    
    # Convert to entity_id format
    entity_name = normalized.replace(" ", "_").replace("-", "_")
    
    return f"{domain}.{entity_name}"


def _get_friendly_name(entity_id: str) -> str:
    """Convert entity_id to friendly name."""
    # Extract name from entity_id
    if "." in entity_id:
        name = entity_id.split(".", 1)[1]
    else:
        name = entity_id
    
    # Convert underscores to spaces and title case
    return name.replace("_", " ").title()


async def handle_turn_on(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle HassTurnOn intent - turn on a device or room lights.
    
    Room-Aware: If no specific device named, uses room from source device.
    "Turn on the lights" from kitchen panel -> turns on kitchen lights.
    
    Slots:
        - name: Device name (optional if room context available)
        - area: Room/area (optional)
    """
    name = intent.slots.get("name", "")
    area = intent.slots.get("area", "")
    
    # Check for room-based control (no specific device named)
    if not name or name.lower() in ["lights", "the lights", "light"]:
        room = await resolve_room_context(intent, context, user_id)
        
        if room:
            # Control all lights in the room
            logger.info(f"Room-aware turn on: room={room}, device_id={context.get('device_id')}")
            result = await control_room_entities(room, "light", "turn_on")
            
            if result.get("success"):
                count = len(result.get("controlled", []))
                return {
                    "success": True,
                    "message": f"💡 Turned on {count} light{'s' if count != 1 else ''} in the {room}",
                    "data": {"room": room, "entities": result.get("controlled", []), "state": "on"}
                }
            else:
                # Fall through to try specific entity
                pass
        elif not name:
            return {
                "success": False,
                "message": "What would you like me to turn on?"
            }
    
    # Specific device named
    if area and area.lower() not in name.lower():
        full_name = f"{area} {name}"
    else:
        full_name = name
    
    # Resolve to entity_id
    entity_id = _resolve_entity_id(full_name, "light")
    friendly_name = _get_friendly_name(entity_id)
    
    # Call Home Assistant
    result = await _call_ha_service("light", "turn_on", entity_id)
    
    if result.get("success"):
        return {
            "success": True,
            "message": f"💡 Turned on {friendly_name}",
            "data": {"entity_id": entity_id, "state": "on"}
        }
    else:
        # Try as a switch if light failed
        entity_id = _resolve_entity_id(full_name, "switch")
        result = await _call_ha_service("switch", "turn_on", entity_id)
        
        if result.get("success"):
            return {
                "success": True,
                "message": f"🔌 Turned on {friendly_name}",
                "data": {"entity_id": entity_id, "state": "on"}
            }
        
        return {
            "success": False,
            "message": f"Sorry, I couldn't turn on {friendly_name}. It may not be available."
        }


async def handle_turn_off(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle HassTurnOff intent - turn off a device or room lights.
    
    Room-Aware: If no specific device named, uses room from source device.
    "Turn off the lights" from kitchen panel -> turns off kitchen lights.
    
    Slots:
        - name: Device name (optional if room context available)
        - area: Room/area (optional)
    """
    name = intent.slots.get("name", "")
    area = intent.slots.get("area", "")
    
    # Check for room-based control (no specific device named)
    if not name or name.lower() in ["lights", "the lights", "light"]:
        room = await resolve_room_context(intent, context, user_id)
        
        if room:
            # Control all lights in the room
            logger.info(f"Room-aware turn off: room={room}, device_id={context.get('device_id')}")
            result = await control_room_entities(room, "light", "turn_off")
            
            if result.get("success"):
                count = len(result.get("controlled", []))
                return {
                    "success": True,
                    "message": f"💡 Turned off {count} light{'s' if count != 1 else ''} in the {room}",
                    "data": {"room": room, "entities": result.get("controlled", []), "state": "off"}
                }
            else:
                # Fall through to try specific entity
                pass
        elif not name:
            return {
                "success": False,
                "message": "What would you like me to turn off?"
            }
    
    # Specific device named
    if area and area.lower() not in name.lower():
        full_name = f"{area} {name}"
    else:
        full_name = name
    
    entity_id = _resolve_entity_id(full_name, "light")
    friendly_name = _get_friendly_name(entity_id)
    
    result = await _call_ha_service("light", "turn_off", entity_id)
    
    if result.get("success"):
        return {
            "success": True,
            "message": f"💡 Turned off {friendly_name}",
            "data": {"entity_id": entity_id, "state": "off"}
        }
    else:
        entity_id = _resolve_entity_id(full_name, "switch")
        result = await _call_ha_service("switch", "turn_off", entity_id)
        
        if result.get("success"):
            return {
                "success": True,
                "message": f"🔌 Turned off {friendly_name}",
                "data": {"entity_id": entity_id, "state": "off"}
            }
        
        return {
            "success": False,
            "message": f"Sorry, I couldn't turn off {friendly_name}. It may not be available."
        }


async def handle_toggle(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle HassToggle intent - toggle a device or room lights.
    
    Room-Aware: If no specific device named, uses room from source device.
    
    Slots:
        - name: Device name (optional if room context available)
    """
    name = intent.slots.get("name", "")
    
    # Check for room-based control
    if not name or name.lower() in ["lights", "the lights", "light"]:
        room = await resolve_room_context(intent, context, user_id)
        
        if room:
            logger.info(f"Room-aware toggle: room={room}")
            result = await control_room_entities(room, "light", "toggle")
            
            if result.get("success"):
                count = len(result.get("controlled", []))
                return {
                    "success": True,
                    "message": f"💡 Toggled {count} light{'s' if count != 1 else ''} in the {room}",
                    "data": {"room": room, "entities": result.get("controlled", [])}
                }
        elif not name:
            return {
                "success": False,
                "message": "What would you like me to toggle?"
            }
    
    entity_id = _resolve_entity_id(name, "light")
    friendly_name = _get_friendly_name(entity_id)
    
    result = await _call_ha_service("light", "toggle", entity_id)
    
    if result.get("success"):
        return {
            "success": True,
            "message": f"💡 Toggled {friendly_name}",
            "data": {"entity_id": entity_id}
        }
    else:
        entity_id = _resolve_entity_id(name, "switch")
        result = await _call_ha_service("switch", "toggle", entity_id)
        
        if result.get("success"):
            return {
                "success": True,
                "message": f"🔌 Toggled {friendly_name}",
                "data": {"entity_id": entity_id}
            }
        
        return {
            "success": False,
            "message": f"Sorry, I couldn't toggle {friendly_name}."
        }


async def handle_set_brightness(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle HassSetBrightness intent - set light brightness.
    
    Slots:
        - name: Light name (required)
        - brightness: Brightness level 0-100 (required)
    """
    name = intent.slots.get("name", "")
    brightness = intent.slots.get("brightness")
    
    if not name:
        return {
            "success": False,
            "message": "Which light would you like to adjust?"
        }
    
    if brightness is None:
        return {
            "success": False,
            "message": "What brightness level? (0-100%)"
        }
    
    # Convert to integer if string
    try:
        brightness_val = int(brightness)
    except (ValueError, TypeError):
        return {
            "success": False,
            "message": f"I didn't understand the brightness level '{brightness}'."
        }
    
    # Clamp to valid range
    brightness_val = max(0, min(100, brightness_val))
    
    # Home Assistant uses 0-255 for brightness
    ha_brightness = int(brightness_val * 255 / 100)
    
    entity_id = _resolve_entity_id(name, "light")
    friendly_name = _get_friendly_name(entity_id)
    
    result = await _call_ha_service(
        "light", "turn_on", entity_id,
        data={"brightness": ha_brightness}
    )
    
    if result.get("success"):
        return {
            "success": True,
            "message": f"💡 Set {friendly_name} to {brightness_val}% brightness",
            "data": {"entity_id": entity_id, "brightness": brightness_val}
        }
    else:
        return {
            "success": False,
            "message": f"Sorry, I couldn't adjust {friendly_name}'s brightness."
        }


async def handle_set_color(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle HassSetColor intent - set light color.
    
    Slots:
        - name: Light name (required)
        - color: Color name (required)
    """
    name = intent.slots.get("name", "")
    color = intent.slots.get("color", "")
    
    if not name:
        return {
            "success": False,
            "message": "Which light would you like to change?"
        }
    
    if not color:
        return {
            "success": False,
            "message": "What color would you like?"
        }
    
    # Color name to RGB mapping
    color_map = {
        "red": [255, 0, 0],
        "green": [0, 255, 0],
        "blue": [0, 0, 255],
        "yellow": [255, 255, 0],
        "orange": [255, 165, 0],
        "purple": [128, 0, 128],
        "pink": [255, 192, 203],
        "white": [255, 255, 255],
        "cyan": [0, 255, 255],
        "warm white": [255, 244, 229],
        "cool white": [255, 255, 255],
    }
    
    rgb = color_map.get(color.lower(), [255, 255, 255])
    
    entity_id = _resolve_entity_id(name, "light")
    friendly_name = _get_friendly_name(entity_id)
    
    result = await _call_ha_service(
        "light", "turn_on", entity_id,
        data={"rgb_color": rgb}
    )
    
    if result.get("success"):
        return {
            "success": True,
            "message": f"🎨 Set {friendly_name} to {color}",
            "data": {"entity_id": entity_id, "color": color}
        }
    else:
        return {
            "success": False,
            "message": f"Sorry, I couldn't change {friendly_name}'s color."
        }


async def handle_set_temperature(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle HassClimateSetTemperature intent - set thermostat temperature.
    
    Slots:
        - name: Climate entity name (optional)
        - temperature: Target temperature (required)
    """
    name = intent.slots.get("name", "thermostat")
    temperature = intent.slots.get("temperature")
    
    if temperature is None:
        return {
            "success": False,
            "message": "What temperature would you like?"
        }
    
    try:
        temp_val = float(temperature)
    except (ValueError, TypeError):
        return {
            "success": False,
            "message": f"I didn't understand the temperature '{temperature}'."
        }
    
    entity_id = _resolve_entity_id(name, "climate")
    friendly_name = _get_friendly_name(entity_id)
    
    result = await _call_ha_service(
        "climate", "set_temperature", entity_id,
        data={"temperature": temp_val}
    )
    
    if result.get("success"):
        return {
            "success": True,
            "message": f"🌡️ Set thermostat to {temp_val}°",
            "data": {"entity_id": entity_id, "temperature": temp_val}
        }
    else:
        return {
            "success": False,
            "message": f"Sorry, I couldn't adjust the thermostat."
        }


async def handle_cover_open(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle HassCoverOpen intent - open blinds/curtains.
    
    Slots:
        - name: Cover name (required)
    """
    name = intent.slots.get("name", "")
    
    if not name:
        name = "blinds"  # Default to blinds
    
    entity_id = _resolve_entity_id(name, "cover")
    friendly_name = _get_friendly_name(entity_id)
    
    result = await _call_ha_service("cover", "open_cover", entity_id)
    
    if result.get("success"):
        return {
            "success": True,
            "message": f"🪟 Opening {friendly_name}",
            "data": {"entity_id": entity_id, "state": "open"}
        }
    else:
        return {
            "success": False,
            "message": f"Sorry, I couldn't open the {friendly_name}."
        }


async def handle_cover_close(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle HassCoverClose intent - close blinds/curtains.
    
    Slots:
        - name: Cover name (required)
    """
    name = intent.slots.get("name", "")
    
    if not name:
        name = "blinds"
    
    entity_id = _resolve_entity_id(name, "cover")
    friendly_name = _get_friendly_name(entity_id)
    
    result = await _call_ha_service("cover", "close_cover", entity_id)
    
    if result.get("success"):
        return {
            "success": True,
            "message": f"🪟 Closing {friendly_name}",
            "data": {"entity_id": entity_id, "state": "closed"}
        }
    else:
        return {
            "success": False,
            "message": f"Sorry, I couldn't close the {friendly_name}."
        }


async def handle_lock_door(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle HassLockDoor intent - lock a door.
    
    Slots:
        - name: Lock name (optional, defaults to "front door")
    """
    name = intent.slots.get("name", "front door")
    
    entity_id = _resolve_entity_id(name, "lock")
    friendly_name = _get_friendly_name(entity_id)
    
    result = await _call_ha_service("lock", "lock", entity_id)
    
    if result.get("success"):
        return {
            "success": True,
            "message": f"🔒 Locked the {friendly_name}",
            "data": {"entity_id": entity_id, "state": "locked"}
        }
    else:
        return {
            "success": False,
            "message": f"Sorry, I couldn't lock the {friendly_name}."
        }


async def handle_unlock_door(intent: ZoeIntent, user_id: str, context: Dict) -> Dict[str, Any]:
    """
    Handle HassUnlockDoor intent - unlock a door.
    
    Slots:
        - name: Lock name (optional, defaults to "front door")
    """
    name = intent.slots.get("name", "front door")
    
    entity_id = _resolve_entity_id(name, "lock")
    friendly_name = _get_friendly_name(entity_id)
    
    result = await _call_ha_service("lock", "unlock", entity_id)
    
    if result.get("success"):
        return {
            "success": True,
            "message": f"🔓 Unlocked the {friendly_name}",
            "data": {"entity_id": entity_id, "state": "unlocked"}
        }
    else:
        return {
            "success": False,
            "message": f"Sorry, I couldn't unlock the {friendly_name}."
        }

