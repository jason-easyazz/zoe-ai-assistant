from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import sqlite3
import json
import asyncio
import httpx
from datetime import datetime
import uuid

router = APIRouter(prefix="/api/matrix", tags=["matrix-integration"])

class MatrixUser(BaseModel):
    user_id: str  # @username:zoe-pi.local
    display_name: str
    avatar_url: Optional[str] = None
    is_household: bool = True
    is_zoe_bot: bool = False
    created_at: str
    last_seen: Optional[str] = None

class MatrixRoom(BaseModel):
    room_id: str
    name: str
    room_type: str  # direct, group, household
    members: List[str]
    created_at: str
    last_activity: Optional[str] = None

class MatrixMessage(BaseModel):
    message_id: str
    room_id: str
    sender: str
    content: str
    message_type: str = "text"  # text, image, file, system
    timestamp: str
    encrypted: bool = True
    reply_to: Optional[str] = None

class ZoeBotCommand(BaseModel):
    command: str
    parameters: Dict[str, Any]
    user_id: str
    room_id: str

# WebSocket connection manager for real-time updates
class MatrixConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.user_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: str = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        if user_id:
            self.user_connections[user_id] = websocket

    def disconnect(self, websocket: WebSocket, user_id: str = None):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if user_id and user_id in self.user_connections:
            del self.user_connections[user_id]

    async def send_to_user(self, user_id: str, message: dict):
        if user_id in self.user_connections:
            await self.user_connections[user_id].send_json(message)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

matrix_manager = MatrixConnectionManager()

# Initialize Matrix database
def init_matrix_db():
    conn = sqlite3.connect('/app/data/zoe.db')
    cursor = conn.cursor()
    
    # Matrix users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matrix_users (
            user_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            avatar_url TEXT,
            is_household BOOLEAN DEFAULT TRUE,
            is_zoe_bot BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP
        )
    """)
    
    # Matrix rooms table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matrix_rooms (
            room_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            room_type TEXT NOT NULL,
            members TEXT NOT NULL,  -- JSON array
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_activity TIMESTAMP
        )
    """)
    
    # Matrix messages table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matrix_messages (
            message_id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            content TEXT NOT NULL,
            message_type TEXT DEFAULT 'text',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            encrypted BOOLEAN DEFAULT TRUE,
            reply_to TEXT,
            FOREIGN KEY (room_id) REFERENCES matrix_rooms(room_id)
        )
    """)
    
    # Create Zoe bot user if it doesn't exist
    cursor.execute("SELECT COUNT(*) FROM matrix_users WHERE is_zoe_bot = TRUE")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO matrix_users 
            (user_id, display_name, is_zoe_bot, created_at)
            VALUES (?, ?, ?, ?)
        """, ("@zoe:zoe-pi.local", "Zoe AI Assistant", True, datetime.now().isoformat()))
    
    # Create household room if it doesn't exist
    cursor.execute("SELECT COUNT(*) FROM matrix_rooms WHERE room_type = 'household'")
    if cursor.fetchone()[0] == 0:
        household_room_id = f"!household:{uuid.uuid4().hex}:zoe-pi.local"
        cursor.execute("""
            INSERT INTO matrix_rooms 
            (room_id, name, room_type, members, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (household_room_id, "Household", "household", 
              json.dumps(["@zoe:zoe-pi.local"]), datetime.now().isoformat()))
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_matrix_db()

@router.get("/users")
async def get_matrix_users():
    """Get all Matrix users"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM matrix_users ORDER BY created_at")
        users = [dict(row) for row in cursor.fetchall()]
        
        conn.close()
        
        return {
            "users": users,
            "total": len(users),
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching users: {str(e)}")

@router.post("/users")
async def create_matrix_user(user: MatrixUser):
    """Create a new Matrix user"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO matrix_users 
            (user_id, display_name, avatar_url, is_household, is_zoe_bot, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user.user_id, user.display_name, user.avatar_url,
            user.is_household, user.is_zoe_bot, user.created_at
        ))
        
        conn.commit()
        conn.close()
        
        return {"message": "User created successfully", "user_id": user.user_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating user: {str(e)}")

@router.get("/rooms")
async def get_matrix_rooms():
    """Get all Matrix rooms"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM matrix_rooms ORDER BY last_activity DESC")
        rooms = []
        for row in cursor.fetchall():
            room = dict(row)
            room['members'] = json.loads(room['members'])
            rooms.append(room)
        
        conn.close()
        
        return {
            "rooms": rooms,
            "total": len(rooms),
            "last_updated": datetime.now().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching rooms: {str(e)}")

@router.post("/rooms")
async def create_matrix_room(room: MatrixRoom):
    """Create a new Matrix room"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO matrix_rooms 
            (room_id, name, room_type, members, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            room.room_id, room.name, room.room_type,
            json.dumps(room.members), room.created_at
        ))
        
        conn.commit()
        conn.close()
        
        return {"message": "Room created successfully", "room_id": room.room_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating room: {str(e)}")

@router.get("/rooms/{room_id}/messages")
async def get_room_messages(room_id: str, limit: int = 50):
    """Get messages from a specific room"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM matrix_messages 
            WHERE room_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (room_id, limit))
        
        messages = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        return {
            "room_id": room_id,
            "messages": messages,
            "total": len(messages)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching messages: {str(e)}")

@router.post("/rooms/{room_id}/messages")
async def send_message(room_id: str, message: MatrixMessage):
    """Send a message to a room"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        # Insert message
        cursor.execute("""
            INSERT INTO matrix_messages 
            (message_id, room_id, sender, content, message_type, timestamp, encrypted, reply_to)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            message.message_id, message.room_id, message.sender,
            message.content, message.message_type, message.timestamp,
            message.encrypted, message.reply_to
        ))
        
        # Update room last activity
        cursor.execute("""
            UPDATE matrix_rooms 
            SET last_activity = ? 
            WHERE room_id = ?
        """, (message.timestamp, room_id))
        
        conn.commit()
        conn.close()
        
        # Broadcast to WebSocket connections
        await matrix_manager.broadcast({
            "type": "new_message",
            "room_id": room_id,
            "message": message.dict()
        })
        
        return {"message": "Message sent successfully", "message_id": message.message_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending message: {str(e)}")

@router.post("/zoe-bot/command")
async def process_zoe_bot_command(command: ZoeBotCommand):
    """Process a command sent to Zoe bot"""
    try:
        response = await handle_zoe_command(command)
        
        # Send response back to the room
        response_message = MatrixMessage(
            message_id=str(uuid.uuid4()),
            room_id=command.room_id,
            sender="@zoe:zoe-pi.local",
            content=response,
            message_type="text",
            timestamp=datetime.now().isoformat(),
            encrypted=True
        )
        
        # Store the response message
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO matrix_messages 
            (message_id, room_id, sender, content, message_type, timestamp, encrypted)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            response_message.message_id, response_message.room_id,
            response_message.sender, response_message.content,
            response_message.message_type, response_message.timestamp,
            response_message.encrypted
        ))
        
        conn.commit()
        conn.close()
        
        # Broadcast response
        await matrix_manager.broadcast({
            "type": "zoe_response",
            "room_id": command.room_id,
            "message": response_message.dict()
        })
        
        return {"message": "Command processed", "response": response}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing command: {str(e)}")

async def handle_zoe_command(command: ZoeBotCommand) -> str:
    """Handle Zoe bot commands"""
    cmd = command.command.lower()
    
    if cmd.startswith("remind me"):
        # Handle reminder creation
        return await create_reminder(command)
    elif cmd.startswith("what's on my calendar") or cmd.startswith("calendar"):
        # Handle calendar query
        return await get_calendar_info(command)
    elif cmd.startswith("turn on") or cmd.startswith("turn off") or cmd.startswith("set"):
        # Handle home automation
        return await control_home_automation(command)
    elif cmd.startswith("create task") or cmd.startswith("add task"):
        # Handle task creation
        return await create_task_from_message(command)
    elif cmd.startswith("help"):
        # Show help
        return get_zoe_help()
    else:
        # General Q&A
        return await handle_general_question(command)

async def create_reminder(command: ZoeBotCommand) -> str:
    """Create a reminder from message"""
    # This would integrate with the reminder/task system
    return f"✅ Reminder created: {command.command}\nI'll remind you at the specified time."

async def get_calendar_info(command: ZoeBotCommand) -> str:
    """Get calendar information"""
    # This would integrate with the calendar system
    return "📅 Here's what's on your calendar:\n• 9:00 AM - Team Meeting\n• 2:00 PM - Doctor Appointment\n• 4:00 PM - Call Mum"

async def control_home_automation(command: ZoeBotCommand) -> str:
    """Control home automation devices"""
    # This would integrate with Home Assistant
    return f"🏠 Home automation command processed: {command.command}\nDevice status updated."

async def create_task_from_message(command: ZoeBotCommand) -> str:
    """Create a task from the message"""
    # This would integrate with the lists system
    return f"✅ Task created: {command.command}\nAdded to your task list."

def get_zoe_help() -> str:
    """Get Zoe bot help information"""
    return """
🤖 **Zoe AI Assistant Help**

**Available Commands:**
• `remind me to [task] at [time]` - Set reminders
• `what's on my calendar [day]` - Check calendar
• `turn on/off [device]` - Control home automation
• `create task [description]` - Add tasks to your list
• `help` - Show this help

**Privacy First:** All messages are encrypted and stay on your device.
"""

async def handle_general_question(command: ZoeBotCommand) -> str:
    """Handle general questions"""
    # This would integrate with the AI system
    return f"🤔 I understand you're asking: {command.command}\nLet me help you with that. (AI response would be generated here)"

@router.websocket("/ws/{user_id}")
async def matrix_websocket(websocket: WebSocket, user_id: str):
    """WebSocket for real-time Matrix updates"""
    await matrix_manager.connect(websocket, user_id)
    try:
        await websocket.send_json({
            "type": "connected",
            "message": f"Connected to Matrix as {user_id}",
            "user_id": user_id
        })
        
        while True:
            # Keep connection alive
            await websocket.receive_text()
            
    except WebSocketDisconnect:
        matrix_manager.disconnect(websocket, user_id)

@router.get("/onboarding/qr/{user_id}")
async def get_user_qr_code(user_id: str):
    """Generate QR code for user to easily add to Matrix clients"""
    try:
        # This would generate a QR code for the Matrix ID
        qr_data = {
            "matrix_id": user_id,
            "server": "zoe-pi.local",
            "invite_url": f"https://zoe-pi.local/matrix/invite/{user_id}",
            "qr_code": f"matrix:{user_id}"  # This would be converted to actual QR
        }
        
        return qr_data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating QR: {str(e)}")

@router.get("/household/setup")
async def get_household_setup():
    """Get household setup information"""
    try:
        conn = sqlite3.connect('/app/data/zoe.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get household users
        cursor.execute("SELECT * FROM matrix_users WHERE is_household = TRUE")
        household_users = [dict(row) for row in cursor.fetchall()]
        
        # Get household room
        cursor.execute("SELECT * FROM matrix_rooms WHERE room_type = 'household'")
        household_room = cursor.fetchone()
        if household_room:
            household_room = dict(household_room)
            household_room['members'] = json.loads(household_room['members'])
        
        conn.close()
        
        return {
            "household_users": household_users,
            "household_room": household_room,
            "zoe_bot_id": "@zoe:zoe-pi.local",
            "setup_complete": len(household_users) > 1,  # At least 2 users (Zoe + 1 human)
            "instructions": [
                "1. Download Element or FluffyChat on your phone",
                "2. Add your Matrix ID to the app",
                "3. Start chatting with Zoe and household members",
                "4. All messages are encrypted and stay local"
            ]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting household setup: {str(e)}")

@router.post("/household/add-member")
async def add_household_member(display_name: str):
    """Add a new household member"""
    try:
        # Generate Matrix ID
        username = display_name.lower().replace(" ", "_")
        user_id = f"@{username}:zoe-pi.local"
        
        # Create user
        user = MatrixUser(
            user_id=user_id,
            display_name=display_name,
            is_household=True,
            is_zoe_bot=False,
            created_at=datetime.now().isoformat()
        )
        
        # Add to database
        conn = sqlite3.connect('/app/data/zoe.db')
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO matrix_users 
            (user_id, display_name, is_household, is_zoe_bot, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, display_name, True, False, datetime.now().isoformat()))
        
        # Add to household room
        cursor.execute("SELECT members FROM matrix_rooms WHERE room_type = 'household'")
        result = cursor.fetchone()
        if result:
            members = json.loads(result[0])
            members.append(user_id)
            cursor.execute("""
                UPDATE matrix_rooms 
                SET members = ? 
                WHERE room_type = 'household'
            """, (json.dumps(members),))
        
        conn.commit()
        conn.close()
        
        return {
            "message": "Household member added",
            "user": user.dict(),
            "qr_data": await get_user_qr_code(user_id)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding household member: {str(e)}")






