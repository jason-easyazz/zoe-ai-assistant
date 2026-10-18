"""Shared mechanics for an nginx ``auth_request`` gate in front of a browser surface Zoe does not own (Music Assistant's own UI,
the Multica board): the signed, path-scoped cookie a page mints with its normal credentials, and its check.

Why a cookie at all: Zoe's member session is an ``X-Session-ID`` header kept in localStorage, and a browser cannot attach a
header to a navigation, an asset load, a ``window.open`` or a WebSocket handshake. So the page trades its session for this
cookie (HttpOnly, SameSite=Strict, Secure over https, one per proxied path prefix and nowhere else), and the gate accepts it.

This module is the MECHANICS only. WHO may mint and pass (the policy) and the env reads (the flag inventory greps literal
names) stay in each surface's own module: ``ma_ui_gate.py``, ``multica_ui_gate.py``.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Callable, Optional

import auth_handoff
from typed_env import env_str


def purpose_key(own: str, label: bytes) -> Optional[str]:
    """The surface's own secret when set, else a key derived from ``ZOE_INTERNAL_TOKEN`` for ``label`` only - so one surface's
    cookie can never verify at another's gate, and the raw internal token is never the key. ``None`` when neither is set
    (``SignedTokens`` then keeps a per-process random key: cookies die with the process, fail safe)."""
    if own:
        return own
    base = env_str("ZOE_INTERNAL_TOKEN")
    return hmac.new(base.encode(), label, hashlib.sha256).hexdigest() if base else None


class PathCookieGate:
    """One surface's cookie: ``purpose`` is stamped into the token and checked, ``paths`` are the cookie scopes."""

    def __init__(self, *, purpose: str, cookie: str, paths: tuple[str, ...], secret_env: str,
                 read_key: Callable[[], Optional[str]], ttl_s: Callable[[], int]) -> None:
        self.purpose, self.cookie, self.paths, self.ttl_s = purpose, cookie, paths, ttl_s
        self._tokens = auth_handoff.SignedTokens(secret_env, read_key)

    def mint(self, who: str) -> dict[str, Any]:
        ttl = self.ttl_s()
        return {"token": self._tokens.mint({"k": self.purpose, "who": str(who or "")[:64]}, ttl), "expires_in": ttl}

    def cookie_valid(self, request: Any) -> bool:
        """The request carries an unexpired, correctly signed cookie of THIS purpose."""
        raw = str((getattr(request, "cookies", None) or {}).get(self.cookie, "") or "").strip()
        claims = self._tokens.verify(raw) if raw else None
        return bool(claims and claims.get("k") == self.purpose)

    def set_cookies(self, request: Any, response: Any, who: str) -> dict[str, Any]:
        """Mint one token and set it on every path scope; returns the body the session route answers with."""
        minted = self.mint(who)
        secure = str(request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower() == "https"
        for path in self.paths:
            response.set_cookie(self.cookie, minted["token"], max_age=minted["expires_in"], path=path,
                                secure=secure, httponly=True, samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        return {"ok": True, "expires_in": minted["expires_in"]}
