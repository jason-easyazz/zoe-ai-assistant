"""
Conversation Store - Persist voice conversations to Zoe's temporal memory
Links LiveKit room sessions to Zoe's episode tracking system
"""

import asyncio
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
import httpx
import os

logger = logging.getLogger(__name__)

ZOE_CORE_URL = os.getenv("ZOE_CORE_URL", "http://localhost:8000")


class ConversationStore:
    """
    Stores voice conversation transcripts and links them to Zoe's temporal memory
    """
    
    def __init__(self, user_id: str, room_name: str):
        self.user_id = user_id
        self.room_name = room_name
        self.episode_id: Optional[str] = None
        self.messages: List[Dict[str, Any]] = []
        self._client = httpx.AsyncClient(timeout=30.0)
        self._initialized = False
    
    async def initialize(self):
        """Initialize conversation tracking with temporal memory"""
        if self._initialized:
            return
        
        try:
            # Create or get active episode for voice conversation
            response = await self._client.post(
                f"{ZOE_CORE_URL}/api/temporal-memory/episodes",
                json={
                    "user_id": self.user_id,
                    "mode": "voice",
                    "context": {
                        "type": "voice_conversation",
                        "room": self.room_name,
                        "started_at": datetime.now().isoformat()
                    }
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                self.episode_id = result.get("episode_id")
                logger.info(f"Created episode {self.episode_id} for voice conversation")
            else:
                logger.warning(f"Failed to create episode: {response.status_code}")
        
        except Exception as e:
            logger.error(f"Failed to initialize conversation store: {e}")
        
        self._initialized = True
    
    async def add_message(self, role: str, content: str):
        """
        Add a message to the conversation
        
        Args:
            role: "user" or "assistant"
            content: Message content (transcribed text)
        """
        # Ensure initialized
        if not self._initialized:
            await self.initialize()
        
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        
        self.messages.append(message)
        
        # Persist to temporal memory
        if self.episode_id:
            try:
                await self._client.post(
                    f"{ZOE_CORE_URL}/api/temporal-memory/episodes/{self.episode_id}/messages",
                    json={
                        "user_id": self.user_id,
                        "role": role,
                        "content": content,
                        "metadata": {
                            "source": "voice",
                            "room": self.room_name
                        }
                    }
                )
            except Exception as e:
                logger.error(f"Failed to persist message to temporal memory: {e}")
    
    async def finalize(self):
        """Finalize the conversation and close the episode"""
        if not self.episode_id:
            return
        
        try:
            # Close the episode
            await self._client.post(
                f"{ZOE_CORE_URL}/api/temporal-memory/episodes/{self.episode_id}/close",
                json={
                    "user_id": self.user_id,
                    "summary": self._generate_summary(),
                    "metadata": {
                        "message_count": len(self.messages),
                        "room": self.room_name,
                        "ended_at": datetime.now().isoformat()
                    }
                }
            )
            logger.info(f"Closed episode {self.episode_id}")
        
        except Exception as e:
            logger.error(f"Failed to finalize conversation: {e}")
        
        finally:
            await self._client.aclose()
    
    def _generate_summary(self) -> str:
        """Generate a brief summary of the conversation"""
        if not self.messages:
            return "No messages exchanged"
        
        user_messages = [m for m in self.messages if m["role"] == "user"]
        assistant_messages = [m for m in self.messages if m["role"] == "assistant"]
        
        return f"Voice conversation with {len(user_messages)} user messages and {len(assistant_messages)} assistant responses"
    
    def get_transcript(self) -> List[Dict[str, Any]]:
        """Get the full conversation transcript"""
        return self.messages.copy()


class ConversationMetrics:
    """
    Track conversation metrics for quality monitoring
    """
    
    def __init__(self):
        self.total_conversations = 0
        self.total_messages = 0
        self.total_duration = 0.0
        self.average_latency = 0.0
        self._client = httpx.AsyncClient(timeout=10.0)
    
    async def record_conversation(
        self,
        user_id: str,
        room_name: str,
        message_count: int,
        duration_seconds: float,
        average_latency_ms: float
    ):
        """Record conversation metrics to user satisfaction system"""
        try:
            await self._client.post(
                f"{ZOE_CORE_URL}/api/satisfaction/interaction",
                json={
                    "user_id": user_id,
                    "interaction_type": "voice_conversation",
                    "request": f"Voice conversation in {room_name}",
                    "response": f"{message_count} messages exchanged",
                    "response_time": duration_seconds,
                    "metadata": {
                        "message_count": message_count,
                        "average_latency_ms": average_latency_ms,
                        "room": room_name
                    }
                }
            )
        except Exception as e:
            logger.error(f"Failed to record conversation metrics: {e}")
    
    async def aclose(self):
        """Close HTTP client"""
        await self._client.aclose()












