"""
Zoe Core LLM Adapter for LiveKit Voice Assistant
Connects voice conversations to Zoe's intelligence
"""

import asyncio
import logging
import httpx
from typing import Optional, AsyncIterator
from livekit.agents import llm

logger = logging.getLogger(__name__)


class ZoeCoreLLM(llm.LLM):
    """
    LLM adapter that connects LiveKit voice assistant to Zoe Core
    Routes voice conversations through Zoe's chat API
    """
    
    def __init__(
        self,
        zoe_core_url: str = "http://localhost:8000",
        user_id: Optional[str] = None,
        timeout: float = 120.0
    ):
        super().__init__()
        self.zoe_core_url = zoe_core_url
        self.user_id = user_id or "voice_user"
        self.timeout = timeout
        
        logger.info(f"Zoe Core LLM initialized for user: {self.user_id}")
    
    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        fnc_ctx: Optional[llm.FunctionContext] = None,
        temperature: Optional[float] = None,
        n: Optional[int] = None,
    ) -> "ZoeChatStream":
        """
        Start a chat completion request
        
        Args:
            chat_ctx: Chat context with message history
            fnc_ctx: Function calling context (not used)
            temperature: Temperature (not used)
            n: Number of completions (not used)
        
        Returns:
            Async stream of chat responses from Zoe
        """
        return ZoeChatStream(
            llm=self,
            chat_ctx=chat_ctx,
            zoe_core_url=self.zoe_core_url,
            user_id=self.user_id,
            timeout=self.timeout
        )


class ZoeChatStream:
    """
    Simple chat stream that calls Zoe Core
    """
    
    def __init__(
        self,
        llm: ZoeCoreLLM,
        chat_ctx: llm.ChatContext,
        zoe_core_url: str,
        user_id: str,
        timeout: float
    ):
        self._llm = llm
        self._chat_ctx = chat_ctx
        self.zoe_core_url = zoe_core_url
        self.user_id = user_id
        self.timeout = timeout
    
    def __aiter__(self) -> AsyncIterator[llm.ChatChunk]:
        """Return async iterator for streaming"""
        return self._run()
    
    async def _run(self) -> AsyncIterator[llm.ChatChunk]:
        """
        Execute chat request to Zoe Core and yield response
        """
        # Extract latest user message
        messages = self._chat_ctx.messages
        if not messages:
            yield llm.ChatChunk(
                choices=[
                    llm.Choice(
                        delta=llm.ChoiceDelta(
                            role="assistant",
                            content="I didn't catch that, could you repeat?"
                        )
                    )
                ]
            )
            return
        
        # Get user's message
        user_message = messages[-1].content if messages else ""
        
        logger.info(f"Zoe processing: '{user_message[:50]}...'")
        
        try:
            # Call Zoe Core chat API
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.zoe_core_url}/api/chat",
                    json={
                        "message": user_message,
                        "user_id": self.user_id,
                        "mode": "voice",
                        "stream": False
                    }
                )
                response.raise_for_status()
                result = response.json()
                
                # Extract Zoe's response
                zoe_response = result.get("response", "I'm having trouble thinking right now.")
                
                logger.info(f"Zoe says: '{zoe_response[:50]}...'")
                
                # Yield response as chat chunk
                yield llm.ChatChunk(
                    choices=[
                        llm.Choice(
                            delta=llm.ChoiceDelta(
                                role="assistant",
                                content=zoe_response
                            )
                        )
                    ]
                )
        
        except httpx.TimeoutException:
            logger.error("Zoe Core timeout")
            yield llm.ChatChunk(
                choices=[
                    llm.Choice(
                        delta=llm.ChoiceDelta(
                            role="assistant",
                            content="Sorry, I'm thinking too slowly."
                        )
                    )
                ]
            )
        
        except Exception as e:
            logger.error(f"Zoe Core error: {e}")
            yield llm.ChatChunk(
                choices=[
                    llm.Choice(
                        delta=llm.ChoiceDelta(
                            role="assistant",
                            content="I'm having trouble right now."
                        )
                    )
                ]
            )
    
    async def aclose(self):
        """Close resources"""
        pass
