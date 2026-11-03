"""The gate in front of Music Assistant's own web UI + WebSocket (nginx ``/modules/music-assistant/``).

nginx used to proxy that path straight through, so anyone on the LAN had full Music Assistant control (add / remove providers,
play anywhere, change settings). It is now an ``auth_request``: nginx asks ``GET /api/music/ma-ui/gate`` before every request to
the path - HTTP and the WebSocket handshake alike - and proxies only on a 2xx.

Zoe's member session is a bearer header kept in localStorage, which a browser cannot attach to a WebSocket handshake or a
``window.open`` navigation. So the page (music.html) first calls ``POST /api/music/ma-ui/session`` with its normal credentials;
that sets a signed, HttpOnly, SameSite=Strict cookie scoped to ``/modules/music-assistant/`` and nothing else, and the gate accepts
the cookie. The gate also accepts the credentials directly (a member's ``X-Session-ID``, the panel's device token, the kiosk
credential or a registered panel address) so a caller that can set headers - or the panel itself - never needs the cookie.

Who passes, minting or gating: a signed-in member or the panel (``panel_principal`` rules, strict ``registered`` form). The
``ZOE_HA_CONTROL_SCOPE=open`` rollback does NOT open this: it is a home-control rollback, and this surface can reconfigure the
whole music stack. ``ZOE_MA_UI_GATE=off`` is the rollback for this gate alone (read per request; the gate answers 204 to all).
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Mapping, Optional

import auth_handoff
import panel_principal
from typed_env import env_bool, env_int, env_str

COOKIE = "zoe_ma_ui"
COOKIE_PATH = "/modules/music-assistant/"
_PURPOSE = "ma-ui"


def enabled() -> bool:
    """``ZOE_MA_UI_GATE`` (default on). Off = the gate answers 204 to everyone: the pre-2026-10-19 behaviour, an operator
    ROLLBACK only. Read per request."""
    return env_bool("ZOE_MA_UI_GATE", default=True)


def ttl_s() -> int:
    return max(300, env_int("ZOE_MA_UI_TTL_S", 43200))


def _key() -> Optional[str]:
    """``ZOE_MA_UI_SECRET`` as is (also where SignedTokens keeps its per-process random fallback, so cookies then die with the
    process), else a key derived from ``ZOE_INTERNAL_TOKEN`` for this purpose only - a jukebox / music-setup / kiosk token can
    never verify here, nor this cookie there."""
    own = env_str("ZOE_MA_UI_SECRET")
    if own:
        return own
    base = env_str("ZOE_INTERNAL_TOKEN")
    return hmac.new(base.encode(), b"zoe-ma-ui-v1", hashlib.sha256).hexdigest() if base else None


_TOKENS = auth_handoff.SignedTokens("ZOE_MA_UI_SECRET", _key)


def mint(who: str) -> dict[str, Any]:
    ttl = ttl_s()
    return {"token": _TOKENS.mint({"k": _PURPOSE, "who": str(who or "")[:64]}, ttl), "expires_in": ttl}


def cookie_valid(request: Any) -> bool:
    """The request carries an unexpired, correctly signed MA-UI cookie."""
    raw = str((getattr(request, "cookies", None) or {}).get(COOKIE, "") or "").strip()
    claims = _TOKENS.verify(raw) if raw else None
    return bool(claims and claims.get("k") == _PURPOSE)


async def principal_ok(request: Any, user: Mapping[str, Any] | None) -> bool:
    """A signed-in member, or the panel in its strict ``registered`` form (device token, kiosk credential, registered address -
    never over the Cloudflare tunnel). Never raises (fail closed)."""
    try:
        if panel_principal.is_member(user):
            return True
        u = user or {}
        if u.get("unauthenticated_override"):
            u = {"user_id": "guest", "role": "guest"}  # a header-less caller IS anonymous: judge it as one, by its device
        return await panel_principal.is_panel_guest(request, u, None, mode="registered")
    except Exception:  # noqa: BLE001 - fail closed
        return False
