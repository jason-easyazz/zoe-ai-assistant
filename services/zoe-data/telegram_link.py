"""Self-service Telegram account linking — stateless signed link tokens + QR.

Flow:
  1. An authenticated user asks for a link token
     (POST /api/user/profile/telegram/link-token). We mint a short-lived,
     HMAC-signed token that ENCODES their user_id + expiry — no server-side
     storage, so it survives restarts and needs no DB table.
  2. The UI shows a Telegram deep link (`https://t.me/<bot>?start=<token>`) and a
     QR of it. The user opens/scans it; Telegram sends the bot `/start <token>`.
  3. The bot forwards the token + the VERIFIED sender id to zoe-data
     (POST /api/system/telegram/consume-link-token, internal-only). We verify the
     signature + expiry, recover the user_id, and store the telegram_id on that
     user's profile.

Security: the token proves "the person who generated this in an authenticated Zoe
session wants to link whatever Telegram account redeems it." The sender's numeric
id is supplied by Telegram to the bot (not by the user), and the consume endpoint
is internal-only — so a leaked token can at worst link the leaker's OWN Telegram
account to the generating user, which is the same trust the deep link already
implies. Tokens are single-use in practice (short TTL) and cannot be forged
without the secret.
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Signing secret, PURPOSE-SPECIFIC to link tokens. Set ZOE_TELEGRAM_LINK_SECRET
# (recommended) so tokens survive a zoe-data restart and rotate independently of
# other credentials; otherwise a per-process random is used (tokens are short-
# lived, so a restart just invalidates any in-flight ones — acceptable). We do NOT
# reuse ZOE_INTERNAL_TOKEN here: mixing an internal-auth credential with a token-
# signing key couples their rotation policies.
_LINK_SECRET = (
    os.environ.get("ZOE_TELEGRAM_LINK_SECRET") or secrets.token_hex(32)
).encode()

LINK_TOKEN_TTL_S = 600  # 10 minutes
_SIG_BYTES = 12  # 96-bit truncated HMAC — ample for a 10-minute single-use token

# Single-use enforcement. In-process only (a restart clears it, but expired tokens
# are rejected regardless) — generate + redeem both hit the same zoe-data process.
# Two sets so single-use holds ACROSS the async DB write without burning the token
# on failure:
#   _pending_sigs  — reserved by verify_link_token() the instant a token validates
#                    (synchronous, no await before the reservation), so a second
#                    concurrent scan of the same QR can't also pass verification.
#   _consumed_sigs — permanently redeemed (moved here after the link write commits).
# A token whose sig is in EITHER set is rejected. On a failed write the caller
# calls release_token() to drop the reservation so the user can just re-scan.
_pending_sigs: dict[bytes, int] = {}
_consumed_sigs: dict[bytes, int] = {}


def _prune(now: int) -> None:
    for store in (_pending_sigs, _consumed_sigs):
        for k, exp in list(store.items()):
            if exp < now:
                del store[k]


def make_link_token(user_id: str, ttl: int = LINK_TOKEN_TTL_S) -> str:
    """Mint a URL-safe, HMAC-signed token encoding user_id + expiry.

    Fits Telegram's deep-link start-param charset ([A-Za-z0-9_-]) and 64-char
    limit for the short user_id slugs Zoe uses.
    """
    exp = int(time.time()) + int(ttl)
    payload = f"{user_id}:{exp}".encode()
    sig = hmac.new(_LINK_SECRET, payload, hashlib.sha256).digest()[:_SIG_BYTES]
    # Fixed-length sig appended with NO separator — a byte separator could collide
    # with a 0x2E in the binary signature. sig is always the last _SIG_BYTES bytes.
    raw = payload + sig
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_raw(token: str):
    """Return (user_id, sig, exp) for an authentic, unexpired token, else None.
    Validates signature + expiry ONLY — no pending/consumed check, no side effect.
    Used by mark/release which operate on an already-reserved sig."""
    if not token or len(token) > 128:
        return None
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad)
        if len(raw) <= _SIG_BYTES:
            return None
        payload, sig = raw[:-_SIG_BYTES], raw[-_SIG_BYTES:]
        expected = hmac.new(_LINK_SECRET, payload, hashlib.sha256).digest()[:_SIG_BYTES]
        if not hmac.compare_digest(sig, expected):
            return None
        user_id, exp_s = payload.decode().rsplit(":", 1)
        exp = int(exp_s)
        if exp < int(time.time()):
            return None
        return (user_id or None, sig, exp)
    except Exception:
        return None


def verify_link_token(token: str) -> Optional[str]:
    """Validate a token AND atomically reserve it (single-use). Returns the
    user_id, or None if invalid/expired/already-claimed. There is NO ``await``
    between the claim check and the reservation, so two concurrent redemptions of
    the same token cannot both pass. The caller MUST then either mark_token_consumed
    (on a successful, committed link) or release_token (on any failure) — the
    reservation alone never permanently burns the token (it expires with the TTL)."""
    decoded = _decode_raw(token)
    if not decoded:
        return None
    user_id, sig, exp = decoded
    now = int(time.time())
    _prune(now)
    if sig in _pending_sigs or sig in _consumed_sigs:
        return None  # already claimed by a concurrent request, or already redeemed
    _pending_sigs[sig] = exp  # reserve — synchronous, no await before this point
    return user_id


def mark_token_consumed(token: str) -> None:
    """Promote a reserved token to permanently redeemed. Call ONLY after the link
    write has committed successfully."""
    decoded = _decode_raw(token)
    if decoded:
        _, sig, exp = decoded
        _pending_sigs.pop(sig, None)
        _consumed_sigs[sig] = exp


def release_token(token: str) -> None:
    """Drop a token's reservation so it can be redeemed again. Call on ANY failure
    after verify_link_token so a transient error (DB hiccup, missing user) doesn't
    leave the user with a dead QR."""
    decoded = _decode_raw(token)
    if decoded:
        _pending_sigs.pop(decoded[1], None)


# ── Bot identity (self-registered by the Telegram bot at startup) ──────────────

_bot_username: Optional[str] = None


def set_bot_username(username: Optional[str]) -> None:
    """Record the running bot's @username so deep links can be built. Called by the
    bot via POST /api/system/telegram/register-bot at startup."""
    global _bot_username
    cleaned = (username or "").lstrip("@").strip() or None
    _bot_username = cleaned
    logger.info("telegram bot username registered: %s", cleaned)


def get_bot_username() -> Optional[str]:
    # Env override lets an operator pin it if the bot hasn't registered yet.
    return os.environ.get("ZOE_TELEGRAM_BOT_USERNAME", "").lstrip("@").strip() or _bot_username


def build_deep_link(token: str) -> Optional[str]:
    """`https://t.me/<bot>?start=<token>` — or None if the bot username is unknown."""
    username = get_bot_username()
    if not username:
        return None
    return f"https://t.me/{username}?start={token}"


def make_qr_svg(data: str) -> Optional[str]:
    """Render `data` as a compact inline SVG QR, or None if the QR lib is absent
    (progressive enhancement — the UI falls back to the raw deep link)."""
    try:
        import segno
    except Exception:
        return None
    try:
        import io

        qr = segno.make(data, error="m")
        buf = io.BytesIO()  # segno's SVG serializer writes bytes
        # xmldecl off → a bare <svg> (with xmlns) we can inject into the DOM.
        qr.save(buf, kind="svg", xmldecl=False, scale=6, border=2)
        return buf.getvalue().decode("utf-8")
    except Exception:
        logger.warning("QR render failed for a telegram link", exc_info=True)
        return None
