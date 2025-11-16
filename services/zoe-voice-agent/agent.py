"""
Zoe Voice Agent - Real-time conversational AI using LiveKit
Simplified version that works with LiveKit agents v0.9.0 API
"""

import asyncio
import logging
import os
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.agents.voice_assistant import VoiceAssistant
from livekit.plugins import silero

# Import our custom components
from streaming_tts import NeuTTSTTS
from zoe_llm import ZoeCoreLLM
from local_whisper_stt import LocalWhisperSTT

logger = logging.getLogger("zoe-voice-agent")
logger.setLevel(logging.INFO)

# Environment configuration
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "secretsecretsecretsecretsecretsecret")
ZOE_CORE_URL = os.getenv("ZOE_CORE_URL", "http://localhost:8000")
TTS_SERVICE_URL = os.getenv("TTS_SERVICE_URL", "http://localhost:9002")
WHISPER_SERVICE_URL = os.getenv("WHISPER_SERVICE_URL", "http://localhost:9001")


async def entrypoint(ctx: JobContext):
    """
    Main entry point for voice agent
    Called when a participant joins a LiveKit room
    """
    logger.info(f"Starting voice agent for room: {ctx.room.name}")
    
    # Connect to room
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    # Wait for a participant to join
    participant = await ctx.wait_for_participant()
    
    user_id = participant.identity or "anonymous"
    logger.info(f"Participant connected: {user_id}")
    
    # Get voice profile from participant metadata
    voice_profile = "dave"  # Default to Dave's voice
    if participant.metadata:
        try:
            import json
            metadata = json.loads(participant.metadata)
            voice_profile = metadata.get("voice_profile", "dave")
        except:
            pass
    
    logger.info(f"Using voice profile: {voice_profile}")
    
    # Create voice assistant with Zoe Core intelligence & local services
    assistant = VoiceAssistant(
        vad=silero.VAD.load(),  # Voice activity detection
        stt=LocalWhisperSTT(  # ✅ Using local Whisper (free & private!)
            whisper_url=WHISPER_SERVICE_URL,
            language="en"
        ),
        llm=ZoeCoreLLM(  # ✅ Using Zoe Core for intelligence!
            zoe_core_url=ZOE_CORE_URL,
            user_id=user_id,
            timeout=120.0  # Allow Zoe time to think
        ),
        tts=NeuTTSTTS(  # ✅ Using NeuTTS Air for ultra-realistic voice!
            service_url=TTS_SERVICE_URL,
            voice_profile=voice_profile,
            user_id=user_id
        ),
    )
    
    # Add event handlers for debugging
    @assistant.on("user_started_speaking")
    def on_user_started():
        logger.info("🎤 User started speaking!")
    
    @assistant.on("user_stopped_speaking")
    def on_user_stopped():
        logger.info("🛑 User stopped speaking")
    
    @assistant.on("user_speech_committed")
    def on_user_speech(msg: str):
        logger.info(f"👤 User said: '{msg}'")
    
    @assistant.on("agent_speech_committed")
    def on_agent_speech(msg: str):
        logger.info(f"🤖 Zoe said: '{msg}'")
    
    # Start the assistant
    assistant.start(ctx.room, participant)
    
    logger.info(f"✅ Voice agent ready for room: {ctx.room.name}")
    logger.info(f"👂 Listening for {user_id}'s voice...")
    
    # The assistant runs until the participant disconnects
    # No need to sleep - LiveKit handles the lifecycle


if __name__ == "__main__":
    # Run the worker
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
            ws_url=LIVEKIT_URL,
        )
    )
