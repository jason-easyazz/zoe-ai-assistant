# Add this to your services/zoe-core/main.py

import asyncio
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
import json
from typing import AsyncGenerator

# Add to your existing imports and models
class StreamingChatMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: Optional[int] = None
    user_id: str = Field(default="default")
    stream: bool = Field(default=True)

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        logger.info(f"Client {client_id} connected to chat stream")

    def disconnect(self, client_id: str):
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"Client {client_id} disconnected from chat stream")

    async def send_personal_message(self, message: str, client_id: str):
        if client_id in self.active_connections:
            await self.active_connections[client_id].send_text(message)

manager = ConnectionManager()

# Streaming Chat Endpoints

@app.websocket("/ws/chat/{client_id}")
async def websocket_chat(websocket: WebSocket, client_id: str):
    """WebSocket endpoint for real-time chat streaming"""
    await manager.connect(websocket, client_id)
    try:
        while True:
            # Wait for user message
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            # Process the message
            user_message = message_data.get("message", "")
            conversation_id = message_data.get("conversation_id")
            
            # Get AI response stream
            async for chunk in stream_ai_response(user_message, conversation_id, client_id):
                await manager.send_personal_message(
                    json.dumps({"type": "ai_chunk", "content": chunk}), 
                    client_id
                )
            
            # Send completion signal
            await manager.send_personal_message(
                json.dumps({"type": "ai_complete"}), 
                client_id
            )
            
    except WebSocketDisconnect:
        manager.disconnect(client_id)

@app.post("/api/chat/stream")
async def streaming_chat_endpoint(chat_msg: StreamingChatMessage):
    """Server-sent events streaming chat endpoint"""
    
    async def generate_response():
        """Generate streaming response"""
        yield "data: " + json.dumps({"type": "start", "message": "Zoe is thinking..."}) + "\n\n"
        
        # Get AI response stream
        full_response = ""
        async for chunk in stream_ai_response(chat_msg.message, chat_msg.conversation_id, chat_msg.user_id):
            full_response += chunk
            yield "data: " + json.dumps({
                "type": "chunk", 
                "content": chunk,
                "full_response": full_response
            }) + "\n\n"
            
            # Small delay for smooth streaming effect
            await asyncio.sleep(0.05)
        
        # Save conversation to database
        await save_conversation(chat_msg.message, full_response, chat_msg.conversation_id, chat_msg.user_id)
        
        yield "data: " + json.dumps({
            "type": "complete", 
            "full_response": full_response,
            "conversation_id": chat_msg.conversation_id
        }) + "\n\n"
    
    return StreamingResponse(
        generate_response(), 
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )

async def stream_ai_response(user_message: str, conversation_id: Optional[int], user_id: str) -> AsyncGenerator[str, None]:
    """Stream AI response from Ollama with personality and memory"""
    
    try:
        # Get conversation context
        context = await get_conversation_context(conversation_id, user_id)
        
        # Get user personality settings
        personality_prompt = await get_personality_prompt(user_message, user_id)
        
        # Build full prompt with context
        full_prompt = f"{personality_prompt}\n\nRecent conversation:\n{context}"
        
        # Get active AI model
        model = await get_setting("ai", "active_model", "llama3.2:3b")
        
        # Stream from Ollama
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                'POST',
                f"{CONFIG['ollama_url']}/api/generate",
                json={
                    "model": model,
                    "prompt": full_prompt,
                    "stream": True,
                    "options": {
                        "temperature": 0.7,
                        "max_tokens": 512,
                    }
                }
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            if "response" in data:
                                chunk = data["response"]
                                if chunk:
                                    yield chunk
                        except json.JSONDecodeError:
                            continue
                            
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        yield f"Sorry, I'm having trouble responding right now. Error: {str(e)}"

async def get_conversation_context(conversation_id: Optional[int], user_id: str, limit: int = 10) -> str:
    """Get recent conversation history for context"""
    if not conversation_id:
        return ""
    
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        cursor = await db.execute("""
            SELECT role, content FROM messages 
            WHERE conversation_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (conversation_id, limit))
        
        messages = await cursor.fetchall()
        
        if not messages:
            return ""
        
        # Format messages for context (reverse to chronological order)
        context_lines = []
        for role, content in reversed(messages):
            if role == "user":
                context_lines.append(f"User: {content}")
            else:
                context_lines.append(f"Zoe: {content}")
        
        return "\n".join(context_lines[-6:])  # Last 6 exchanges

async def save_conversation(user_message: str, ai_response: str, conversation_id: Optional[int], user_id: str) -> int:
    """Save conversation to database"""
    async with aiosqlite.connect(CONFIG["database_path"]) as db:
        # Create conversation if needed
        if not conversation_id:
            cursor = await db.execute("""
                INSERT INTO conversations (title, user_id, created_at)
                VALUES (?, ?, ?)
            """, (f"Chat {datetime.now().strftime('%m/%d %H:%M')}", user_id, datetime.now()))
            await db.commit()
            conversation_id = cursor.lastrowid
        
        # Save user message
        await db.execute("""
            INSERT INTO messages (conversation_id, role, content, timestamp)
            VALUES (?, ?, ?, ?)
        """, (conversation_id, "user", user_message, datetime.now()))
        
        # Save AI response
        await db.execute("""
            INSERT INTO messages (conversation_id, role, content, timestamp)
            VALUES (?, ?, ?, ?)
        """, (conversation_id, "assistant", ai_response, datetime.now()))
        
        await db.commit()
        return conversation_id

# Update your existing chat endpoint to support both streaming and non-streaming
@app.post("/api/chat")
async def chat_endpoint(chat_msg: ChatMessage, background_tasks: BackgroundTasks):
    """Enhanced chat endpoint with streaming support"""
    
    # If streaming is requested, redirect to streaming endpoint
    if hasattr(chat_msg, 'stream') and chat_msg.stream:
        return {"redirect": "/api/chat/stream"}
    
    # Non-streaming response (for compatibility)
    try:
        full_response = ""
        async for chunk in stream_ai_response(chat_msg.message, chat_msg.conversation_id, chat_msg.user_id):
            full_response += chunk
        
        # Save conversation
        conversation_id = await save_conversation(
            chat_msg.message, 
            full_response, 
            chat_msg.conversation_id, 
            chat_msg.user_id
        )
        
        # Extract entities in background
        background_tasks.add_task(extract_entities_from_message, chat_msg.message, chat_msg.user_id)
        
        return {
            "response": full_response,
            "conversation_id": conversation_id,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=f"Chat processing failed: {str(e)}")

# Entity extraction function (stub for now)
async def extract_entities_from_message(message: str, user_id: str):
    """Extract tasks, events, reminders from user message"""
    # TODO: Implement smart entity extraction
    # This will analyze the message and auto-create tasks/events
    logger.info(f"Extracting entities from: {message}")
    pass