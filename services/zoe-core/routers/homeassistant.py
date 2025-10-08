"""
Home Assistant Integration
Basic integration for home automation control
"""
from fastapi import APIRouter, HTTPException, Query
from auth_integration import require_permission, validate_session
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import requests
import os

router = APIRouter(prefix="/api/homeassistant", tags=["homeassistant"])

# Home Assistant configuration
HA_BASE_URL = os.getenv("HOMEASSISTANT_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.getenv("HOMEASSISTANT_TOKEN", "")

class ServiceCall(BaseModel):
    service: str
    entity_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

@router.get("/states")
async def get_states():
    """Get all Home Assistant states"""
    if not HA_TOKEN or HA_TOKEN.startswith("your-"):
        # Return mock data if no HA token or placeholder token
        return {
            "states": {
                "lights": [
                    {"entity_id": "light.living_room", "state": "off", "attributes": {"friendly_name": "Living Room"}},
                    {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
                    {"entity_id": "light.bedroom", "state": "off", "attributes": {"friendly_name": "Bedroom"}},
                    {"entity_id": "light.office", "state": "off", "attributes": {"friendly_name": "Office"}},
                    {"entity_id": "light.outdoor", "state": "on", "attributes": {"friendly_name": "Outdoor"}},
                    {"entity_id": "light.garage", "state": "off", "attributes": {"friendly_name": "Garage"}}
                ],
                "sensors": [
                    {"entity_id": "sensor.solar_output", "state": "2.4", "attributes": {"unit_of_measurement": "kW"}},
                    {"entity_id": "sensor.battery_level", "state": "85", "attributes": {"unit_of_measurement": "%"}},
                    {"entity_id": "sensor.temperature", "state": "22", "attributes": {"unit_of_measurement": "°C"}},
                    {"entity_id": "sensor.security_status", "state": "secure", "attributes": {"friendly_name": "Security"}}
                ]
            }
        }
    
    try:
        headers = {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(f"{HA_BASE_URL}/api/states", headers=headers, timeout=5)
        response.raise_for_status()
        
        states = response.json()
        
        # Organize states by domain
        organized_states = {
            "lights": [s for s in states if s["entity_id"].startswith("light.")],
            "sensors": [s for s in states if s["entity_id"].startswith("sensor.")],
            "switches": [s for s in states if s["entity_id"].startswith("switch.")],
            "covers": [s for s in states if s["entity_id"].startswith("cover.")],
            "climate": [s for s in states if s["entity_id"].startswith("climate.")]
        }
        
        return {"states": organized_states}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to connect to Home Assistant: {str(e)}")

@router.post("/service")
async def call_service(service_call: ServiceCall):
    """Call a Home Assistant service"""
    if not HA_TOKEN or HA_TOKEN.startswith("your-"):
        # Mock response for testing
        return {"message": f"Mock call to {service_call.service} on {service_call.entity_id}"}
    
    try:
        headers = {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json"
        }
        
        data = {
            "entity_id": service_call.entity_id,
            **(service_call.data or {})
        }
        
        response = requests.post(
            f"{HA_BASE_URL}/api/services/{service_call.service.replace('.', '/')}",
            headers=headers,
            json=data,
            timeout=5
        )
        response.raise_for_status()
        
        return {"message": "Service called successfully", "response": response.json()}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to call service: {str(e)}")

@router.get("/entities")
async def get_entities(domain: Optional[str] = None):
    """Get entities by domain"""
    if not HA_TOKEN:
        # Return mock entities
        mock_entities = {
            "light": [
                {"entity_id": "light.living_room", "friendly_name": "Living Room", "state": "off"},
                {"entity_id": "light.kitchen", "friendly_name": "Kitchen", "state": "on"},
                {"entity_id": "light.bedroom", "friendly_name": "Bedroom", "state": "off"},
                {"entity_id": "light.office", "friendly_name": "Office", "state": "off"},
                {"entity_id": "light.outdoor", "friendly_name": "Outdoor", "state": "on"},
                {"entity_id": "light.garage", "friendly_name": "Garage", "state": "off"}
            ],
            "sensor": [
                {"entity_id": "sensor.solar_output", "friendly_name": "Solar Output", "state": "2.4"},
                {"entity_id": "sensor.battery_level", "friendly_name": "Battery Level", "state": "85"},
                {"entity_id": "sensor.temperature", "friendly_name": "Temperature", "state": "22"},
                {"entity_id": "sensor.security_status", "friendly_name": "Security Status", "state": "secure"}
            ]
        }
        
        if domain:
            return {"entities": mock_entities.get(domain, [])}
        return {"entities": mock_entities}
    
    try:
        headers = {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json"
        }
        
        if domain:
            response = requests.get(f"{HA_BASE_URL}/api/states", headers=headers, timeout=5)
            response.raise_for_status()
            
            states = response.json()
            entities = [s for s in states if s["entity_id"].startswith(f"{domain}.")]
            
            return {"entities": entities}
        else:
            # Get all entities grouped by domain
            response = requests.get(f"{HA_BASE_URL}/api/states", headers=headers, timeout=5)
            response.raise_for_status()
            
            states = response.json()
            entities_by_domain = {}
            
            for state in states:
                domain = state["entity_id"].split(".")[0]
                if domain not in entities_by_domain:
                    entities_by_domain[domain] = []
                entities_by_domain[domain].append(state)
            
            return {"entities": entities_by_domain}
            
    except Exception as e:
        # Return a proper error response instead of raising an exception
        return {
            "entities": [],
            "count": 0,
            "error": f"Failed to get entities: {str(e)}",
            "status": 500
        }

@router.get("/config")
async def get_config():
    """Get Home Assistant configuration"""
    if not HA_TOKEN:
        return {
            "config": {
                "location_name": "Home",
                "time_zone": "UTC",
                "version": "Mock",
                "unit_system": "metric"
            }
        }
    
    try:
        headers = {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(f"{HA_BASE_URL}/api/config", headers=headers, timeout=5)
        response.raise_for_status()
        
        return {"config": response.json()}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get config: {str(e)}")

@router.get("/health")
async def health_check():
    """Check Home Assistant connectivity"""
    if not HA_TOKEN:
        return {"status": "mock", "message": "Running in mock mode"}
    
    try:
        headers = {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(f"{HA_BASE_URL}/api/", headers=headers, timeout=5)
        response.raise_for_status()
        
        return {"status": "connected", "message": "Connected to Home Assistant"}
        
    except Exception as e:
        return {"status": "disconnected", "message": f"Failed to connect: {str(e)}"}
