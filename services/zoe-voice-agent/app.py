"""
Zoe Voice Agent HTTP Server
Provides health checks, stats, and management endpoints
The actual voice agent runs separately via agent.py
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, List
import logging
import os

logger = logging.getLogger(__name__)

app = FastAPI(title="Zoe Voice Agent Service", version="1.0.0")

# Service status
service_status = {
    "started_at": None,
    "active_conversations": 0,
    "total_conversations": 0,
    "livekit_connected": False
}


@app.on_event("startup")
async def startup_event():
    """Initialize service"""
    from datetime import datetime
    service_status["started_at"] = datetime.now().isoformat()
    logger.info("Zoe Voice Agent HTTP server started")


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "zoe-voice-agent",
        "livekit_url": os.getenv("LIVEKIT_URL", "ws://localhost:7880"),
        "started_at": service_status["started_at"]
    }


@app.get("/status")
async def get_status():
    """Get service status and statistics"""
    return {
        "service": "Zoe Voice Agent",
        "version": "1.0.0",
        "status": service_status,
        "environment": {
            "livekit_url": os.getenv("LIVEKIT_URL"),
            "zoe_core_url": os.getenv("ZOE_CORE_URL"),
            "tts_service_url": os.getenv("TTS_SERVICE_URL"),
            "whisper_service_url": os.getenv("WHISPER_SERVICE_URL")
        }
    }


@app.get("/")
async def root():
    """Service information"""
    return {
        "service": "Zoe Voice Agent",
        "description": "Real-time voice conversations with Zoe using LiveKit",
        "version": "1.0.0",
        "features": [
            "Ultra-low latency voice conversations (<200ms)",
            "Natural interruption handling",
            "Multi-user/multi-device support",
            "Voice cloning with NeuTTS Air",
            "Conversation persistence to temporal memory",
            "WebRTC-based streaming"
        ],
        "endpoints": {
            "GET /health": "Health check",
            "GET /status": "Service status and stats",
            "GET /": "Service information"
        },
        "documentation": "See /docs for API documentation"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9003)












