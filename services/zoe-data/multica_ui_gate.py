"""The Zoe sign-in gate in front of the Multica board (nginx ``/multica/``, ``/multica-api/`` and ``/_next/``).

Upstream Multica (checked through v0.6.3 and main on 2026-10-19) has no generic OIDC login: its only SSO is Google, with the
endpoints hardcoded, and the open upstream work (multica-ai/multica#1014, PRs #5791 / #5792 / #8129) has not merged. Without an
email backend the board's only login is the dev verification code, which made a six-digit code the security boundary for anyone
on the LAN. So the board now sits behind Zoe's own sign-in: nginx runs an ``auth_request`` to ``GET /api/multica-ui/gate``
before every request to those three paths (HTTP and the WebSocket handshake alike) and proxies only on a 2xx. Multica's own
login (the dev code) is then a second step that only a signed-in adult household member can even reach.

Zoe's member session is a bearer header kept in localStorage, which a browser cannot attach to a navigation, an asset load or a
WebSocket handshake. So ``/board.html`` first calls ``POST /api/multica-ui/session`` with its normal credentials; that sets a
signed, HttpOnly, SameSite=Strict cookie on each of the three paths and nothing else (mechanics shared with the Music
Assistant UI gate: ``path_gate.py``), and the gate accepts the cookie. The gate
also accepts the credentials directly (an ``X-Session-ID``), so a caller that can set headers never needs the cookie.

Who passes, minting or gating: a signed-in household member who is POSITIVELY an adult (``manner_block.eligible``: an
``auth_users`` role in ``ADULT_ROLES`` and no minor / kid ``member_modes`` row; any lookup failure says no). Never a guest, a
child, the header-less caller ``ZOE_UNAUTHENTICATED_ROLE=family-admin`` promotes, an auth-outage principal, or the wall panel
(a kiosk is whoever is standing at it). ``ZOE_MULTICA_UI_GATE=off`` is the operator rollback (read per request; the gate then
answers 204 to all).

Not covered by this gate, by design: zoe-data's own board sync (``MULTICA_BASE_URL=http://localhost:8080``), Cursor MCP and
``scripts/setup/populate_multica.py`` reach the backend on loopback, and the executor / Omnigent read the ``multica`` database
directly. None of them goes through nginx.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

import manner_block
import panel_principal
import path_gate
from typed_env import env_bool, env_int, env_str

COOKIE = "zoe_multica_ui"
#: one cookie per proxied prefix - a cookie has a single Path, and Path=/ would ride along on every Zoe request
COOKIE_PATHS = ("/multica/", "/multica-api/", "/_next/")
_PURPOSE = "multica-ui"


def enabled() -> bool:
    """``ZOE_MULTICA_UI_GATE`` (default on). Off = the gate answers 204 to everyone: the pre-2026-10-19 behaviour, an
    operator ROLLBACK only. Read per request."""
    return env_bool("ZOE_MULTICA_UI_GATE", default=True)


def ttl_s() -> int:
    """Cookie lifetime (``ZOE_MULTICA_UI_TTL_S``, default 8 h, floor 5 min). A Zoe sign-out does not revoke a minted cookie;
    this bound is the revocation."""
    return max(300, env_int("ZOE_MULTICA_UI_TTL_S", 28800))


def _key() -> Optional[str]:
    """``ZOE_MULTICA_UI_SECRET`` as is, else a key derived from ``ZOE_INTERNAL_TOKEN`` for this purpose only
    (``path_gate.purpose_key``)."""
    return path_gate.purpose_key(env_str("ZOE_MULTICA_UI_SECRET"), b"zoe-multica-ui-v1")


GATE = path_gate.PathCookieGate(purpose=_PURPOSE, cookie=COOKIE, paths=COOKIE_PATHS, secret_env="ZOE_MULTICA_UI_SECRET",
                                read_key=_key, ttl_s=ttl_s)


def mint(who: str) -> dict[str, Any]:
    return GATE.mint(who)


def cookie_valid(request: Any) -> bool:
    """The request carries an unexpired, correctly signed board cookie."""
    return GATE.cookie_valid(request)


def is_signed_in(user: Mapping[str, Any] | None) -> bool:
    """A real signed-in principal that is not a panel device (``panel_principal.is_member`` excludes guests, the
    ``family-admin`` promotion and auth-outage principals; a device token's principal carries ``device_token_role`` /
    ``panel_id`` even under the ``ZOE_DEVICE_TOKEN_BINDS_MEMBER`` rollback)."""
    u = user or {}
    if u.get("device_token_role") or u.get("panel_id"):
        return False
    return panel_principal.is_member(u)


async def adult_member(user: Mapping[str, Any] | None) -> bool:
    """Signed in AND positively an adult household member. Never raises (fail closed)."""
    try:
        return is_signed_in(user) and await manner_block.eligible(str((user or {}).get("user_id") or ""))
    except Exception:  # noqa: BLE001 - fail closed
        return False
