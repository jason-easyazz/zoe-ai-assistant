"""
Voice Router
============

WebSocket and HTTP endpoints for voice control integration.
Handles wake word events, audio ducking, and barge-in.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from typing import Dict, List, Optional
import asyncio
import logging
import json
from datetime import datetime

from services.voice import (
    get_wake_word_detector,
    get_audio_ducker
)
from services.voice.barge_in import (
    get_barge_in_controller,
    InterruptType,
    ZoeState
)
from auth_integration import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

# Connected WebSocket clients
_voice_clients: Dict[str, WebSocket] = {}


# ========================================
# WebSocket Connection
# ========================================

@router.websocket("/ws")
async def voice_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for real-time voice control.
    
    Messages from client:
        - {"type": "wake_word", "keyword": "hey zoe", "confidence": 0.95}
        - {"type": "vad", "active": true, "confidence": 0.8}
        - {"type": "interrupt", "source": "button"}
        - {"type": "state_request"}
    
    Messages to client:
        - {"type": "ducking", "state": "ducked", "level": 0.2}
        - {"type": "state", "zoe_state": "listening", "music_state": {...}}
        - {"type": "interrupt_ack", "success": true}
    """
    await websocket.accept()
    
    # Get session ID from query params or cookies
    session_id = websocket.query_params.get("session_id")
    if not session_id:
        session_id = f"voice_{id(websocket)}"
    
    _voice_clients[session_id] = websocket
    logger.info(f"Voice WebSocket connected: {session_id}")
    
    # Get service instances
    wake_word = get_wake_word_detector()
    audio_ducker = get_audio_ducker()
    barge_in = get_barge_in_controller()
    
    # Set up ducking callback to broadcast to client
    def on_duck_change(state, level):
        asyncio.create_task(_send_ducking_update(websocket, state.value, level))
    
    audio_ducker.on_duck_change(on_duck_change)
    
    # Set up interrupt callback
    def on_interrupt(event):
        asyncio.create_task(_send_interrupt_event(websocket, event))
    
    barge_in.on_interrupt(on_interrupt)
    
    try:
        while True:
            message = await websocket.receive_text()
            await _handle_voice_message(websocket, session_id, message)
            
    except WebSocketDisconnect:
        logger.info(f"Voice WebSocket disconnected: {session_id}")
    except Exception as e:
        logger.error(f"Voice WebSocket error: {e}")
    finally:
        if session_id in _voice_clients:
            del _voice_clients[session_id]


async def _handle_voice_message(
    websocket: WebSocket,
    session_id: str,
    message: str
) -> None:
    """Handle incoming voice WebSocket message."""
    try:
        data = json.loads(message)
        msg_type = data.get("type")
        
        if msg_type == "wake_word":
            await _handle_wake_word(websocket, data)
        
        elif msg_type == "vad":
            await _handle_vad(websocket, data)
        
        elif msg_type == "interrupt":
            await _handle_interrupt(websocket, data)
        
        elif msg_type == "state_request":
            await _send_state(websocket)
        
        elif msg_type == "ping":
            await websocket.send_json({"type": "pong"})
        
        else:
            logger.warning(f"Unknown voice message type: {msg_type}")
            
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in voice message: {message}")
    except Exception as e:
        logger.error(f"Error handling voice message: {e}")


async def _handle_wake_word(websocket: WebSocket, data: dict) -> None:
    """Handle wake word detection from browser."""
    wake_word = get_wake_word_detector()
    
    # Process the detection
    await wake_word.handle_browser_detection({
        "keyword": data.get("keyword", "hey zoe"),
        "confidence": data.get("confidence", 1.0)
    })
    
    # Acknowledge
    await websocket.send_json({
        "type": "wake_word_ack",
        "success": True,
        "keyword": data.get("keyword")
    })


async def _handle_vad(websocket: WebSocket, data: dict) -> None:
    """Handle voice activity detection from browser."""
    barge_in = get_barge_in_controller()
    
    await barge_in.handle_voice_activity(
        is_active=data.get("active", False),
        confidence=data.get("confidence", 1.0)
    )


async def _handle_interrupt(websocket: WebSocket, data: dict) -> None:
    """Handle manual interrupt request."""
    barge_in = get_barge_in_controller()
    
    source = data.get("source", "manual")
    interrupt_type = InterruptType.BUTTON if source == "button" else InterruptType.MANUAL
    
    await barge_in.trigger_interrupt(interrupt_type)
    
    await websocket.send_json({
        "type": "interrupt_ack",
        "success": True,
        "source": source
    })


async def _send_ducking_update(websocket: WebSocket, state: str, level: float) -> None:
    """Send ducking state update to client."""
    try:
        await websocket.send_json({
            "type": "ducking",
            "state": state,
            "level": level,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Failed to send ducking update: {e}")


async def _send_interrupt_event(websocket: WebSocket, event) -> None:
    """Send interrupt event to client."""
    try:
        await websocket.send_json({
            "type": "interrupt",
            "interrupt_type": event.interrupt_type.value,
            "previous_state": event.previous_state.value,
            "confidence": event.confidence,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Failed to send interrupt event: {e}")


async def _send_state(websocket: WebSocket) -> None:
    """Send current voice state to client."""
    barge_in = get_barge_in_controller()
    audio_ducker = get_audio_ducker()
    
    await websocket.send_json({
        "type": "state",
        "barge_in": barge_in.get_state(),
        "ducking": audio_ducker.get_state(),
        "timestamp": datetime.now().isoformat()
    })


# ========================================
# HTTP Endpoints
# ========================================

@router.get("/config")
async def get_voice_config():
    """Get voice configuration for browser-side detection."""
    wake_word = get_wake_word_detector()
    
    return {
        "wake_word": wake_word.get_browser_config(),
        "vad": {
            "threshold": 0.5,
            "min_speech_ms": 100,
            "max_silence_ms": 1500
        },
        "ducking": {
            "level": 0.2,
            "fade_ms": 200
        }
    }


@router.get("/state")
async def get_voice_state(user_id: str = Depends(get_current_user)):
    """Get current voice control state."""
    barge_in = get_barge_in_controller()
    audio_ducker = get_audio_ducker()
    
    return {
        "barge_in": barge_in.get_state(),
        "ducking": audio_ducker.get_state()
    }


@router.post("/interrupt")
async def trigger_interrupt(
    source: str = "api",
    user_id: str = Depends(get_current_user)
):
    """Manually trigger an interrupt."""
    barge_in = get_barge_in_controller()
    
    interrupt_type = InterruptType.BUTTON if source == "button" else InterruptType.MANUAL
    await barge_in.trigger_interrupt(interrupt_type)
    
    return {
        "success": True,
        "source": source,
        "previous_state": barge_in.state.value
    }


@router.post("/duck")
async def start_ducking(
    reason: str = "api",
    user_id: str = Depends(get_current_user)
):
    """Start audio ducking."""
    audio_ducker = get_audio_ducker()
    await audio_ducker.duck(reason)
    
    return audio_ducker.get_state()


@router.post("/unduck")
async def stop_ducking(
    immediate: bool = False,
    user_id: str = Depends(get_current_user)
):
    """Stop audio ducking."""
    audio_ducker = get_audio_ducker()
    
    if immediate:
        await audio_ducker.unduck_immediate()
    else:
        await audio_ducker.unduck()
    
    return audio_ducker.get_state()


@router.post("/set-state")
async def set_zoe_state(
    state: str,
    user_id: str = Depends(get_current_user)
):
    """
    Set Zoe's current activity state.
    
    States: idle, listening, processing, speaking, playing
    """
    barge_in = get_barge_in_controller()
    
    try:
        zoe_state = ZoeState(state)
        barge_in.set_state(zoe_state)
        return {"success": True, "state": state}
    except ValueError:
        raise HTTPException(400, f"Invalid state: {state}")


# ========================================
# Broadcast Helper
# ========================================

async def broadcast_to_voice_clients(message: dict) -> None:
    """Broadcast message to all connected voice clients."""
    disconnected = []
    
    for session_id, websocket in _voice_clients.items():
        try:
            await websocket.send_json(message)
        except Exception:
            disconnected.append(session_id)
    
    # Clean up disconnected clients
    for session_id in disconnected:
        del _voice_clients[session_id]

