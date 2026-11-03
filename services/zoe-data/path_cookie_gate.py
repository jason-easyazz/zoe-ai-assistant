"""Shared mechanics for a web UI that nginx gates with ``auth_request`` and a signed, path-scoped cookie.

A third-party web UI proxied by nginx (Home Assistant's ``/ha/``; Music Assistant's and the Multica board's follow the same
shape) cannot read Zoe's member session: that is a bearer header in localStorage, which a browser never attaches to a
navigation, an asset load or a WebSocket handshake. So the gate accepts either the caller's own credentials (a header, the
panel's device token / kiosk credential / registered address) or a short-lived HMAC-signed cookie, HttpOnly + SameSite=Strict
and scoped to the gated path(s) only, which a Zoe page mints for a signed-in caller.

This module owns only the mechanics - signing, verifying and setting that cookie. WHO may pass is each gate's policy (for
example ``panel_principal.member_or_panel``), and each gate reads its own env vars with literal names (so the flag inventory
sees them) and hands them in as callables. Keys are purpose-separated: ``derived_key`` labels the shared base secret per
gate, and every token carries the gate's purpose claim, so one gate's cookie never verifies at another.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Callable, Optional

import auth_handoff


def derived_key(own_secret: str, base_secret: str, label: str) -> Optional[str]:
    """The gate's own secret as is, else a key derived from the shared base secret (``ZOE_INTERNAL_TOKEN``) for this label
    only, else None (``SignedTokens`` then keeps a per-process random key: cookies die with the process - fail safe)."""
    if own_secret:
        return own_secret
    return hmac.new(base_secret.encode(), label.encode(), hashlib.sha256).hexdigest() if base_secret else None


@dataclass
class PathCookieGate:
    """One gated UI. ``enabled`` / ``ttl_s`` / ``key`` are read per call by the owning module."""

    purpose: str
    cookie: str
    paths: tuple[str, ...]
    secret_env: str
    key: Callable[[], Optional[str]]
    ttl_s: Callable[[], int]
    enabled: Callable[[], bool]

    def __post_init__(self) -> None:
        self._tokens = auth_handoff.SignedTokens(self.secret_env, self.key)

    def mint(self, who: str) -> dict[str, Any]:
        ttl = max(60, int(self.ttl_s()))
        return {"token": self._tokens.mint({"k": self.purpose, "who": str(who or "")[:64]}, ttl), "expires_in": ttl}

    def cookie_valid(self, request: Any) -> bool:
        """The request carries an unexpired, correctly signed cookie for THIS gate."""
        raw = str((getattr(request, "cookies", None) or {}).get(self.cookie, "") or "").strip()
        claims = self._tokens.verify(raw) if raw else None
        return bool(claims and claims.get("k") == self.purpose)

    def set_cookies(self, request: Any, response: Any, minted: dict[str, Any]) -> None:
        """One cookie per gated path (a cookie has a single Path; ``/`` would ride along on every Zoe request)."""
        secure = str(request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower() == "https"
        for path in self.paths:
            response.set_cookie(
                self.cookie, minted["token"], max_age=minted["expires_in"], path=path,
                secure=secure, httponly=True, samesite="strict",
            )
        response.headers["Cache-Control"] = "no-store"
