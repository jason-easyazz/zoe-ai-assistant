"""
Music Router - REMOVED (Phase 6 Cleanup)
==========================================

Music functionality is handled by Music Assistant in Home Assistant.
The zoe-music module has been removed from enabled modules.
Music intents should be routed to HA via the MCP bridge.

If you need music playback:
    - Use Home Assistant's Music Assistant integration
    - Music intents are handled by the intent system (hassil_classifier)
    - The HA router provides device and service control

This file is kept as a stub to prevent import errors from the auto-router-loader.
"""

from fastapi import APIRouter
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/music", tags=["music-deprecated"])

logger.info("Music router loaded (stub only -- music handled by HA Music Assistant)")


@router.get("/status")
async def music_status():
    """Music service status -- redirects to HA."""
    return {
        "status": "removed",
        "message": "Music is now handled by Music Assistant in Home Assistant. Use the HA integration instead.",
        "migration": "Phase 6 cleanup - zoe-music module removed, use HA Music Assistant"
    }
