"""music_setup — the QR→phone "add a music source through Zoe" handoff.

The panel mints a short-lived, single-use setup token and shows it as a QR. The
user's phone scans it, opens Zoe's own setup page, and completes the provider
setup (a form for credential providers like YouTube Music; OAuth for Spotify —
added in a later slice). Zoe's backend then saves the config into Music
Assistant. The user never sees Music Assistant.

Security: the token is HMAC-signed (tamper-proof), short-TTL, and single-use, so
only the phone that just scanned the panel — within the window — can complete a
setup. This gates WHO can add accounts; the provider's own auth is separate.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Optional

# TTL for a setup token — long enough to scan + fill a form, short enough that a
# leaked QR photo is useless soon after.
SETUP_TTL_S = int(os.environ.get("ZOE_MUSIC_SETUP_TTL_S", "900"))  # 15 min

# Single-use ledger: nonces consumed (or issued-then-spent), with expiry so it
# self-cleans. In-process is fine — zoe-data is one process and TTLs are short.
_consumed: dict[str, float] = {}


def _secret() -> bytes:
    s = os.environ.get("ZOE_MUSIC_SETUP_SECRET") or os.environ.get("ZOE_INTERNAL_TOKEN") or ""
    if not s:
        # No configured secret → derive a per-process ephemeral one. Tokens then
        # only survive within this process lifetime, which is acceptable for a
        # 15-min setup flow and fails safe (never a predictable/empty key).
        s = secrets.token_hex(32)
        os.environ["ZOE_MUSIC_SETUP_SECRET"] = s
    return s.encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _prune() -> None:
    now = time.time()
    for k in [k for k, exp in _consumed.items() if exp < now]:
        _consumed.pop(k, None)


def mint(provider: str, user_id: str = "") -> dict[str, Any]:
    """Mint a single-use setup token for a provider. Returns {token, expires_in}."""
    exp = int(time.time()) + SETUP_TTL_S
    payload = {"p": provider, "u": user_id, "exp": exp, "n": secrets.token_urlsafe(9)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return {"token": f"{body}.{sig}", "expires_in": SETUP_TTL_S, "provider": provider}


def verify(token: str) -> Optional[dict[str, Any]]:
    """Validate a setup token (signature + TTL + not-yet-consumed). Returns the
    payload {p, u, exp, n} or None. Does NOT consume — call consume() on save."""
    try:
        body, sig = str(token).split(".", 1)
        expected = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    _prune()
    if payload.get("n") in _consumed:
        return None
    return payload


def consume(token: str) -> Optional[dict[str, Any]]:
    """Verify + mark single-use spent. Returns the payload or None. Idempotency:
    a second consume of the same token returns None."""
    payload = verify(token)
    if payload is None:
        return None
    _consumed[payload["n"]] = int(payload.get("exp", time.time() + SETUP_TTL_S))
    return payload
