"""
Zoe Matrix Integration Service
Secure messaging bridge for Zoe AI Assistant
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Try to import Matrix client
try:
    from nio import AsyncClient, MatrixRoom, RoomMessageText
    MATRIX_AVAILABLE = True
except ImportError:
    MATRIX_AVAILABLE = False
    logging.warning("Matrix client not available")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe Matrix Service", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
CONFIG_FILE = "/app/data/matrix_config.json"
STORE_PATH = "/app/data/matrix_store"

class MatrixConfig(BaseModel):
    homeserver: str = "https://matrix.org"
    username: str = ""
    password: str = ""
    device_name: str = "Zoe AI Assistant"
    enabled: bool = False

class MessageRequest(BaseModel):
    room_id: str
    message: str
    message_type: str = "text"

class MatrixManager:
    def __init__(self):
        self.client: Optional[AsyncClient] = None
        self.config: Optional[MatrixConfig] = None
        self.connected = False
        self.rooms: Dict[str, str] = {}  # room_id -> room_name
        
    async def load_config(self):
        """Load Matrix configuration"""
        try:
            if Path(CONFIG_FILE).exists():
                with open(CONFIG_FILE, 'r') as f:
                    config_data = json.load(f)
                    self.config = MatrixConfig(**config_data)
            else:
                self.config = MatrixConfig()
                await self.save_config()
        except Exception as e:
            logger.error(f"Failed to load Matrix config: {e}")
            self.config = MatrixConfig()
    
    async def save_config(self):
        """Save Matrix configuration"""
        try:
            Path(CONFIG_FILE).parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config.dict(), f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save Matrix config: {e}")
    
    async def connect(self):
        """Connect to Matrix homeserver"""
        if not MATRIX_AVAILABLE:
            logger.warning("Matrix client not available")
            return False
            
        if not self.config or not self.config.enabled:
            logger.info("Matrix not configured or disabled")
            return False
            
        try:
            self.client = AsyncClient(
                self.config.homeserver,
                self.config.username,
                store_path=STORE_PATH
            )
            
            # Set up callbacks
            self.client.add_event_callback(self.message_callback, RoomMessageText)
            
            # Login
            response = await self.client.login(
                password=self.config.password,
                device_name=self.config.device_name
            )
            
            if hasattr(response, 'access_token'):
                logger.info("✅ Connected to Matrix successfully")
                self.connected = True
                
                # Sync to get room list
                await self.client.sync()
                
                # Update rooms list
                for room_id, room in self.client.rooms.items():
                    self.rooms[room_id] = room.display_name or room_id
                
                return True
            else:
                logger.error(f"Matrix login failed: {response}")
                return False
                
        except Exception as e:
            logger.error(f"Matrix connection failed: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from Matrix"""
        if self.client and self.connected:
            await self.client.logout()
            await self.client.close()
            self.connected = False
            logger.info("Disconnected from Matrix")
    
    async def message_callback(self, room: MatrixRoom, event: RoomMessageText):
        """Handle incoming Matrix messages"""
        if event.sender == self.client.user_id:
            return  # Ignore our own messages
            
        logger.info(f"Received message from {event.sender} in {room.display_name}: {event.body}")
        
        # Forward to Zoe for processing
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "http://zoe-core:8000/api/chat",
                    json={
                        "message": event.body,
                        "source": "matrix",
                        "room_id": room.room_id,
                        "sender": event.sender
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    # Send Zoe's response back to Matrix
                    await self.send_message(room.room_id, data.get("response", "I'm having trouble responding right now."))
                    
        except Exception as e:
            logger.error(f"Failed to process Matrix message: {e}")
    
    async def send_message(self, room_id: str, message: str):
        """Send message to Matrix room"""
        if not self.client or not self.connected:
            raise Exception("Not connected to Matrix")
            
        try:
            await self.client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content={
                    "msgtype": "m.text",
                    "body": message
                }
            )
            logger.info(f"Sent message to {room_id}: {message[:50]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to send Matrix message: {e}")
            raise
    
    async def get_rooms(self):
        """Get list of joined rooms"""
        if not self.connected:
            return {}
        return self.rooms
    
    async def join_room(self, room_identifier: str):
        """Join a Matrix room"""
        if not self.client or not self.connected:
            raise Exception("Not connected to Matrix")
            
        try:
            response = await self.client.join(room_identifier)
            if hasattr(response, 'room_id'):
                room_id = response.room_id
                self.rooms[room_id] = room_identifier
                logger.info(f"Joined room: {room_identifier}")
                return room_id
            else:
                raise Exception(f"Failed to join room: {response}")
        except Exception as e:
            logger.error(f"Failed to join room {room_identifier}: {e}")
            raise

# Global Matrix manager
matrix_manager = MatrixManager()

@app.on_event("startup")
async def startup():
    await matrix_manager.load_config()
    if matrix_manager.config and matrix_manager.config.enabled:
        await matrix_manager.connect()

@app.on_event("shutdown")
async def shutdown():
    await matrix_manager.disconnect()

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "matrix_available": MATRIX_AVAILABLE,
        "connected": matrix_manager.connected,
        "rooms_count": len(matrix_manager.rooms),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/config")
async def get_config():
    """Get Matrix configuration (without password)"""
    if matrix_manager.config:
        config_dict = matrix_manager.config.dict()
        config_dict.pop("password", None)  # Don't expose password
        return config_dict
    return MatrixConfig().dict()

@app.put("/api/config")
async def update_config(config: MatrixConfig):
    """Update Matrix configuration"""
    matrix_manager.config = config
    await matrix_manager.save_config()
    
    # Reconnect if enabled
    if config.enabled:
        await matrix_manager.disconnect()
        success = await matrix_manager.connect()
        return {"success": success, "message": "Configuration updated and connection attempted"}
    else:
        await matrix_manager.disconnect()
        return {"success": True, "message": "Configuration updated and disconnected"}

@app.get("/api/rooms")
async def get_rooms():
    """Get list of joined rooms"""
    rooms = await matrix_manager.get_rooms()
    return {"rooms": rooms, "connected": matrix_manager.connected}

@app.post("/api/rooms/join")
async def join_room(room_data: dict):
    """Join a Matrix room"""
    room_identifier = room_data.get("room_identifier")
    if not room_identifier:
        raise HTTPException(status_code=400, detail="room_identifier required")
    
    try:
        room_id = await matrix_manager.join_room(room_identifier)
        return {"success": True, "room_id": room_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/send")
async def send_message(message_req: MessageRequest):
    """Send message to Matrix room"""
    try:
        await matrix_manager.send_message(message_req.room_id, message_req.message)
        return {"success": True, "message": "Message sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/connect")
async def connect_matrix():
    """Manual connection attempt"""
    try:
        success = await matrix_manager.connect()
        return {"success": success, "connected": matrix_manager.connected}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/disconnect")
async def disconnect_matrix():
    """Disconnect from Matrix"""
    try:
        await matrix_manager.disconnect()
        return {"success": True, "connected": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9003)
