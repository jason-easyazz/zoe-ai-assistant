"""
Touch Panel Push Utility
=========================

Pushes visual content (QR codes, overlays) to nearby touch panels
via WebSocket connections.

Used by the channel setup flow to display QR codes for account linking.
"""

import base64
import io
import json
import logging
import os
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_PATH", "/app/data/zoe.db")


def _generate_qr_base64(data: str, size: int = 256) -> str:
    """Generate a QR code as a base64-encoded PNG image.
    
    Args:
        data: Text/URL to encode in the QR code
        size: Image size in pixels
        
    Returns:
        Base64-encoded PNG string
    """
    try:
        import qrcode
        from qrcode.image.pil import PilImage

        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return base64.b64encode(buffer.read()).decode("utf-8")
    except ImportError:
        logger.warning("qrcode library not installed, generating placeholder")
        return ""
    except Exception as e:
        logger.error(f"QR generation failed: {e}")
        return ""


def _find_best_touch_panel(user_id: str = None, room: str = None) -> Optional[dict]:
    """Find the most appropriate touch panel to display content on.
    
    Priority:
    1. Panel in same room as user (if room specified)
    2. Panel assigned to user
    3. Any online panel with display capability
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        if room:
            cursor.execute("""
                SELECT * FROM devices 
                WHERE type = 'touch_panel' AND status = 'online' AND room = ?
                LIMIT 1
            """, (room,))
            row = cursor.fetchone()
            if row:
                return dict(row)

        if user_id:
            cursor.execute("""
                SELECT * FROM devices 
                WHERE type = 'touch_panel' AND status = 'online' AND assigned_user = ?
                LIMIT 1
            """, (user_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)

        cursor.execute("""
            SELECT * FROM devices 
            WHERE type = 'touch_panel' AND status = 'online'
            LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            return dict(row)

        return None
    except Exception as e:
        logger.debug(f"Touch panel lookup failed (table may not exist): {e}")
        return None
    finally:
        conn.close()


async def push_qr_to_touch_panel(
    data: str,
    title: str = "Scan to Link",
    user_id: str = None,
    room: str = None,
    duration_seconds: int = 120,
) -> dict:
    """Generate a QR code and push it to the nearest touch panel.
    
    Args:
        data: Text/URL to encode in the QR code
        title: Title to display above the QR code
        user_id: Optional user ID for panel selection
        room: Optional room name for panel selection
        duration_seconds: How long to display the overlay
        
    Returns:
        Dict with success status, panel info, and base64 QR image
    """
    qr_base64 = _generate_qr_base64(data)
    if not qr_base64:
        return {
            "success": False,
            "error": "Failed to generate QR code",
            "qr_base64": "",
        }

    panel = _find_best_touch_panel(user_id=user_id, room=room)

    result = {
        "success": True,
        "qr_base64": qr_base64,
        "title": title,
        "data": data,
        "panel_found": panel is not None,
    }

    if panel:
        sent = await _send_to_panel(panel, {
            "event": "display_overlay",
            "payload": {
                "type": "qr_code",
                "title": title,
                "image_base64": qr_base64,
                "duration_seconds": duration_seconds,
            },
        })
        result["panel_id"] = panel.get("id")
        result["panel_name"] = panel.get("name", "Unknown Panel")
        result["panel_room"] = panel.get("room", "Unknown")
        result["pushed"] = sent
    else:
        result["pushed"] = False
        result["note"] = "No touch panel found; QR code returned in response for display in chat"

    return result


async def push_content_to_touch_panel(
    content_type: str,
    payload: dict,
    user_id: str = None,
    room: str = None,
) -> dict:
    """Push arbitrary content to a touch panel.
    
    Args:
        content_type: Type of content (e.g., "notification", "image", "text")
        payload: Content payload
        user_id: Optional user ID for panel selection
        room: Optional room name for panel selection
        
    Returns:
        Dict with success status
    """
    panel = _find_best_touch_panel(user_id=user_id, room=room)
    if not panel:
        return {"success": False, "error": "No touch panel available"}

    sent = await _send_to_panel(panel, {
        "event": "display_overlay",
        "payload": {"type": content_type, **payload},
    })

    return {"success": sent, "panel_id": panel.get("id")}


async def _send_to_panel(panel: dict, message: dict) -> bool:
    """Send a WebSocket message to a touch panel.
    
    Connects to the panel's WebSocket endpoint and sends the message.
    """
    ws_url = panel.get("ws_url") or panel.get("address")
    if not ws_url:
        logger.warning(f"Touch panel {panel.get('id')} has no WebSocket URL")
        return False

    try:
        import websockets
        async with websockets.connect(ws_url, close_timeout=5) as ws:
            await ws.send(json.dumps(message))
            logger.info(f"Content pushed to touch panel: {panel.get('name', panel.get('id'))}")
            return True
    except ImportError:
        logger.warning("websockets library not installed, cannot push to panel")
        return False
    except Exception as e:
        logger.error(f"Failed to push to touch panel: {e}")
        return False
