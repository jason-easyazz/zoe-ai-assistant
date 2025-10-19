"""
Voice Agent Router - LiveKit room management and access tokens
Handles real-time voice conversation setup
"""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging
import os
import time
from livekit import api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["Voice Agent"])

# LiveKit configuration
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secretsecretsecretsecretsecretsecret")

# External LiveKit URL for browser clients (use Pi's hostname or IP)
# Will detect from request headers
def get_external_livekit_url(request_host: str, is_https: bool = False) -> str:
    """Get external LiveKit URL based on request origin"""
    # Extract hostname from request (e.g., "zoe.local" or "192.168.1.x")
    
    if request_host:
        host = request_host.split(':')[0]  # Remove port if present
        
        if is_https:
            # HTTPS: Use nginx WSS proxy (port 443, path /livekit/)
            return f"wss://{host}/livekit"
        else:
            # HTTP: Direct WebSocket connection
            return f"ws://{host}:7880"
    
    return "ws://localhost:7880"


class StartConversationRequest(BaseModel):
    user_id: str
    voice_profile: str = "default"
    room_name: Optional[str] = None


class ConversationToken(BaseModel):
    token: str
    room_name: str
    livekit_url: str
    user_id: str
    expires_at: int


@router.post("/start-conversation")
async def start_conversation(request: StartConversationRequest, http_request: Request) -> ConversationToken:
    """
    Start a new voice conversation
    
    Creates a LiveKit room and generates access token for the user
    
    - **user_id**: User identifier
    - **voice_profile**: Voice profile to use for TTS (default, dave, jo, zoe, or custom)
    - **room_name**: Optional room name (auto-generated if not provided)
    
    Returns access token and room details
    """
    try:
        # Generate room name if not provided
        room_name = request.room_name or f"zoe-voice-{request.user_id}-{int(time.time())}"
        
        # Create access token
        token = api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        token.with_identity(request.user_id)
        token.with_name(request.user_id)
        token.with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True
            )
        )
        
        # Add voice profile metadata
        token.with_metadata(f'{{"voice_profile": "{request.voice_profile}"}}')
        
        # Token expires in 1 hour
        expires_at = int(time.time()) + 3600
        
        jwt_token = token.to_jwt()
        
        # Detect if request came over HTTPS
        is_https = (
            http_request.headers.get("x-forwarded-proto") == "https" or
            http_request.url.scheme == "https"
        )
        
        # Get external LiveKit URL for browser clients
        external_url = get_external_livekit_url(
            http_request.headers.get("host", ""),
            is_https=is_https
        )
        
        logger.info(f"Created voice conversation for user {request.user_id} in room {room_name}")
        logger.info(f"LiveKit URL: {external_url} (HTTPS detected: {is_https})")
        
        return ConversationToken(
            token=jwt_token,
            room_name=room_name,
            livekit_url=external_url,
            user_id=request.user_id,
            expires_at=expires_at
        )
    
    except Exception as e:
        logger.error(f"Failed to start conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rooms")
async def list_rooms():
    """
    List active voice conversation rooms
    
    Shows currently active LiveKit rooms
    """
    try:
        # Create LiveKit API client
        lk_api = api.LiveKitAPI(
            url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET
        )
        
        # List rooms
        rooms = await lk_api.room.list_rooms(api.ListRoomsRequest())
        
        return {
            "rooms": [
                {
                    "name": room.name,
                    "sid": room.sid,
                    "num_participants": room.num_participants,
                    "created_at": room.creation_time,
                    "empty_timeout": room.empty_timeout
                }
                for room in rooms
            ],
            "total": len(rooms)
        }
    
    except Exception as e:
        logger.error(f"Failed to list rooms: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rooms/{room_name}")
async def get_room(room_name: str):
    """Get details of a specific room"""
    try:
        lk_api = api.LiveKitAPI(
            url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET
        )
        
        # Get room details
        rooms = await lk_api.room.list_rooms(api.ListRoomsRequest())
        room = next((r for r in rooms if r.name == room_name), None)
        
        if not room:
            raise HTTPException(status_code=404, detail=f"Room '{room_name}' not found")
        
        # Get participants
        participants = await lk_api.room.list_participants(
            api.ListParticipantsRequest(room=room_name)
        )
        
        return {
            "name": room.name,
            "sid": room.sid,
            "num_participants": room.num_participants,
            "created_at": room.creation_time,
            "participants": [
                {
                    "identity": p.identity,
                    "name": p.name,
                    "sid": p.sid,
                    "state": p.state
                }
                for p in participants
            ]
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get room: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/rooms/{room_name}")
async def delete_room(room_name: str):
    """
    Delete a voice conversation room
    
    Ends the conversation and removes all participants
    """
    try:
        lk_api = api.LiveKitAPI(
            url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET
        )
        
        await lk_api.room.delete_room(api.DeleteRoomRequest(room=room_name))
        
        logger.info(f"Deleted room: {room_name}")
        
        return {
            "success": True,
            "message": f"Room '{room_name}' deleted successfully"
        }
    
    except Exception as e:
        logger.error(f"Failed to delete room: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def voice_agent_status():
    """Get voice agent service status"""
    import httpx
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Check voice agent service
            agent_response = await client.get("http://zoe-voice-agent:9003/status")
            agent_status = agent_response.json() if agent_response.status_code == 200 else None
            
            # Check LiveKit server
            livekit_response = await client.get(f"{LIVEKIT_URL.replace('ws://', 'http://')}/")
            livekit_healthy = livekit_response.status_code == 200
            
            return {
                "voice_agent": agent_status,
                "livekit_server": {
                    "url": LIVEKIT_URL,
                    "healthy": livekit_healthy
                },
                "features": {
                    "real_time_conversations": True,
                    "voice_cloning": True,
                    "multi_user": True,
                    "interruption_handling": True,
                    "streaming_tts": True
                }
            }
    
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return {
            "error": str(e),
            "voice_agent": None,
            "livekit_server": {
                "url": LIVEKIT_URL,
                "healthy": False
            }
        }


@router.get("/")
async def voice_info():
    """Voice agent service information"""
    return {
        "service": "Zoe Voice Agent",
        "description": "Real-time voice conversations with ultra-low latency",
        "version": "1.0.0",
        "features": [
            "WebRTC-based real-time voice",
            "Natural interruption handling",
            "Multi-user conversations",
            "Voice cloning with NeuTTS Air",
            "Conversation persistence",
            "Mobile app ready"
        ],
        "endpoints": {
            "POST /start-conversation": "Start new voice conversation",
            "GET /rooms": "List active conversations",
            "GET /rooms/{name}": "Get room details",
            "DELETE /rooms/{name}": "End conversation",
            "GET /status": "Service status"
        },
        "livekit_url": LIVEKIT_URL
    }







