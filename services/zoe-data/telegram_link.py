"""Self-service Telegram account linking — stateless signed link tokens + QR.

Flow:
  1. An authenticated user asks for a link token
     (POST /api/user/profile/telegram/link-token). We mint a short-lived,
     HMAC-signed token that ENCODES their user_id + expiry (nothing stored at mint).
  2. The UI shows a Telegram deep link (`https://t.me/<bot>?start=<token>`) and a
     QR of it. The user opens/scans it; Telegram sends the bot `/start <token>`.
  3. The bot forwards the token + the VERIFIED sender id to zoe-data
     (POST /api/system/telegram/consume-link-token, internal-only). We verify the
     signature + expiry, recover the user_id, and ``link`` records the redemption and
     stores the telegram_id on that user's profile in ONE transaction.

Security: the token proves "the person who generated this in an authenticated Zoe
session wants to link whatever Telegram account redeems it." The sender's numeric
id is supplied by Telegram to the bot (not by the user), and the consume endpoint
is internal-only — so a leaked token can at worst link the leaker's OWN Telegram
account to the generating user, which is the same trust the deep link already
implies. Tokens are short-lived, cannot be forged without the secret, and are
single-use DURABLY (``telegram_link_redemptions``, alembic 0084: a database row, so it
holds across restarts and workers); a token issued before a member erase is void.

The link store (this module is its one writer; ``user_preferences.prefs``):
  * ``telegram_id``          the linked account (read by the resolver and the outbound senders);
  * ``telegram_link_proof``  how and when it was proven (``via``, ``at``, the Telegram username shown in Settings);
  * ``telegram_link_change`` the last explicit unlink (``event``, ``at``, ``by``: "self" | "admin" | "forget").
A link is written ONLY by ``link`` (a redeemed token), never displaces another member's link and never moves a member's own
link to another account (unlink first), and is removed only by ``unlink`` (the member or an admin) or ``forget_user`` (the
member erase). One holder per account is a database guarantee (``uq_user_preferences_telegram_id``, alembic 0084). ``resolve`` answers only for an
unambiguous link held by an active account.
"""

import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

import auth_handoff

logger = logging.getLogger(__name__)

# Signing secret, PURPOSE-SPECIFIC to link tokens. Set ZOE_TELEGRAM_LINK_SECRET
# (recommended) so tokens survive a zoe-data restart and rotate independently of
# other credentials; otherwise a per-process random is used (tokens are short-
# lived, so a restart just invalidates any in-flight ones — acceptable). Either
# way single use is the database's, not this process's. We do NOT
# reuse ZOE_INTERNAL_TOKEN here: mixing an internal-auth credential with a token-
# signing key couples their rotation policies.
_LINK_SECRET = (
    os.environ.get("ZOE_TELEGRAM_LINK_SECRET") or secrets.token_hex(32)
).encode()

LINK_TOKEN_TTL_S = 600  # 10 minutes
_SIG_BYTES = 12  # 96-bit truncated HMAC — ample for a 10-minute single-use token

# Single use is DURABLE: a redeemed token's id is a row in ``telegram_link_redemptions`` (alembic 0084), written by ``link`` in the
# same transaction as the link itself, so it holds across restarts and workers until the token expires.
REDEMPTIONS = "telegram_link_redemptions"
#: the unique index that keeps one holder per messaging id (alembic 0084)
TID_INDEX = "uq_user_preferences_telegram_id"


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
    return auth_handoff.b64(payload + sig)


def _decode_raw(token: str):
    """Return (user_id, sig, exp) for an authentic, unexpired token, else None. Signature + expiry only; no side effect."""
    if not token or len(token) > 128:
        return None
    try:
        raw = auth_handoff.b64d(token)
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


def decode_link_token(token: str) -> Optional[tuple[str, str, int]]:
    """``(user_id, token_id, expires_at)`` for an authentic, unexpired token, else None. No side effect: whether it was already
    redeemed is the database's answer, given by ``link``. ``token_id`` is a hash of the signature (never the token itself)."""
    decoded = _decode_raw(token)
    if not decoded or not decoded[0]:
        return None
    user_id, sig, exp = decoded
    return user_id, hashlib.sha256(b"telegram-link:" + sig).hexdigest()[:32], exp


def verify_link_token(token: str) -> Optional[str]:
    """The member a token was minted for, if it is authentic and unexpired. Says nothing about single use (``link`` does)."""
    decoded = decode_link_token(token)
    return decoded[0] if decoded else None


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


# ── The link store (see the module doc) ─────────────────────────────────────────

KEY_ID = "telegram_id"
KEY_PROOF = "telegram_link_proof"
KEY_CHANGE = "telegram_link_change"
LINKED, ALREADY_LINKED, HELD_BY_OTHER = "linked", "already_linked", "held_by_other"
MEMBER_LINKED, TOKEN_SPENT, MEMBER_GONE = "member_linked", "token_spent", "member_gone"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _holders(db, tid: str) -> list[str]:
    cursor = await db.execute(
        """SELECT user_id, prefs FROM user_preferences
           WHERE prefs::jsonb ->> 'telegram_id' = ?
           ORDER BY updated_at ASC""",
        (tid,),
    )
    return [row["user_id"] for row in await cursor.fetchall()]


class _Watermark:
    """``erase_watermark``'s store, read on the redemption's OWN connection (inside its transaction)."""

    def __init__(self, db):
        self._db = db

    async def get(self, user_id: str) -> Optional[str]:
        import erase_watermark

        cursor = await self._db.execute(
            "SELECT forgotten_at FROM memory_forgotten WHERE user_id = ? AND key_hash = ?", (user_id, erase_watermark.KEY))
        row = await cursor.fetchone()
        return str(row["forgotten_at"]) if row and row["forgotten_at"] else None


async def _member_lock(db, user_id: str) -> None:
    """The member's link lock, held to the end of the current transaction. ``link`` and ``forget_user`` both take it, so a
    redemption and a member erase are ordered by the database: whichever commits second sees what the first did."""
    await db.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (f"telegram_link:{user_id}",))


async def _issued_before_erase(db, user_id: str, expires_at: float) -> bool:
    """True when the member was erased at or after this token was issued (``erase_watermark``: durable, recorded by
    ``MemoryService.delete_user`` before it erases anything), so the token is void. Raises when the mark cannot be read."""
    import erase_watermark

    mark = await erase_watermark.watermark(user_id, backend=_Watermark(db))
    return mark is not None and float(expires_at) - LINK_TOKEN_TTL_S <= mark.timestamp()


class _Refused(Exception):
    """Inside ``link``'s transaction: refuse, and roll back the redemption row with everything else."""

    def __init__(self, outcome: str):
        super().__init__(outcome)
        self.outcome = outcome


def _is_tid_conflict(exc: BaseException) -> bool:
    """The one-holder index refused the write (a concurrent redemption linked this account to another member first)."""
    if getattr(exc, "sqlstate", None) != "23505":
        return False
    name = getattr(exc, "constraint_name", None)
    return name is None or name == TID_INDEX


async def link(db, user_id: str, tid: str, username: Optional[str] = None, *, token_id: str, expires_at: float) -> str:
    """Redeem the token ``token_id`` (minted for ``user_id``, expiring at ``expires_at``) from the account ``tid``, and link them.

    ONE transaction: expired redemption rows are swept, the redemption is recorded (its primary key makes a token single-use for
    good), the holders are checked and the link is written. Returns ``LINKED`` / ``ALREADY_LINKED`` (committed), or a refusal with
    nothing kept, not even the redemption, so the member can scan again once the cause is cleared:
      * ``TOKEN_SPENT``   - this token was already redeemed;
      * ``HELD_BY_OTHER`` - another member holds ``tid`` (the one-holder index also refuses a concurrent second holder);
      * ``MEMBER_LINKED`` - ``user_id`` already holds a DIFFERENT account. A redemption never moves a link: the member (or an admin)
                            unlinks first, then links the new account;
      * ``MEMBER_GONE``   - the member no longer exists, or was erased after this token was issued (the token is void for good).
    The member's link lock (``_member_lock``) is held throughout, so a member erase never interleaves with a redemption.
    """
    from user_prefs import read_prefs, set_prefs

    proof = {"via": "signed_link", "at": _now_iso()}
    cleaned = (username or "").lstrip("@").strip()[:64]
    if cleaned:
        proof["username"] = cleaned
    now = time.time()
    try:
        async with db.transaction():
            await _member_lock(db, user_id)
            cursor = await db.execute("SELECT 1 FROM users WHERE id = ? LIMIT 1", (user_id,))
            if not await cursor.fetchone() or await _issued_before_erase(db, user_id, expires_at):
                raise _Refused(MEMBER_GONE)
            await db.execute(f"DELETE FROM {REDEMPTIONS} WHERE expires_at < ?", (now,))
            cursor = await db.execute(
                f"""INSERT INTO {REDEMPTIONS} (token_id, user_id, expires_at, redeemed_at) VALUES (?, ?, ?, ?)
                    ON CONFLICT (token_id) DO NOTHING RETURNING token_id""",
                (token_id, user_id, float(expires_at), now),
            )
            if not await cursor.fetchone():
                raise _Refused(TOKEN_SPENT)
            if any(h != user_id for h in await _holders(db, tid)):
                raise _Refused(HELD_BY_OTHER)
            current = (await read_prefs(db, user_id)).get(KEY_ID)
            if current and str(current) != tid:
                raise _Refused(MEMBER_LINKED)
            if not await set_prefs(user_id, {KEY_ID: tid, KEY_PROOF: proof}, db=db, unless_held=(KEY_ID, tid)):
                raise _Refused(MEMBER_LINKED)      # a concurrent redemption linked this member to another account first
    except _Refused as refused:
        logger.warning("telegram link refused (%s) for user %s", refused.outcome, user_id)
        return refused.outcome
    except Exception as exc:  # noqa: BLE001 - only the one-holder index is a refusal; anything else propagates
        if _is_tid_conflict(exc):
            logger.warning("telegram link refused (%s, concurrent) for user %s", HELD_BY_OTHER, user_id)
            return HELD_BY_OTHER
        raise
    logger.info("telegram link proven for user %s", user_id)
    return ALREADY_LINKED if current else LINKED


async def unlink(db, user_id: str, *, by: str) -> bool:
    """Remove ``user_id``'s link and record who did it (``by``: "self" | "admin"). Returns whether one existed."""
    from user_prefs import delete_prefs, read_prefs, set_prefs

    had = bool((await read_prefs(db, user_id)).get(KEY_ID))
    await delete_prefs(user_id, (KEY_ID, KEY_PROOF), db=db)
    if had:
        await set_prefs(user_id, {KEY_CHANGE: {"event": "unlinked", "at": _now_iso(), "by": by}}, db=db)
        logger.warning("telegram link removed for user %s (by %s)", user_id, by)
    return had


async def forget_user(user_id: str) -> int:
    """Member erase (``MemoryService.delete_user``): remove the link, its records and the member's redemption rows. Returns 1 when a
    link existed. Raises on a DB error (the caller fails closed).

    Under the member's link lock, in one transaction: a redemption in flight commits first and its link is removed here; one that
    starts after waits, then finds the erase watermark (``delete_user`` records it before any eraser runs) and is refused. Tokens
    issued before the erase stay void after it for the same reason."""
    from db_pool import get_db_ctx
    from user_prefs import delete_prefs, read_prefs

    uid = str(user_id or "").strip()
    if not uid:
        return 0
    try:
        async with get_db_ctx() as db:
            async with db.transaction():
                await _member_lock(db, uid)
                had = bool((await read_prefs(db, uid)).get(KEY_ID))
                await delete_prefs(uid, (KEY_ID, KEY_PROOF, KEY_CHANGE), db=db)
            await db.execute(f"DELETE FROM {REDEMPTIONS} WHERE user_id = ?", (uid,))
    except Exception as exc:  # noqa: BLE001
        from conversation_notes import _no_store       # the one "nothing stored to erase" rule (a lost pool still raises)

        if _no_store(exc):
            return 0
        logger.error("telegram link forget failed (%s): link NOT erased", type(exc).__name__)
        raise
    return int(had)


async def _account_active(db, user_id: str) -> bool:
    """True only for a registered, active account (``auth_users``). Raises on a failed lookup (the caller refuses)."""
    cursor = await db.execute("SELECT is_active FROM auth_users WHERE user_id = ? LIMIT 1", (user_id,))
    row = await cursor.fetchone()
    if not row:
        return False
    active = row["is_active"]
    return active is None or bool(int(active))


async def resolve(db, tid: str) -> Optional[str]:
    """The member ``tid`` is linked to, or None: no link, a link more than one member holds (ambiguous, refused), or a link
    whose member is no longer an active account (deleted or deactivated). Fails closed on a lookup error."""
    try:
        holders = await _holders(db, tid)
        if len(holders) != 1:
            if holders:
                logger.warning("telegram resolve refused: %d members hold one account", len(holders))
            return None
        return holders[0] if await _account_active(db, holders[0]) else None
    except Exception as exc:  # noqa: BLE001 - not knowing who someone is means acting for nobody
        logger.warning("telegram resolve failed closed: %s", type(exc).__name__)
        return None
