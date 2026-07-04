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

# Signing secret. Prefer an explicit stable secret so tokens survive a zoe-data
# restart; fall back to the internal token, else a per-process random (tokens are
# short-lived, so a restart simply invalidates any in-flight ones — acceptable).
_LINK_SECRET = (
    os.environ.get("ZOE_TELEGRAM_LINK_SECRET")
    or os.environ.get("ZOE_INTERNAL_TOKEN")
    or secrets.token_hex(32)
).encode()

LINK_TOKEN_TTL_S = 600  # 10 minutes
_SIG_BYTES = 12  # 96-bit truncated HMAC — ample for a 10-minute single-use token


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


def verify_link_token(token: str) -> Optional[str]:
    """Return the user_id if the token is authentic and unexpired, else None."""
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
        if int(exp_s) < int(time.time()):
            return None
        return user_id or None
    except Exception:
        return None


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
