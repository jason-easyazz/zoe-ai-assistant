"""The phone jukebox capability: a QR on the panel lets a phone play and queue music, nothing else.

Since 2026-10-18 every music write needs a member or the panel (``require_member_or_panel``), and a phone that scans the
panel's jukebox QR is neither. So the panel mints a short-lived, single-purpose token when it shows the QR (``mint``, via
``POST /api/music/jukebox/start``), the QR is a server SVG by single-use handle (``setup_qr``, the same no-secret-in-a-URL
pattern as the music setup QR), and the phone page keeps the token in its ``#fragment`` and sends it as ``X-Jukebox-Token``.

The token is: signed (``auth_handoff.SignedTokens``, with a key DERIVED for this purpose so a music-setup or Telegram-link
token can never verify here, nor this one there); bound to the panel that showed it (refused once that panel is inactive or
stops allowing guests); expiring (``ZOE_JUKEBOX_TTL_S``, default 2 h); multi-use within that window (a phone queues several
songs); accepted ONLY by ``/api/music/play_media`` and ``/api/music/play`` (``require_member_panel_or_jukebox``), never by a
queue edit, a library write or a speaker change; and never over the Cloudflare tunnel unless a member is signed in.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Optional

from fastapi import Depends, Request

import auth_handoff
import panel_kiosk_credential
import panel_principal
from auth import get_current_user
from typed_env import env_int, env_str

HEADER = "X-Jukebox-Token"
_PURPOSE = "jukebox"


def ttl_s() -> int:
    return max(60, env_int("ZOE_JUKEBOX_TTL_S", 7200))


def _key() -> Optional[str]:
    """``ZOE_JUKEBOX_SECRET`` as is (dedicated; also where SignedTokens keeps its per-process random fallback, so tokens
    then die with the process), else a key derived from ``ZOE_INTERNAL_TOKEN`` for this purpose only."""
    own = env_str("ZOE_JUKEBOX_SECRET")
    if own:
        return own
    base = env_str("ZOE_INTERNAL_TOKEN")
    return hmac.new(base.encode(), b"zoe-jukebox-v1", hashlib.sha256).hexdigest() if base else None


_TOKENS = auth_handoff.SignedTokens("ZOE_JUKEBOX_SECRET", _key)


def mint(panel_id: str) -> dict[str, Any]:
    ttl = ttl_s()
    return {"token": _TOKENS.mint({"k": _PURPOSE, "pid": str(panel_id)}, ttl), "expires_in": ttl}


def panel_of(token: str) -> Optional[str]:
    """The panel a valid jukebox token is bound to, or None (bad signature, expired, wrong purpose)."""
    claims = _TOKENS.verify(token) if token else None
    if not claims or claims.get("k") != _PURPOSE or not claims.get("pid"):
        return None
    return str(claims["pid"])


async def require_member_panel_or_jukebox(request: Request, user: dict = Depends(get_current_user)) -> dict:
    """A member or the panel (``require_member_or_panel``'s rule), or a phone holding a valid jukebox token for an active,
    guest-allowing panel - never over the tunnel. For play / queue-a-song only."""
    if await panel_principal.may_control_home(request, user):
        return user
    pid = panel_of(str(request.headers.get(HEADER, "") or "").strip())
    if pid and not panel_kiosk_credential.via_public_tunnel(request) and await panel_principal._with_registry(
            None, panel_principal._kiosk_panel_allows_guest, pid):
        return user
    raise panel_principal._refuse("no member, panel or jukebox token", request, user,
                                  "Scan the code on the panel again, or sign in")
