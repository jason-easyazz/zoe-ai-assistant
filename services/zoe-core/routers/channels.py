"""
Channels Router
================

Phase 4: API endpoints for channel management and webhooks.

Endpoints:
    GET  /api/channels              -- List available channels
    GET  /api/channels/bindings     -- List user's channel bindings
    POST /api/channels/link         -- Generate verification code for linking
    POST /api/channels/verify       -- Verify a code to complete binding
    DELETE /api/channels/bindings/{id} -- Remove a channel binding
    POST /api/channels/{channel}/webhook -- Webhook receiver for external channels
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from auth_integration import validate_session, AuthenticatedSession
from channels.registry import channel_registry, ChannelRegistry
from channels.web import WebChannelAdapter
from channels.whatsapp import WhatsAppChannelAdapter
from channels.telegram import TelegramChannelAdapter
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])

# Register available channel adapters
channel_registry.register(WebChannelAdapter())
channel_registry.register(WhatsAppChannelAdapter())
channel_registry.register(TelegramChannelAdapter())


class LinkRequest(BaseModel):
    channel: str  # "telegram", "whatsapp", "discord"


class VerifyRequest(BaseModel):
    channel: str
    external_id: str
    code: str


@router.get("")
async def list_channels(session: AuthenticatedSession = Depends(validate_session)):
    """List all available channel adapters."""
    return {
        "channels": channel_registry.list_channels(),
        "count": len(channel_registry.list_channels()),
    }


@router.get("/bindings")
async def list_bindings(session: AuthenticatedSession = Depends(validate_session)):
    """List user's verified channel bindings."""
    bindings = ChannelRegistry.get_bindings(session.user_id)
    return {"bindings": bindings, "count": len(bindings)}


@router.post("/link")
async def generate_link_code(
    request: LinkRequest,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Generate a 6-digit verification code for linking an external channel.

    The user sends this code to the Zoe bot on the external platform
    to complete the binding.
    """
    code = ChannelRegistry.generate_verification_code(
        user_id=session.user_id,
        channel=request.channel,
    )
    if not code:
        raise HTTPException(status_code=500, detail="Failed to generate code")

    return {
        "code": code,
        "channel": request.channel,
        "instructions": f"Send this code to the Zoe bot on {request.channel}: {code}",
        "expires_in": "10 minutes",
    }


@router.post("/verify")
async def verify_binding(request: VerifyRequest):
    """Verify a channel binding code.

    Called by the channel webhook when a user sends a verification code.
    This endpoint is public (no session required) since it's called
    from external platforms.
    """
    user_id = ChannelRegistry.verify_code(
        channel=request.channel,
        external_id=request.external_id,
        code=request.code,
    )
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    return {
        "success": True,
        "user_id": user_id,
        "channel": request.channel,
        "message": f"Channel {request.channel} linked to user {user_id}",
    }


@router.delete("/bindings/{binding_id}")
async def remove_binding(
    binding_id: int,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Remove a channel binding."""
    success = ChannelRegistry.remove_binding(session.user_id, binding_id)
    if not success:
        raise HTTPException(status_code=404, detail="Binding not found")
    return {"success": True}


@router.post("/{channel_id}/webhook")
async def channel_webhook(channel_id: str, request: Request):
    """Generic webhook receiver for external channels.

    Each channel adapter parses its own webhook format.
    The message is then routed through Trust Gate -> Chat Pipeline.
    """
    adapter = channel_registry.get(channel_id)
    if not adapter:
        raise HTTPException(status_code=404, detail=f"Unknown channel: {channel_id}")

    try:
        body = await request.json()
    except Exception:
        body = {}

    try:
        message = await adapter.receive_message(body)

        if not message.user_id:
            return {
                "status": "unbound",
                "message": "No Zoe user bound to this external identity. Send a verification code to link.",
            }

        # Route through the chat pipeline
        # In a full implementation, this would call the chat handler
        logger.info(
            f"Channel webhook: {channel_id} message from "
            f"{message.external_id} -> user {message.user_id}: "
            f"{message.content[:50]}..."
        )

        return {
            "status": "received",
            "channel": channel_id,
            "user_id": message.user_id,
            "session_key": message.session_key,
        }
    except Exception as e:
        logger.error(f"Channel webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
