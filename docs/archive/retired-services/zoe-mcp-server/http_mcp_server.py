#!/usr/bin/env python3
"""
HTTP wrapper for the advanced Zoe MCP Server
Provides all Zoe, Home Assistant, N8N, and Matrix tools via HTTP API
"""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Import the advanced MCP server
from main import ZoeMCPServer

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe Advanced MCP Server - HTTP API")

class ToolRequest(BaseModel):
    user_id: Optional[str] = "default"  # User ID for isolation
    _auth_token: Optional[str] = "default"
    _session_id: Optional[str] = "default"
    _context: Optional[Dict[str, Any]] = None  # Enhanced context for Samantha-level intelligence
    _user_preferences: Optional[Dict[str, Any]] = None  # User preferences for personalization

class AddToListRequest(ToolRequest):
    list_name: str
    task_text: str
    priority: str = "medium"

class CreatePersonRequest(ToolRequest):
    name: str
    relationship: Optional[str] = ""
    notes: Optional[str] = ""

class CreateCalendarEventRequest(ToolRequest):
    title: str
    start_date: str
    start_time: Optional[str] = ""
    description: Optional[str] = ""
    location: Optional[str] = ""

class ControlDeviceRequest(ToolRequest):
    entity_id: str
    action: str
    data: Optional[dict] = {}

class TriggerAutomationRequest(ToolRequest):
    automation_id: str

class ActivateSceneRequest(ToolRequest):
    scene_id: str

class ExecuteWorkflowRequest(ToolRequest):
    workflow_id: str
    input_data: Optional[dict] = {}

class CreateWorkflowRequest(ToolRequest):
    name: str
    nodes: list
    connections: dict
    active: bool = False

class SendMatrixMessageRequest(ToolRequest):
    room_id: str
    message: str
    message_type: str = "m.text"

class CreateMatrixRoomRequest(ToolRequest):
    name: str
    topic: Optional[str] = ""
    is_public: bool = False

class JoinMatrixRoomRequest(ToolRequest):
    room_id: str

class GetMatrixMessagesRequest(ToolRequest):
    room_id: str
    limit: int = 20

class SetMatrixPresenceRequest(ToolRequest):
    presence: str  # "online", "offline", "unavailable"
    status_msg: Optional[str] = ""

# Music Module Requests
class MusicSearchRequest(ToolRequest):
    query: str
    filter_type: str = "songs"  # songs, albums, artists, playlists
    limit: int = 10

class MusicPlayRequest(ToolRequest):
    query: Optional[str] = None
    track_id: Optional[str] = None
    source: str = "youtube"
    zone: Optional[str] = None

class MusicVolumeRequest(ToolRequest):
    volume: int  # 0-100
    zone: Optional[str] = None

class MusicQueueRequest(ToolRequest):
    track_id: str
    title: Optional[str] = None
    artist: Optional[str] = None

class MusicContextRequest(ToolRequest):
    pass  # Just needs user_id from ToolRequest

# Initialize the advanced MCP server
mcp_server = ZoeMCPServer()

# Music module URL
MUSIC_MODULE_URL = os.getenv("MUSIC_MODULE_URL", "http://zoe-music:8100")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "zoe-advanced-mcp-server", "tools": "zoe+homeassistant+n8n+matrix"}

@app.post("/tools/list")
async def list_tools(request: ToolRequest):
    """List all available advanced tools"""
    try:
        # Define tools directly since we can't easily call the MCP server's list_tools
        tools = [
            # Zoe Memory System
            {"name": "search_memories", "description": "Search through Zoe's memory system for people, projects, facts, and collections"},
            {"name": "create_person", "description": "Create a new person in Zoe's memory system"},
            {"name": "create_collection", "description": "Create a new collection in Zoe's memory system"},
            {"name": "get_people", "description": "Get all people from the people service"},
            {"name": "get_person_analysis", "description": "Get comprehensive analysis of a person including relationships and timeline"},
            {"name": "get_collections", "description": "Get all collections from the collections service"},
            {"name": "get_collection_analysis", "description": "Get comprehensive analysis of a collection including tiles, layouts, and curation"},
            
            # Calendar & Lists
            {"name": "create_calendar_event", "description": "Create a new calendar event"},
            {"name": "add_to_list", "description": "Add an item to a user's todo list"},
            {"name": "get_calendar_events", "description": "Get calendar events for a date range"},
            {"name": "get_lists", "description": "Get all user's todo lists"},
            
            # Home Assistant
            {"name": "get_home_assistant_devices", "description": "Get all devices from Home Assistant (lights, switches, sensors)"},
            {"name": "control_home_assistant_device", "description": "Control a Home Assistant device (turn on/off, set brightness, etc.)"},
            {"name": "get_home_assistant_automations", "description": "Get all automations from Home Assistant"},
            {"name": "trigger_home_assistant_automation", "description": "Trigger a Home Assistant automation"},
            {"name": "get_home_assistant_scenes", "description": "Get all scenes from Home Assistant"},
            {"name": "activate_home_assistant_scene", "description": "Activate a Home Assistant scene"},
            
            # N8N
            {"name": "get_n8n_workflows", "description": "Get all workflows from N8N"},
            {"name": "create_n8n_workflow", "description": "Create a new workflow in N8N"},
            {"name": "execute_n8n_workflow", "description": "Execute a workflow in N8N"},
            {"name": "get_n8n_executions", "description": "Get workflow executions from N8N"},
            {"name": "get_n8n_nodes", "description": "Get all available nodes from N8N"},
            
            # Developer
            {"name": "get_developer_tasks", "description": "Get developer tasks from the roadmap"},
            
            # Matrix Integration
            {"name": "send_matrix_message", "description": "Send a message to a Matrix room"},
            {"name": "get_matrix_rooms", "description": "Get list of Matrix rooms"},
            {"name": "create_matrix_room", "description": "Create a new Matrix room"},
            {"name": "join_matrix_room", "description": "Join a Matrix room"},
            {"name": "get_matrix_messages", "description": "Get recent messages from a Matrix room"},
            {"name": "set_matrix_presence", "description": "Set Matrix presence status"},
            
            # Music Module
            {"name": "music_search", "description": "Search for music (songs, albums, artists, playlists)"},
            {"name": "music_play_song", "description": "Play a song, album, or playlist"},
            {"name": "music_pause", "description": "Pause current playback"},
            {"name": "music_resume", "description": "Resume playback"},
            {"name": "music_skip", "description": "Skip to next track"},
            {"name": "music_set_volume", "description": "Set playback volume (0-100)"},
            {"name": "music_get_queue", "description": "Get current playback queue"},
            {"name": "music_add_to_queue", "description": "Add track to playback queue"},
            {"name": "music_get_recommendations", "description": "Get personalized music recommendations"},
            {"name": "music_get_context", "description": "Get music context for conversation"}
        ]
        
        return {
            "tools": tools,
            "total_tools": len(tools),
            "categories": {
                "zoe_memory": len([t for t in tools if any(x in t["name"] for x in ["memory", "person", "collection", "fact"])]),
                "zoe_lists": len([t for t in tools if any(x in t["name"] for x in ["list", "calendar", "event"])]),
                "home_assistant": len([t for t in tools if "home_assistant" in t["name"]]),
                "n8n": len([t for t in tools if "n8n" in t["name"]]),
                "developer": len([t for t in tools if "developer" in t["name"]]),
                "matrix": len([t for t in tools if "matrix" in t["name"]]),
                "music": len([t for t in tools if "music" in t["name"]])
            }
        }
        
    except Exception as e:
        logger.error(f"Error listing tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/add_to_list")
async def add_to_list(request: AddToListRequest):
    """Add item to list"""
    try:
        # 🔥 FIX: Use actual user_id from request instead of hardcoded 'default'
        user_id = request.user_id or "default"
        logger.info(f"✅ add_to_list: user_id={user_id}, task={request.task_text}")
        
        result = await mcp_server._add_to_list({
            "list_name": request.list_name,
            "task_text": request.task_text,
            "priority": request.priority
        }, type('UserContext', (), {'user_id': user_id, 'username': user_id})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "add_to_list"
        }
        
    except Exception as e:
        logger.error(f"Error adding to list: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/create_person")
async def create_person(request: CreatePersonRequest):
    """Create a new person"""
    try:
        result = await mcp_server._create_person({
            "name": request.name,
            "relationship": request.relationship,
            "notes": request.notes
        }, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "create_person"
        }
        
    except Exception as e:
        logger.error(f"Error creating person: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/create_calendar_event")
async def create_calendar_event(request: CreateCalendarEventRequest):
    """Create a calendar event"""
    try:
        # 🔥 FIX: Use actual user_id from request instead of hardcoded 'default'
        user_id = request.user_id or "default"
        logger.info(f"✅ create_calendar_event: user_id={user_id}, title={request.title}")
        
        result = await mcp_server._create_calendar_event({
            "title": request.title,
            "start_date": request.start_date,
            "start_time": request.start_time,
            "description": request.description,
            "location": request.location
        }, type('UserContext', (), {'user_id': user_id, 'username': user_id})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "create_calendar_event"
        }
        
    except Exception as e:
        logger.error(f"Error creating calendar event: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_lists")
async def get_lists(request: ToolRequest):
    """Get all lists"""
    try:
        result = await mcp_server._get_lists({}, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "get_lists"
        }
        
    except Exception as e:
        logger.error(f"Error getting lists: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_calendar_events")
async def get_calendar_events(request: ToolRequest):
    """Get calendar events"""
    try:
        result = await mcp_server._get_calendar_events({}, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "get_calendar_events"
        }
        
    except Exception as e:
        logger.error(f"Error getting calendar events: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# HOME ASSISTANT TOOLS
# ============================================================================

@app.post("/tools/get_home_assistant_devices")
async def get_home_assistant_devices(request: ToolRequest):
    """Get Home Assistant devices"""
    try:
        result = await mcp_server._get_home_assistant_devices({}, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "get_home_assistant_devices"
        }
        
    except Exception as e:
        logger.error(f"Error getting HA devices: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/control_home_assistant_device")
async def control_home_assistant_device(request: ControlDeviceRequest):
    """Control a Home Assistant device"""
    try:
        result = await mcp_server._control_home_assistant_device({
            "entity_id": request.entity_id,
            "action": request.action,
            "data": request.data
        }, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "control_home_assistant_device"
        }
        
    except Exception as e:
        logger.error(f"Error controlling HA device: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_home_assistant_automations")
async def get_home_assistant_automations(request: ToolRequest):
    """Get Home Assistant automations"""
    try:
        result = await mcp_server._get_home_assistant_automations({}, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "get_home_assistant_automations"
        }
        
    except Exception as e:
        logger.error(f"Error getting HA automations: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/trigger_home_assistant_automation")
async def trigger_home_assistant_automation(request: TriggerAutomationRequest):
    """Trigger a Home Assistant automation"""
    try:
        result = await mcp_server._trigger_home_assistant_automation({
            "automation_id": request.automation_id
        }, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "trigger_home_assistant_automation"
        }
        
    except Exception as e:
        logger.error(f"Error triggering HA automation: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_home_assistant_scenes")
async def get_home_assistant_scenes(request: ToolRequest):
    """Get Home Assistant scenes"""
    try:
        result = await mcp_server._get_home_assistant_scenes({}, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "get_home_assistant_scenes"
        }
        
    except Exception as e:
        logger.error(f"Error getting HA scenes: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/activate_home_assistant_scene")
async def activate_home_assistant_scene(request: ActivateSceneRequest):
    """Activate a Home Assistant scene"""
    try:
        result = await mcp_server._activate_home_assistant_scene({
            "scene_id": request.scene_id
        }, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "activate_home_assistant_scene"
        }
        
    except Exception as e:
        logger.error(f"Error activating HA scene: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# N8N TOOLS
# ============================================================================

@app.post("/tools/get_n8n_workflows")
async def get_n8n_workflows(request: ToolRequest):
    """Get N8N workflows"""
    try:
        result = await mcp_server._get_n8n_workflows({}, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "get_n8n_workflows"
        }
        
    except Exception as e:
        logger.error(f"Error getting N8N workflows: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/create_n8n_workflow")
async def create_n8n_workflow(request: CreateWorkflowRequest):
    """Create N8N workflow"""
    try:
        result = await mcp_server._create_n8n_workflow({
            "name": request.name,
            "nodes": request.nodes,
            "connections": request.connections,
            "active": request.active
        }, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "create_n8n_workflow"
        }
        
    except Exception as e:
        logger.error(f"Error creating N8N workflow: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/execute_n8n_workflow")
async def execute_n8n_workflow(request: ExecuteWorkflowRequest):
    """Execute N8N workflow"""
    try:
        result = await mcp_server._execute_n8n_workflow({
            "workflow_id": request.workflow_id,
            "input_data": request.input_data
        }, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "execute_n8n_workflow"
        }
        
    except Exception as e:
        logger.error(f"Error executing N8N workflow: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_n8n_executions")
async def get_n8n_executions(request: ToolRequest):
    """Get N8N executions"""
    try:
        result = await mcp_server._get_n8n_executions({}, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "get_n8n_executions"
        }
        
    except Exception as e:
        logger.error(f"Error getting N8N executions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_n8n_nodes")
async def get_n8n_nodes(request: ToolRequest):
    """Get N8N nodes"""
    try:
        result = await mcp_server._get_n8n_nodes({}, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "get_n8n_nodes"
        }
        
    except Exception as e:
        logger.error(f"Error getting N8N nodes: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# MEMORY SYSTEM TOOLS
# ============================================================================

@app.post("/tools/search_memories")
async def search_memories(request: ToolRequest):
    """Search memories"""
    try:
        result = await mcp_server._search_memories({
            "query": request.__dict__.get("query", ""),
            "memory_type": request.__dict__.get("memory_type", "all"),
            "limit": request.__dict__.get("limit", 10)
        }, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "search_memories"
        }
        
    except Exception as e:
        logger.error(f"Error searching memories: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_people")
async def get_people(request: ToolRequest):
    """Get people"""
    try:
        result = await mcp_server._get_people({}, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "get_people"
        }
        
    except Exception as e:
        logger.error(f"Error getting people: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/store_self_fact")
async def store_self_fact(request: ToolRequest):
    """Store a fact about the user themselves"""
    try:
        # Get user_id from request parameters, not as attribute
        params = dict(request)
        user_id = params.get('user_id', 'default')
        user_context = type('UserContext', (), {'user_id': user_id, 'username': user_id})()
        
        result = await mcp_server._store_self_fact(params, user_context)
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "store_self_fact"
        }
        
    except Exception as e:
        logger.error(f"Error storing self fact: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_self_info")
async def get_self_info(request: ToolRequest):
    """Get information about the user themselves"""
    try:
        # Extract user_id from request body
        user_id = request.user_id or "default"
        logger.info(f"🔍 get_self_info called with user_id: {user_id}")
        
        params = request.dict() if hasattr(request, 'dict') else dict(request)
        logger.info(f"🔍 params dict: {params}")
        
        # Create user context with proper user_id
        user_context = type('UserContext', (), {'user_id': user_id, 'username': user_id})()
        
        result = await mcp_server._get_self_info(params, user_context)
        logger.info(f"🔍 Result message preview: {result.content[0].text[:100]}")
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "get_self_info"
        }
        
    except Exception as e:
        logger.error(f"Error getting self info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_developer_tasks")
async def get_developer_tasks(request: ToolRequest):
    """Get developer tasks"""
    try:
        result = await mcp_server._get_developer_tasks({}, type('UserContext', (), {'user_id': 'default', 'username': 'default'})())
        
        return {
            "success": True,
            "message": result.content[0].text,
            "tool_name": "get_developer_tasks"
        }
        
    except Exception as e:
        logger.error(f"Error getting developer tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# MATRIX TOOLS
# ============================================================================

@app.post("/tools/send_matrix_message")
async def send_matrix_message(request: SendMatrixMessageRequest):
    """Send a message to a Matrix room"""
    try:
        # For now, return a placeholder response
        # In a real implementation, this would connect to Matrix
        return {
            "success": True,
            "message": f"Matrix message sent to room {request.room_id}: {request.message}",
            "tool_name": "send_matrix_message"
        }
        
    except Exception as e:
        logger.error(f"Error sending Matrix message: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_matrix_rooms")
async def get_matrix_rooms(request: ToolRequest):
    """Get list of Matrix rooms"""
    try:
        # For now, return a placeholder response
        return {
            "success": True,
            "message": "Matrix rooms retrieved successfully",
            "data": [
                {"room_id": "!room1:matrix.org", "name": "General Chat"},
                {"room_id": "!room2:matrix.org", "name": "Development"}
            ],
            "tool_name": "get_matrix_rooms"
        }
        
    except Exception as e:
        logger.error(f"Error getting Matrix rooms: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/create_matrix_room")
async def create_matrix_room(request: CreateMatrixRoomRequest):
    """Create a new Matrix room"""
    try:
        # For now, return a placeholder response
        return {
            "success": True,
            "message": f"Matrix room '{request.name}' created successfully",
            "tool_name": "create_matrix_room"
        }
        
    except Exception as e:
        logger.error(f"Error creating Matrix room: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/join_matrix_room")
async def join_matrix_room(request: JoinMatrixRoomRequest):
    """Join a Matrix room"""
    try:
        # For now, return a placeholder response
        return {
            "success": True,
            "message": f"Successfully joined Matrix room {request.room_id}",
            "tool_name": "join_matrix_room"
        }
        
    except Exception as e:
        logger.error(f"Error joining Matrix room: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/get_matrix_messages")
async def get_matrix_messages(request: GetMatrixMessagesRequest):
    """Get recent messages from a Matrix room"""
    try:
        # For now, return a placeholder response
        return {
            "success": True,
            "message": f"Retrieved {request.limit} messages from room {request.room_id}",
            "data": [
                {"sender": "@user1:matrix.org", "message": "Hello!", "timestamp": "2025-01-04T10:00:00Z"},
                {"sender": "@user2:matrix.org", "message": "Hi there!", "timestamp": "2025-01-04T10:01:00Z"}
            ],
            "tool_name": "get_matrix_messages"
        }
        
    except Exception as e:
        logger.error(f"Error getting Matrix messages: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/set_matrix_presence")
async def set_matrix_presence(request: SetMatrixPresenceRequest):
    """Set Matrix presence status"""
    try:
        # For now, return a placeholder response
        return {
            "success": True,
            "message": f"Matrix presence set to {request.presence}",
            "tool_name": "set_matrix_presence"
        }
        
    except Exception as e:
        logger.error(f"Error setting Matrix presence: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================
# MUSIC MODULE TOOLS
# ==============================================

import httpx

@app.post("/tools/music_search")
async def music_search(request: MusicSearchRequest):
    """Search for music"""
    try:
        user_id = request.user_id or "default"
        logger.info(f"✅ music_search: query={request.query}, user_id={user_id}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/search",
                json={
                    "query": request.query,
                    "filter_type": request.filter_type,
                    "limit": request.limit,
                    "user_id": user_id
                },
                timeout=10.0
            )
            result = response.json()
        
        return {
            "success": result.get("success", True),
            "results": result.get("results", []),
            "count": result.get("count", 0),
            "tool_name": "music_search"
        }
        
    except Exception as e:
        logger.error(f"Error searching music: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/music_play_song")
async def music_play_song(request: MusicPlayRequest):
    """Play a song"""
    try:
        user_id = request.user_id or "default"
        logger.info(f"✅ music_play_song: query={request.query}, user_id={user_id}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/play_song",
                json={
                    "query": request.query,
                    "track_id": request.track_id,
                    "source": request.source,
                    "zone": request.zone,
                    "user_id": user_id
                },
                timeout=10.0
            )
            result = response.json()
        
        return {
            "success": result.get("success", True),
            "status": result.get("status", ""),
            "track": result.get("track", {}),
            "tool_name": "music_play_song"
        }
        
    except Exception as e:
        logger.error(f"Error playing song: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/music_pause")
async def music_pause(request: ToolRequest):
    """Pause playback"""
    try:
        logger.info("✅ music_pause")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/pause",
                timeout=10.0
            )
            result = response.json()
        
        return {
            "success": result.get("success", True),
            "status": result.get("status", "paused"),
            "tool_name": "music_pause"
        }
        
    except Exception as e:
        logger.error(f"Error pausing music: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/music_resume")
async def music_resume(request: ToolRequest):
    """Resume playback"""
    try:
        logger.info("✅ music_resume")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/resume",
                timeout=10.0
            )
            result = response.json()
        
        return {
            "success": result.get("success", True),
            "status": result.get("status", "playing"),
            "tool_name": "music_resume"
        }
        
    except Exception as e:
        logger.error(f"Error resuming music: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/music_skip")
async def music_skip(request: ToolRequest):
    """Skip to next track"""
    try:
        logger.info("✅ music_skip")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/skip",
                timeout=10.0
            )
            result = response.json()
        
        return {
            "success": result.get("success", True),
            "status": result.get("status", "skipped"),
            "next_track": result.get("next_track"),
            "tool_name": "music_skip"
        }
        
    except Exception as e:
        logger.error(f"Error skipping track: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/music_set_volume")
async def music_set_volume(request: MusicVolumeRequest):
    """Set playback volume"""
    try:
        logger.info(f"✅ music_set_volume: volume={request.volume}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/set_volume",
                json={
                    "volume": request.volume,
                    "zone": request.zone
                },
                timeout=10.0
            )
            result = response.json()
        
        return {
            "success": result.get("success", True),
            "volume": result.get("volume", request.volume),
            "tool_name": "music_set_volume"
        }
        
    except Exception as e:
        logger.error(f"Error setting volume: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/music_get_queue")
async def music_get_queue(request: ToolRequest):
    """Get playback queue"""
    try:
        user_id = request.user_id or "default"
        logger.info(f"✅ music_get_queue: user_id={user_id}")
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{MUSIC_MODULE_URL}/tools/get_queue",
                params={"user_id": user_id},
                timeout=10.0
            )
            result = response.json()
        
        return {
            "success": result.get("success", True),
            "queue": result.get("queue", []),
            "count": result.get("count", 0),
            "tool_name": "music_get_queue"
        }
        
    except Exception as e:
        logger.error(f"Error getting queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/music_add_to_queue")
async def music_add_to_queue(request: MusicQueueRequest):
    """Add track to queue"""
    try:
        user_id = request.user_id or "default"
        logger.info(f"✅ music_add_to_queue: track_id={request.track_id}, user_id={user_id}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/add_to_queue",
                json={
                    "track_id": request.track_id,
                    "title": request.title,
                    "artist": request.artist,
                    "user_id": user_id
                },
                timeout=10.0
            )
            result = response.json()
        
        return {
            "success": result.get("success", True),
            "message": result.get("message", "Added to queue"),
            "tool_name": "music_add_to_queue"
        }
        
    except Exception as e:
        logger.error(f"Error adding to queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/music_get_recommendations")
async def music_get_recommendations(request: ToolRequest):
    """Get music recommendations"""
    try:
        user_id = request.user_id or "default"
        logger.info(f"✅ music_get_recommendations: user_id={user_id}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/get_recommendations",
                params={"user_id": user_id, "limit": 10},
                timeout=10.0
            )
            result = response.json()
        
        return {
            "success": result.get("success", True),
            "recommendations": result.get("recommendations", []),
            "count": result.get("count", 0),
            "tool_name": "music_get_recommendations"
        }
        
    except Exception as e:
        logger.error(f"Error getting recommendations: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/tools/music_get_context")
async def music_get_context(request: MusicContextRequest):
    """Get music context for conversation"""
    try:
        user_id = request.user_id or "default"
        logger.info(f"✅ music_get_context: user_id={user_id}")
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{MUSIC_MODULE_URL}/tools/get_context",
                json={"user_id": user_id},
                timeout=10.0
            )
            result = response.json()
        
        return {
            "success": result.get("success", True),
            "context": result.get("context", ""),
            "tool_name": "music_get_context"
        }
        
    except Exception as e:
        logger.error(f"Error getting music context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
