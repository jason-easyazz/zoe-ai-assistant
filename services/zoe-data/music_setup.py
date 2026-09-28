"""music_setup — the QR→phone "add a music source through Zoe" handoff.

The panel mints a short-lived, single-use setup token and shows it as a QR. The
user's phone scans it, opens Zoe's own setup page, and completes the provider
setup (a form for credential providers like YouTube Music; OAuth for Spotify —
added in a later slice). Zoe's backend then saves the config into Music
Assistant. The user never sees Music Assistant.

Security: the token is HMAC-signed (tamper-proof), short-TTL, and single-use
(``auth_handoff.SignedTokens`` — the shared mechanics), so
only the phone that just scanned the panel — within the window — can complete a
setup. This gates WHO can add accounts; the provider's own auth is separate.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

import auth_handoff

# TTL for a setup token — long enough to scan + fill a form, short enough that a
# leaked QR photo is useless soon after.
SETUP_TTL_S = int(os.environ.get("ZOE_MUSIC_SETUP_TTL_S", "900"))  # 15 min

# The signing + single-use mechanics are auth_handoff's (one copy); this module
# keeps its own key (ZOE_MUSIC_SETUP_SECRET), claims and wire format.
_TOKENS = auth_handoff.SignedTokens(
    "ZOE_MUSIC_SETUP_SECRET", lambda: os.environ.get("ZOE_MUSIC_SETUP_SECRET"))
_consumed = _TOKENS.ledger.spent


def mint(provider: str, user_id: str = "") -> dict[str, Any]:
    """Mint a single-use setup token for a provider. Returns {token, expires_in}."""
    token = _TOKENS.mint({"p": provider, "u": user_id}, SETUP_TTL_S)
    return {"token": token, "expires_in": SETUP_TTL_S, "provider": provider}


def verify(token: str) -> Optional[dict[str, Any]]:
    """Validate a setup token (signature + TTL + not-yet-consumed). Returns the
    payload {p, u, exp, n} or None. Does NOT consume — call consume() on save."""
    return _TOKENS.verify(token)


def consume(token: str) -> Optional[dict[str, Any]]:
    """Verify + mark single-use spent. Returns the payload or None. Idempotency:
    a second consume of the same token returns None."""
    return _TOKENS.consume(token)


def qr_path(token: str, provider: str) -> str:
    """The panel's QR image path for a minted token: an opaque single-use handle
    (``setup_qr``), never the token — nginx logs query strings."""
    import setup_qr

    return f"/api/music/setup/qr/{setup_qr.issue('music', token=token, provider=provider)}"


def handoff_link(provider: str, user_id: str = "") -> dict[str, Any]:
    """The phone link for an ``auth_handoff`` of kind ``music``: a fresh setup
    token, its nonce as the handoff's ``ref``, and the fragment-only phone path."""
    minted = mint(provider, user_id)
    nonce = json.loads(auth_handoff.b64d(minted["token"].split(".", 1)[0]))["n"]
    return {"token": minted["token"], "ref": nonce, "ttl": minted["expires_in"],
            "path": f"/setup-music.html#provider={provider}&t={minted['token']}"}
