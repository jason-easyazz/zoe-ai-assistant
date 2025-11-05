#!/usr/bin/env python3
"""
Zoe Home Assistant MCP Bridge Service
Provides MCP tools for controlling Home Assistant devices and automations
"""

from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import httpx
import json
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.append('/app')

app = FastAPI(title="Zoe Home Assistant MCP Bridge", version="1.0.0")

# Configuration
HA_BASE_URL = os.getenv("HA_BASE_URL", "http://homeassistant:8123")
HA_ACCESS_TOKEN = os.getenv("HA_ACCESS_TOKEN", "")

# Initialize Home Assistant bridge service
class HomeAssistantBridge:
    def __init__(self, base_url: str, access_token: str):
        self.base_url = base_url.rstrip('/')
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
    
    async def _make_request(self, method: str, endpoint: str, data: Dict = None) -> Dict:
        """Make HTTP request to Home Assistant API"""
        url = f"{self.base_url}/api/{endpoint}"
        
        async with httpx.AsyncClient() as client:
            try:
                if method.upper() == "GET":
                    response = await client.get(url, headers=self.headers, timeout=10.0)
                elif method.upper() == "POST":
                    response = await client.post(url, headers=self.headers, json=data, timeout=10.0)
                elif method.upper() == "PUT":
                    response = await client.put(url, headers=self.headers, json=data, timeout=10.0)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                
                if response.status_code == 200:
                    return response.json()
                else:
                    raise HTTPException(status_code=response.status_code, detail=response.text)
                    
            except httpx.TimeoutException:
                raise HTTPException(status_code=408, detail="Home Assistant request timeout")
            except httpx.ConnectError:
                raise HTTPException(status_code=503, detail="Cannot connect to Home Assistant")
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Home Assistant API error: {str(e)}")
    
    async def get_states(self) -> List[Dict]:
        """Get all entity states from Home Assistant"""
        return await self._make_request("GET", "states")
    
    async def get_services(self) -> Dict:
        """Get all available services from Home Assistant"""
        return await self._make_request("GET", "services")
    
    async def call_service(self, domain: str, service: str, entity_id: str = None, data: Dict = None) -> Dict:
        """Call a Home Assistant service"""
        endpoint = f"services/{domain}/{service}"
        payload = {}
        
        if entity_id:
            payload["entity_id"] = entity_id
        if data:
            payload.update(data)
        
        return await self._make_request("POST", endpoint, payload)
    
    async def get_automations(self) -> List[Dict]:
        """Get all automations from Home Assistant"""
        return await self._make_request("GET", "automation")
    
    async def trigger_automation(self, automation_id: str) -> Dict:
        """Trigger an automation"""
        return await self.call_service("automation", "trigger", automation_id)
    
    async def get_scenes(self) -> List[Dict]:
        """Get all scenes from Home Assistant"""
        return await self._make_request("GET", "scene")
    
    async def activate_scene(self, scene_id: str) -> Dict:
        """Activate a scene"""
        return await self.call_service("scene", "turn_on", scene_id)
    
    async def get_scripts(self) -> List[Dict]:
        """Get all scripts from Home Assistant"""
        return await self._make_request("GET", "script")
    
    async def run_script(self, script_id: str, variables: Dict = None) -> Dict:
        """Run a script"""
        data = {}
        if variables:
            data["variables"] = variables
        return await self.call_service("script", "turn_on", script_id, data)

# Initialize bridge
ha_bridge = HomeAssistantBridge(HA_BASE_URL, HA_ACCESS_TOKEN)

# Pydantic models
class DeviceControlRequest(BaseModel):
    entity_id: str
    action: str  # 'turn_on', 'turn_off', 'toggle', 'set_brightness', 'set_color', etc.
    data: Optional[Dict[str, Any]] = None

class AutomationTriggerRequest(BaseModel):
    automation_id: str
    variables: Optional[Dict[str, Any]] = None

class SceneActivateRequest(BaseModel):
    scene_id: str

class ScriptRunRequest(BaseModel):
    script_id: str
    variables: Optional[Dict[str, Any]] = None

# API Endpoints
@app.get("/")
async def root():
    """Service health check"""
    try:
        # Test connection to Home Assistant
        states = await ha_bridge.get_states()
        return {
            "service": "Zoe Home Assistant MCP Bridge",
            "status": "healthy",
            "version": "1.0.0",
            "ha_connected": True,
            "entities_count": len(states)
        }
    except Exception as e:
        return {
            "service": "Zoe Home Assistant MCP Bridge",
            "status": "unhealthy",
            "version": "1.0.0",
            "ha_connected": False,
            "error": str(e)
        }

@app.get("/entities")
async def get_entities(
    domain: Optional[str] = Query(None, description="Filter by domain (e.g., 'light', 'switch', 'sensor')"),
    state: Optional[str] = Query(None, description="Filter by state (e.g., 'on', 'off')")
):
    """Get all entities from Home Assistant with optional filtering"""
    try:
        states = await ha_bridge.get_states()
        
        # Filter by domain if specified
        if domain:
            states = [s for s in states if s.get("entity_id", "").startswith(f"{domain}.")]
        
        # Filter by state if specified
        if state:
            states = [s for s in states if s.get("state") == state]
        
        # Format response
        entities = []
        for state in states:
            entities.append({
                "entity_id": state.get("entity_id"),
                "state": state.get("state"),
                "attributes": state.get("attributes", {}),
                "last_changed": state.get("last_changed"),
                "last_updated": state.get("last_updated")
            })
        
        return {"entities": entities, "count": len(entities)}
        
    except HTTPException as e:
        # Return a proper error response instead of raising
        return {
            "entities": [],
            "count": 0,
            "error": e.detail,
            "status": e.status_code
        }
    except Exception as e:
        return {
            "entities": [],
            "count": 0,
            "error": f"Failed to get entities: {str(e)}",
            "status": 500
        }

@app.get("/entities/{entity_id}")
async def get_entity(entity_id: str):
    """Get specific entity state from Home Assistant"""
    try:
        states = await ha_bridge.get_states()
        
        entity = next((s for s in states if s.get("entity_id") == entity_id), None)
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")
        
        return {
            "entity_id": entity.get("entity_id"),
            "state": entity.get("state"),
            "attributes": entity.get("attributes", {}),
            "last_changed": entity.get("last_changed"),
            "last_updated": entity.get("last_updated")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/devices/control")
async def control_device(request: DeviceControlRequest):
    """Control a device (light, switch, etc.) in Home Assistant"""
    try:
        entity_id = request.entity_id
        action = request.action
        data = request.data or {}
        
        # Map common actions to Home Assistant services
        service_mapping = {
            "turn_on": "turn_on",
            "turn_off": "turn_off",
            "toggle": "toggle",
            "set_brightness": "turn_on",
            "set_color": "turn_on",
            "set_temperature": "turn_on"
        }
        
        if action not in service_mapping:
            raise HTTPException(status_code=400, detail=f"Unsupported action: {action}")
        
        service = service_mapping[action]
        domain = entity_id.split(".")[0]
        
        # Prepare service data
        service_data = {"entity_id": entity_id}
        
        if action == "set_brightness" and "brightness" in data:
            service_data["brightness"] = data["brightness"]
        elif action == "set_color" and "rgb_color" in data:
            service_data["rgb_color"] = data["rgb_color"]
        elif action == "set_temperature" and "color_temp" in data:
            service_data["color_temp"] = data["color_temp"]
        
        # Add any additional data
        service_data.update(data)
        
        result = await ha_bridge.call_service(domain, service, entity_id, service_data)
        
        return {
            "message": f"Successfully executed {action} on {entity_id}",
            "result": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/lights")
async def get_lights():
    """Get all lights from Home Assistant"""
    try:
        states = await ha_bridge.get_states()
        lights = [s for s in states if s.get("entity_id", "").startswith("light.")]
        
        formatted_lights = []
        for light in lights:
            attributes = light.get("attributes", {})
            formatted_lights.append({
                "entity_id": light.get("entity_id"),
                "name": attributes.get("friendly_name", light.get("entity_id")),
                "state": light.get("state"),
                "brightness": attributes.get("brightness"),
                "color_temp": attributes.get("color_temp"),
                "rgb_color": attributes.get("rgb_color"),
                "supported_features": attributes.get("supported_features", 0)
            })
        
        return {"lights": formatted_lights, "count": len(formatted_lights)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/switches")
async def get_switches():
    """Get all switches from Home Assistant"""
    try:
        states = await ha_bridge.get_states()
        switches = [s for s in states if s.get("entity_id", "").startswith("switch.")]
        
        formatted_switches = []
        for switch in switches:
            attributes = switch.get("attributes", {})
            formatted_switches.append({
                "entity_id": switch.get("entity_id"),
                "name": attributes.get("friendly_name", switch.get("entity_id")),
                "state": switch.get("state"),
                "device_class": attributes.get("device_class")
            })
        
        return {"switches": formatted_switches, "count": len(formatted_switches)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sensors")
async def get_sensors():
    """Get all sensors from Home Assistant"""
    try:
        states = await ha_bridge.get_states()
        sensors = [s for s in states if s.get("entity_id", "").startswith("sensor.")]
        
        formatted_sensors = []
        for sensor in sensors:
            attributes = sensor.get("attributes", {})
            formatted_sensors.append({
                "entity_id": sensor.get("entity_id"),
                "name": attributes.get("friendly_name", sensor.get("entity_id")),
                "state": sensor.get("state"),
                "unit_of_measurement": attributes.get("unit_of_measurement"),
                "device_class": attributes.get("device_class")
            })
        
        return {"sensors": formatted_sensors, "count": len(formatted_sensors)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/automations")
async def get_automations():
    """Get all automations from Home Assistant"""
    try:
        automations = await ha_bridge.get_automations()
        
        formatted_automations = []
        for automation in automations:
            formatted_automations.append({
                "entity_id": automation.get("entity_id"),
                "name": automation.get("attributes", {}).get("friendly_name", automation.get("entity_id")),
                "state": automation.get("state"),
                "last_triggered": automation.get("attributes", {}).get("last_triggered")
            })
        
        return {"automations": formatted_automations, "count": len(formatted_automations)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/automations/trigger")
async def trigger_automation(request: AutomationTriggerRequest):
    """Trigger an automation in Home Assistant"""
    try:
        result = await ha_bridge.trigger_automation(request.automation_id)
        
        return {
            "message": f"Successfully triggered automation {request.automation_id}",
            "result": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/scenes")
async def get_scenes():
    """Get all scenes from Home Assistant"""
    try:
        scenes = await ha_bridge.get_scenes()
        
        formatted_scenes = []
        for scene in scenes:
            formatted_scenes.append({
                "entity_id": scene.get("entity_id"),
                "name": scene.get("attributes", {}).get("friendly_name", scene.get("entity_id")),
                "state": scene.get("state")
            })
        
        return {"scenes": formatted_scenes, "count": len(formatted_scenes)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scenes/activate")
async def activate_scene(request: SceneActivateRequest):
    """Activate a scene in Home Assistant"""
    try:
        result = await ha_bridge.activate_scene(request.scene_id)
        
        return {
            "message": f"Successfully activated scene {request.scene_id}",
            "result": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/scripts")
async def get_scripts():
    """Get all scripts from Home Assistant"""
    try:
        scripts = await ha_bridge.get_scripts()
        
        formatted_scripts = []
        for script in scripts:
            formatted_scripts.append({
                "entity_id": script.get("entity_id"),
                "name": script.get("attributes", {}).get("friendly_name", script.get("entity_id")),
                "state": script.get("state")
            })
        
        return {"scripts": formatted_scripts, "count": len(formatted_scripts)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scripts/run")
async def run_script(request: ScriptRunRequest):
    """Run a script in Home Assistant"""
    try:
        result = await ha_bridge.run_script(request.script_id, request.variables)
        
        return {
            "message": f"Successfully ran script {request.script_id}",
            "result": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/services")
async def get_services():
    """Get all available services from Home Assistant"""
    try:
        services = await ha_bridge.get_services()
        
        return {"services": services}
        
    except HTTPException as e:
        # Return a proper error response instead of raising
        return {
            "services": {},
            "error": e.detail,
            "status": e.status_code
        }
    except Exception as e:
        return {
            "services": {},
            "error": f"Failed to get services: {str(e)}",
            "status": 500
        }

@app.get("/analysis")
async def analyze_home_assistant():
    """Get comprehensive analysis of Home Assistant setup"""
    try:
        states = await ha_bridge.get_states()
        automations = await ha_bridge.get_automations()
        scenes = await ha_bridge.get_scenes()
        scripts = await ha_bridge.get_scripts()
        
        # Analyze entities by domain
        domains = {}
        for state in states:
            domain = state.get("entity_id", "").split(".")[0]
            if domain not in domains:
                domains[domain] = {"count": 0, "states": {}}
            domains[domain]["count"] += 1
            
            entity_state = state.get("state")
            if entity_state not in domains[domain]["states"]:
                domains[domain]["states"][entity_state] = 0
            domains[domain]["states"][entity_state] += 1
        
        # Calculate insights
        total_entities = len(states)
        total_automations = len(automations)
        total_scenes = len(scenes)
        total_scripts = len(scripts)
        
        # Find most common domains
        most_common_domain = max(domains.keys(), key=lambda k: domains[k]["count"]) if domains else None
        
        # Find entities that are on
        on_entities = sum(1 for state in states if state.get("state") == "on")
        
        analysis = {
            "summary": {
                "total_entities": total_entities,
                "total_automations": total_automations,
                "total_scenes": total_scenes,
                "total_scripts": total_scripts,
                "entities_on": on_entities,
                "entities_off": total_entities - on_entities
            },
            "domains": domains,
            "insights": {
                "most_common_domain": most_common_domain,
                "automation_density": total_automations / total_entities if total_entities > 0 else 0,
                "scene_density": total_scenes / total_entities if total_entities > 0 else 0,
                "script_density": total_scripts / total_entities if total_entities > 0 else 0,
                "automation_level": "high" if total_automations > 10 else "medium" if total_automations > 5 else "low",
                "scene_level": "high" if total_scenes > 5 else "medium" if total_scenes > 2 else "low"
            }
        }
        
        return {"analysis": analysis}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)

