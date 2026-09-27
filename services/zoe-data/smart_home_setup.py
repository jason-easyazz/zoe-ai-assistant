"""smart_home_setup — the QR→phone "add a device to your home" handoff token.

The panel mints a short-lived, single-use token and shows it as a QR. The owner's
phone scans it, opens Zoe's own branded setup guide (setup-device.html), and is
walked through adding a device. Home Assistant runs headless and its MCP bridge
exposes no config-flow/pairing endpoint, so Zoe can't silently finish pairing —
the token simply gates WHO gets the guide (only the phone that just scanned the
panel, within the window), the same shape as music_setup.

Security: HMAC-signed (tamper-proof), short-TTL, single-use — the shared
``auth_handoff.SignedTokens`` mechanics.
"""
from __future__ import annotations

import os
import time  # noqa: F401 — the module's public surface; tests patch smart_home_setup.time.time
from typing import Any, Optional

import auth_handoff

# TTL for a setup token — long enough to scan + read the guide, short enough that
# a leaked QR photo is useless soon after.
SETUP_TTL_S = int(os.environ.get("ZOE_HOME_SETUP_TTL_S", "900"))  # 15 min

# Shared mechanics (auth_handoff); own key, claims and wire format.
_TOKENS = auth_handoff.SignedTokens(
    "ZOE_HOME_SETUP_SECRET", lambda: os.environ.get("ZOE_HOME_SETUP_SECRET"))
_consumed = _TOKENS.ledger.spent


def mint(user_id: str = "") -> dict[str, Any]:
    """Mint a single-use setup token. Returns {token, expires_in}."""
    return {"token": _TOKENS.mint({"u": user_id}, SETUP_TTL_S), "expires_in": SETUP_TTL_S}


def verify(token: str) -> Optional[dict[str, Any]]:
    """Validate a token (signature + TTL + not-yet-consumed). Returns the payload
    {u, exp, n} or None. Does NOT consume — call consume() to spend it."""
    return _TOKENS.verify(token)


def consume(token: str) -> Optional[dict[str, Any]]:
    """Validate AND spend the token (single-use). Returns the payload or None."""
    return _TOKENS.consume(token)


def qr_path(token: str) -> str:
    """The panel's QR image path for a minted token: an opaque single-use handle
    (``setup_qr``), never the token — nginx logs query strings."""
    import setup_qr

    return f"/api/home/setup/qr/{setup_qr.issue('home', token=token)}"
