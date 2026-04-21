"""
Channels Router
================

Phase 4: API endpoints for channel management, setup, and webhooks.

Endpoints:
    GET  /api/channels                      -- List available channels (with config status)
    GET  /api/channels/bindings             -- List user's channel bindings
    POST /api/channels/link                 -- Generate verification code for linking
    POST /api/channels/verify               -- Verify a code to complete binding
    DELETE /api/channels/bindings/{id}      -- Remove a channel binding
    POST /api/channels/{channel}/auto-setup -- Automated setup via Agent Zero
    POST /api/channels/{channel}/configure  -- Manual credential configuration
    GET  /api/channels/{channel}/status     -- Check setup status
    POST /api/channels/{channel}/test       -- Test connection
    DELETE /api/channels/{channel}/config   -- Disconnect channel
    POST /api/channels/{channel}/qr-code   -- Generate linking QR code (push to touch panel)
    POST /api/channels/{channel}/webhook    -- Webhook receiver for external channels
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Optional, Dict, Any
from auth_integration import validate_session, AuthenticatedSession
from channels.registry import channel_registry, ChannelRegistry
from channels.web import WebChannelAdapter
from channels.whatsapp import WhatsAppChannelAdapter
from channels.telegram import TelegramChannelAdapter
from channels.discord import DiscordChannelAdapter
from channels.setup_orchestrator import channel_orchestrator, get_all_channel_configs
from channels.touch_panel_push import push_qr_to_touch_panel
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])

# Register available channel adapters
channel_registry.register(WebChannelAdapter())
channel_registry.register(WhatsAppChannelAdapter())
channel_registry.register(TelegramChannelAdapter())
channel_registry.register(DiscordChannelAdapter())


# ---- Request Models ----

class LinkRequest(BaseModel):
    channel: str

class VerifyRequest(BaseModel):
    channel: str
    external_id: str
    code: str

class AutoSetupRequest(BaseModel):
    bot_name: str = "Zoe"

class ConfigureRequest(BaseModel):
    credentials: Dict[str, str]


# ---- Channel Listing ----

@router.get("")
async def list_channels(session: AuthenticatedSession = Depends(validate_session)):
    """List all available channel adapters with configuration status."""
    channels = channel_registry.list_channels()
    configs = {c["channel"]: c for c in get_all_channel_configs()}

    enriched = []
    for ch in channels:
        ch_id = ch["id"]
        config = configs.get(ch_id, {})
        enriched.append({
            "id": ch_id,
            "label": ch["label"],
            "configured": bool(config),
            "status": config.get("status", "not_configured"),
            "bot_username": config.get("bot_username", ""),
            "last_error": config.get("last_error"),
        })

    return {"channels": enriched, "count": len(enriched)}


# ---- Channel Bindings ----

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
    """Generate a 6-digit verification code for linking an external channel."""
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
    """Verify a channel binding code (public endpoint for external platforms)."""
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


# ---- Channel Setup ----

@router.post("/{channel_id}/auto-setup")
async def auto_setup_channel(
    channel_id: str,
    request: AutoSetupRequest,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Automated channel setup via Agent Zero browser automation."""
    result = await channel_orchestrator.auto_setup(
        channel=channel_id,
        bot_name=request.bot_name,
        user_id=session.user_id,
    )
    return {
        "success": result.success,
        "channel": result.channel,
        "method": result.method,
        "message": result.message,
        "credentials": result.credentials,
        "next_steps": result.next_steps,
        "error": result.error,
    }


@router.post("/{channel_id}/configure")
async def configure_channel(
    channel_id: str,
    request: ConfigureRequest,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Manually configure channel with provided credentials."""
    result = await channel_orchestrator.manual_configure(
        channel=channel_id,
        credentials=request.credentials,
    )
    return {
        "success": result.success,
        "channel": result.channel,
        "message": result.message,
        "next_steps": result.next_steps,
    }


@router.get("/{channel_id}/status")
async def channel_status(
    channel_id: str,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Get setup status for a channel."""
    return await channel_orchestrator.get_status(channel_id)


@router.post("/{channel_id}/test")
async def test_channel(
    channel_id: str,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Test connectivity with a configured channel."""
    result = await channel_orchestrator._test_connection(channel_id)
    return result


@router.delete("/{channel_id}/config")
async def disconnect_channel(
    channel_id: str,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Disconnect and remove channel configuration."""
    result = await channel_orchestrator.disconnect(channel_id)
    return {
        "success": result.success,
        "message": result.message,
    }


@router.post("/{channel_id}/qr-code")
async def generate_qr_code(
    channel_id: str,
    session: AuthenticatedSession = Depends(validate_session),
):
    """Generate a QR code for channel linking and push to nearest touch panel."""
    code = ChannelRegistry.generate_verification_code(
        user_id=session.user_id,
        channel=channel_id,
    )
    if not code:
        raise HTTPException(status_code=500, detail="Failed to generate verification code")

    qr_data = f"zoe://link?channel={channel_id}&code={code}"

    qr_result = await push_qr_to_touch_panel(
        data=qr_data,
        title=f"Link {channel_id.title()} Account",
        user_id=session.user_id,
    )

    return {
        "code": code,
        "channel": channel_id,
        "qr_data": qr_data,
        "qr_base64": qr_result.get("qr_base64", ""),
        "panel_found": qr_result.get("panel_found", False),
        "panel_name": qr_result.get("panel_name"),
        "pushed": qr_result.get("pushed", False),
        "expires_in": "10 minutes",
    }


# ---- Webhook ----

@router.post("/{channel_id}/webhook")
async def channel_webhook(channel_id: str, request: Request):
    """Webhook receiver for external channels.
    
    Parses incoming messages, checks for verification codes,
    routes through Trust Gate and chat pipeline, then replies.
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

        # Check if this is a verification code (6-digit number)
        content = (message.content or "").strip()
        if content.isdigit() and len(content) == 6:
            user_id = ChannelRegistry.verify_code(
                channel=channel_id,
                external_id=message.external_id,
                code=content,
            )
            if user_id:
                await adapter.send_message(
                    message.session_key,
                    f"Account linked successfully! You're now connected to Zoe.",
                )
                return {"status": "linked", "user_id": user_id}

        if not message.user_id:
            await adapter.send_message(
                message.session_key,
                "Hi! I don't recognise this account yet. "
                "To link it, go to your Zoe settings and generate a verification code, "
                "then send it here as a 6-digit number.",
            )
            return {
                "status": "unbound",
                "message": "No Zoe user bound to this external identity.",
            }

        # Route through Trust Gate and chat pipeline
        try:
            from security.trust_gate import trust_gate
            gate_result = await trust_gate.evaluate(
                user_id=message.user_id,
                message=message.content,
                channel=channel_id,
            )
            if not gate_result.get("allowed", True):
                await adapter.send_message(
                    message.session_key,
                    "Sorry, I can't process that request right now.",
                )
                return {"status": "blocked", "reason": gate_result.get("reason")}
        except Exception as e:
            logger.debug(f"Trust gate check skipped: {e}")

        # Send to chat handler
        try:
            from routers.chat import _chat_handler
            chat_response = await _chat_handler(
                message=message.content,
                user_id=message.user_id,
                session_key=message.session_key,
                channel=channel_id,
            )
            reply_text = chat_response.get("response", "I'm not sure how to respond to that.")
        except Exception as e:
            logger.error(f"Chat handler error: {e}")
            reply_text = "Sorry, I had trouble processing that. Please try again."

        await adapter.send_message(message.session_key, reply_text)

        return {
            "status": "processed",
            "channel": channel_id,
            "user_id": message.user_id,
        }

    except Exception as e:
        logger.error(f"Channel webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
