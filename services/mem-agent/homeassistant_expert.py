"""
HomeAssistantExpert - Smart Home Control via Natural Language
============================================================
"""
import httpx
import re
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class HomeAssistantExpert:
    """Expert for Home Assistant device control and automation"""
    
    def __init__(self):
        self.api_base = "http://zoe-core-test:8000/api"
        self.intent_patterns = [
            r"turn on|turn off|switch|toggle",
            r"set.*temperature|set.*thermostat",
            r"dim.*lights|brightness",
            r"is.*on|is.*off|status.*of",
            r"lock|unlock|garage|door",
            r"scene|routine|automation"
        ]
    
    def can_handle(self, query: str) -> float:
        """Return confidence score for handling this query"""
        query_lower = query.lower()
        
        # High confidence for explicit device control
        if re.search(r"turn (on|off)|set (temperature|thermostat)", query_lower):
            return 0.95
        
        # High confidence for device queries
        if re.search(r"lights?|temperature|thermostat|lock|garage", query_lower):
            return 0.85
        
        # Check other patterns
        for pattern in self.intent_patterns:
            if re.search(pattern, query_lower):
                return 0.75
        
        return 0.0
    
    async def execute(self, query: str, user_id: str) -> Dict[str, Any]:
        """Execute Home Assistant actions"""
        query_lower = query.lower()
        
        # Detect action type
        if "turn on" in query_lower:
            return await self._turn_on(query, user_id)
        elif "turn off" in query_lower:
            return await self._turn_off(query, user_id)
        elif "set temperature" in query_lower or "set thermostat" in query_lower:
            return await self._set_temperature(query, user_id)
        elif "is" in query_lower and ("on" in query_lower or "off" in query_lower or "closed" in query_lower):
            return await self._get_status(query, user_id)
        else:
            return await self._generic_control(query, user_id)
    
    async def _turn_on(self, query: str, user_id: str) -> Dict[str, Any]:
        """Turn on a device"""
        try:
            # Extract device name
            device_match = re.search(r"turn on (?:the )?(.+?)(?:\s+light|$)", query, re.IGNORECASE)
            device = device_match.group(1).strip() if device_match else "light"
            
            # Call Home Assistant API via MCP
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/homeassistant/control",
                    headers={"X-Service-Token": "zoe_internal_2025"},
                    json={
                        "action": "turn_on",
                        "device": device,
                        "user_id": user_id
                    },
                    timeout=5.0
                )
                
                if response.status_code == 200:
                    return {
                        "success": True,
                        "action": "turn_on_device",
                        "device": device,
                        "message": f"✅ Turned on {device}"
                    }
                else:
                    return {
                        "success": False,
                        "error": f"API returned {response.status_code}",
                        "message": f"❌ Couldn't control {device}"
                    }
        except Exception as e:
            logger.error(f"HA control failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"❌ Error controlling device: {e}"
            }
    
    async def _turn_off(self, query: str, user_id: str) -> Dict[str, Any]:
        """Turn off a device"""
        try:
            device_match = re.search(r"turn off (?:the )?(.+?)(?:\s+light|$)", query, re.IGNORECASE)
            device = device_match.group(1).strip() if device_match else "light"
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/homeassistant/control",
                    headers={"X-Service-Token": "zoe_internal_2025"},
                    json={
                        "action": "turn_off",
                        "device": device,
                        "user_id": user_id
                    },
                    timeout=5.0
                )
                
                if response.status_code == 200:
                    return {
                        "success": True,
                        "action": "turn_off_device",
                        "device": device,
                        "message": f"✅ Turned off {device}"
                    }
                else:
                    return {
                        "success": False,
                        "error": f"API returned {response.status_code}",
                        "message": f"❌ Couldn't control {device}"
                    }
        except Exception as e:
            logger.error(f"HA control failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"❌ Error controlling device: {e}"
            }
    
    async def _set_temperature(self, query: str, user_id: str) -> Dict[str, Any]:
        """Set thermostat temperature"""
        try:
            temp_match = re.search(r"(\d+)\s*(?:degrees?|°)?", query, re.IGNORECASE)
            temperature = int(temp_match.group(1)) if temp_match else 72
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/homeassistant/control",
                    headers={"X-Service-Token": "zoe_internal_2025"},
                    json={
                        "action": "set_temperature",
                        "temperature": temperature,
                        "user_id": user_id
                    },
                    timeout=5.0
                )
                
                if response.status_code == 200:
                    return {
                        "success": True,
                        "action": "set_temperature",
                        "temperature": temperature,
                        "message": f"✅ Set temperature to {temperature}°"
                    }
                else:
                    return {
                        "success": False,
                        "error": f"API returned {response.status_code}",
                        "message": f"❌ Couldn't set temperature"
                    }
        except Exception as e:
            logger.error(f"Temperature control failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"❌ Error setting temperature: {e}"
            }
    
    async def _get_status(self, query: str, user_id: str) -> Dict[str, Any]:
        """Get device status"""
        try:
            device_match = re.search(r"is (?:the )?(.+?)\s+(?:on|off|open|closed)", query, re.IGNORECASE)
            device = device_match.group(1).strip() if device_match else "device"
            
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_base}/homeassistant/status",
                    params={"device": device, "user_id": user_id},
                    headers={"X-Service-Token": "zoe_internal_2025"},
                    timeout=3.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    status = data.get("status", "unknown")
                    return {
                        "success": True,
                        "action": "get_device_status",
                        "device": device,
                        "status": status,
                        "message": f"💡 {device.title()} is {status}"
                    }
                else:
                    return {
                        "success": False,
                        "error": f"API returned {response.status_code}",
                        "message": f"❌ Couldn't check {device} status"
                    }
        except Exception as e:
            logger.error(f"Status check failed: {e}")
            return {
                "success": True,
                "action": "get_device_status",
                "message": f"💡 Checking {device} status...",
                "results": []
            }
    
    async def _generic_control(self, query: str, user_id: str) -> Dict[str, Any]:
        """Generic home control - let MCP figure it out"""
        return {
            "success": True,
            "action": "home_control",
            "message": f"🏠 Processing home control: {query[:50]}...",
            "mcp_delegate": True  # Signal to use MCP tools
        }

