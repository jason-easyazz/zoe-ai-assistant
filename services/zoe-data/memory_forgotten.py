"""Durable forgetting: the ``memory_forgotten`` ledger (memory fidelity audit P2.2, ZMB cell F3).

"Forget Dana" used to be a 300 s in-process tombstone (``memory_tombstones``). The nightly digest
re-reads the day's ``chat_messages`` (which still hold the forgotten turns) hours later, after the
tombstone has expired, and the name came back. This module is the durable half of the fix; the
tombstone stays as the fast path for the in-flight race it was built for.

THE LEDGER MUST NOT RETAIN WHAT IT WAS ASKED TO FORGET. A row holds ``user_id``, a salted hash of the
normalised entity key, ``forgotten_at``, the shield end, ``scope`` and ``actor`` - never the name,
never topic text. It answers "is this forgotten?"; it cannot list what was forgotten.

    key_hash = HMAC-SHA256( user_salt, normalised_key )
    user_salt = HMAC-SHA256( ZOE_FORGET_LEDGER_SALT, "zoe.forgotten.v1|salt|" + user_id )

The secret is ``ZOE_FORGET_LEDGER_SALT`` (the secrets store / the service ``.env``), never in the
table; the per-user salt is derived from it, so one user's hash is not another's and nothing but the
secret is needed to re-derive them. Unset (or shorter than 16 chars) the ledger is UNCONFIGURED:
lookups match nothing, a forget logs a loud warning and the 300 s tombstone is all that remains.
Rotating the secret orphans every existing hash (they stop matching): re-forget what must stay forgotten.

Matching. A candidate text is tokenised into words and every contiguous run of 1..``MAX_KEY_TOKENS``
words is normalised and hashed the same way, then looked up in the user's set - so a name that
only appears inside a longer phrase ("okay so Dana rang about the weekend") is found without ever
storing the phrase. Same anchoring as the tombstone's ``\\bname\\b``: whole words only, case-blind.

Consulted by ``MemoryService.ingest`` (every source except an explicit re-teach by a verified
speaker) and by the digest / idle-consolidation / open-loops transcript loaders (turns naming a
forgotten entity are skipped, not re-mined). An explicit re-teach releases the entry
(``release``) AFTER the store succeeds.

A lookup failure is fail-open on the last-known-good set (a DB blip must never lose a fact); a forget
whose DB write fails still shields this process through the in-process overlay.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Protocol

logger = logging.getLogger(__name__)

SALT_ENV = "ZOE_FORGET_LEDGER_SALT"
SHIELD_DAYS_ENV = "ZOE_FORGOTTEN_SHIELD_DAYS"
#: Owner decision 2026-10-06 ("forgotten means forever"): a forget never expires. The env var
#: is the explicit loosening for a household that wants a bounded shield (days > 0).
DEFAULT_SHIELD_DAYS = 0          # 0 = permanent
PERMANENT_UNTIL = "9999-12-31T23:59:59Z"   # sorts after every real timestamp in the ledger
MIN_SALT_CHARS = 16
#: Longest entity (in words) the ledger can hold and match. Names are 1-4 words; 6 leaves room.
MAX_KEY_TOKENS = 6
SCOPE_ENTITY = "entity"
#: A cached "forgotten set" is trusted this long; any write through this module invalidates it.
_CACHE_TTL_S = 30.0
#: After a failed read, do not hammer the DB per ingest: reuse the last-known-good set this long.
_FAIL_BACKOFF_S = 5.0

#: Writers that are an EXPLICIT teach by the person (voice "remember that ...", the review UI, the brain
#: acting on an explicit "remember ..." turn - ``intent_router`` labels that ``explicit_teach``). Every other
#: writer, ``brain_tool`` paraphrases and digests included, is blocked by an active ledger entry.
EXPLICIT_RETEACH_WRITERS = frozenset({"voice_fact", "review_ui", "explicit_teach"})

_WORD_RE = re.compile(r"\w+", re.UNICODE)

_warned_unconfigured = False


# ── time ─────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    """Wall clock (UTC). One seam, so tests step the shield window without sleeping."""
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def shield_days() -> int:
    """Days a forget shields for; ``0`` means forever (the default). ``ZOE_FORGOTTEN_SHIELD_DAYS``
    with a positive integer bounds it; anything else keeps the permanent default."""
    raw = (os.environ.get("ZOE_FORGOTTEN_SHIELD_DAYS") or "").strip()
    try:
        days = int(raw)
        if days > 0:
            return days
    except ValueError:
        pass
    return DEFAULT_SHIELD_DAYS


def shield_until_for(start: datetime, days: Optional[int]) -> str:
    """The ledger's ``shield_until``: a real instant for a bounded shield, the far-future
    sentinel for a permanent one (so ``shield_until > now`` stays the single lookup rule)."""
    d = days if days else shield_days()
    return PERMANENT_UNTIL if d <= 0 else _iso(start + timedelta(days=d))


# ── the secret and the hash ──────────────────────────────────────────────────

def _master() -> bytes:
    raw = (os.environ.get("ZOE_FORGET_LEDGER_SALT") or "").strip()
    return raw.encode("utf-8") if len(raw) >= MIN_SALT_CHARS else b""


def configured() -> bool:
    """True when the salt secret is set (the ledger can hash, store and match)."""
    return bool(_master())


def _warn_unconfigured() -> None:
    global _warned_unconfigured
    if not _warned_unconfigured:
        _warned_unconfigured = True
        logger.warning(
            "memory_forgotten: %s is unset (or under %d chars) - the durable forget ledger is OFF; "
            "only the 300 s tombstone shields a forgotten name until the secret is configured",
            SALT_ENV, MIN_SALT_CHARS)


def _user_salt(user_id: str) -> bytes:
    return hmac.new(_master(), b"zoe.forgotten.v1|salt|" + user_id.encode("utf-8"), hashlib.sha256).digest()


def normalise_key(name: str) -> str:
    """The canonical entity key: NFKC, case-folded words joined by one space (at most ``MAX_KEY_TOKENS``)."""
    text = unicodedata.normalize("NFKC", str(name or "")).casefold()
    return " ".join(_WORD_RE.findall(text)[:MAX_KEY_TOKENS])


class _Hasher:
    """One user's HMAC state, built once per call (``copy()`` per candidate is far cheaper than re-keying)."""

    def __init__(self, user_id: str):
        self._base = hmac.new(_user_salt(user_id), digestmod=hashlib.sha256)

    def hash(self, key_norm: str) -> str:
        h = self._base.copy()
        h.update(key_norm.encode("utf-8"))
        return h.hexdigest()


def key_hash(user_id: str, name: str) -> str:
    """The ledger key of an entity name for a user ('' when unconfigured or the name has no words)."""
    key = normalise_key(name)
    if not key or not user_id or not configured():
        return ""
    return _Hasher(user_id).hash(key)


def candidate_hashes(user_id: str, text: str, *, wanted: Optional[frozenset[str]] = None) -> set[str]:
    """The hash of every 1..MAX_KEY_TOKENS-word run of ``text`` (whole words, case-blind). With
    ``wanted`` set, only the hashes that are in it are returned (the match)."""
    if not user_id or not text or not configured():
        return set()
    words = _WORD_RE.findall(unicodedata.normalize("NFKC", str(text)).casefold())
    if not words:
        return set()
    hasher = _Hasher(user_id)
    out: set[str] = set()
    for i in range(len(words)):
        for n in range(1, MAX_KEY_TOKENS + 1):
            if i + n > len(words):
                break
            digest = hasher.hash(" ".join(words[i:i + n]))
            if wanted is None or digest in wanted:
                out.add(digest)
    return out


# ── storage ──────────────────────────────────────────────────────────────────

class Backend(Protocol):
    async def upsert(self, user_id: str, key_hash: str, *, scope: str, actor: str,
                     forgotten_at: str, shield_until: str) -> None: ...

    async def active_hashes(self, user_id: str, now_iso: str) -> set[str]: ...

    async def delete(self, user_id: str, key_hashes: Iterable[str]) -> int: ...


class MemoryBackend:
    """In-process ledger (tests, the benchmark lab). Same contract as the Postgres one; ``rows`` is exposed so a
    test can assert the table holds no text."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, str]] = {}

    async def upsert(self, user_id, key_hash, *, scope, actor, forgotten_at, shield_until):
        self.rows[(user_id, key_hash)] = {
            "user_id": user_id, "key_hash": key_hash, "scope": scope, "actor": actor,
            "forgotten_at": forgotten_at, "shield_until": shield_until}

    async def active_hashes(self, user_id, now_iso):
        return {h for (u, h), r in self.rows.items() if u == user_id and r["shield_until"] > now_iso}

    async def delete(self, user_id, key_hashes):
        n = 0
        for h in list(key_hashes):
            if self.rows.pop((user_id, h), None) is not None:
                n += 1
        return n


class PostgresBackend:
    """The ``memory_forgotten`` table (alembic 0038) over ``db_pool``. Portable SQL (``?`` placeholders,
    ``ON CONFLICT``), so the test suite runs these exact statements on SQLite."""

    @staticmethod
    def _ctx():
        from db_pool import get_db_ctx  # type: ignore[import]
        return get_db_ctx()

    async def upsert(self, user_id, key_hash, *, scope, actor, forgotten_at, shield_until):
        async with self._ctx() as db:
            await db.execute(
                """INSERT INTO memory_forgotten
                       (user_id, key_hash, scope, actor, forgotten_at, shield_until)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (user_id, key_hash) DO UPDATE
                   SET scope = excluded.scope, actor = excluded.actor,
                       forgotten_at = excluded.forgotten_at, shield_until = excluded.shield_until""",
                (user_id, key_hash, scope, actor, forgotten_at, shield_until))
            await db.commit()

    async def active_hashes(self, user_id, now_iso):
        async with self._ctx() as db:
            cur = await db.execute(
                "SELECT key_hash FROM memory_forgotten WHERE user_id = ? AND shield_until > ?",
                (user_id, now_iso))
            return {str(r[0]) for r in await cur.fetchall()}

    async def delete(self, user_id, key_hashes):
        hashes = list(key_hashes)
        if not hashes:
            return 0
        marks = ", ".join("?" for _ in hashes)
        async with self._ctx() as db:
            cur = await db.execute(
                f"DELETE FROM memory_forgotten WHERE user_id = ? AND key_hash IN ({marks})",
                (user_id, *hashes))
            await db.commit()
            return int(getattr(cur, "rowcount", 0) or 0)


_backend: Optional[Backend] = None


def get_backend() -> Backend:
    global _backend
    if _backend is None:
        _backend = PostgresBackend()
    return _backend


def set_backend(backend: Optional[Backend]) -> None:
    """Swap the store (tests / lab); ``None`` restores the Postgres default. Drops every cache."""
    global _backend
    _backend = backend
    reset_state()


# ── the in-process view: cache + overlay of this process's own writes ────────

# {user_id: (trusted_until_monotonic, frozenset(hashes))}
_cache: dict[str, tuple[float, frozenset[str]]] = {}
# {user_id: {key_hash: shield_until_iso}}: forgets this process made. Shields the process even when the DB
# write failed, and is dropped for a hash once the DB confirms or the entry is released.
_overlay: dict[str, dict[str, str]] = {}
# {user_id: monotonic time before which a failed read is not retried}
_fail_until: dict[str, float] = {}
_gen = 0


def reset_state() -> None:
    """Drop every in-process view (tests, ``set_backend``)."""
    global _gen, _warned_unconfigured
    _gen += 1
    _cache.clear()
    _overlay.clear()
    _fail_until.clear()
    _warned_unconfigured = False


def _invalidate(user_id: str) -> None:
    global _gen
    _gen += 1
    _cache.pop(user_id, None)
    _fail_until.pop(user_id, None)


async def active_hashes(user_id: str) -> frozenset[str]:
    """The user's active forgotten-entity hashes (cached ``_CACHE_TTL_S``; DB failure -> last known set)."""
    now = time.monotonic()
    hit = _cache.get(user_id)
    mem = frozenset(h for h, until in _overlay.get(user_id, {}).items() if until > _iso(_now()))
    if hit is not None and hit[0] > now:
        return hit[1] | mem
    if _fail_until.get(user_id, 0.0) > now:
        return (hit[1] if hit else frozenset()) | mem
    gen = _gen
    try:
        fresh = frozenset(await get_backend().active_hashes(user_id, _iso(_now())))
    except Exception as exc:  # noqa: BLE001 - a lookup failure must never lose a fact
        _fail_until[user_id] = time.monotonic() + _FAIL_BACKOFF_S
        logger.debug("memory_forgotten: lookup failed (%s) - using the last known set", type(exc).__name__)
        return (hit[1] if hit else frozenset()) | mem
    if _gen == gen:  # nothing was written while we were reading
        _cache[user_id] = (time.monotonic() + _CACHE_TTL_S, fresh)
        # the DB has the overlay's rows now: it no longer needs to carry them
        ov = _overlay.get(user_id)
        if ov:
            for h in list(ov):
                if h in fresh:
                    del ov[h]
            if not ov:
                _overlay.pop(user_id, None)
    return fresh | mem


# ── the API ──────────────────────────────────────────────────────────────────

async def add(user_id: str, name: str, *, actor: str = "", scope: str = SCOPE_ENTITY,
              shield_for_days: Optional[int] = None) -> bool:
    """Record that ``name`` was forgotten for ``user_id``. Stores only the salted hash. True when the entry
    is durable (the DB write succeeded); False when unconfigured, empty, or the DB write failed (the
    process is still shielded through the overlay in the last case)."""
    key = normalise_key(name)
    if not user_id or not key:
        return False
    if not configured():
        _warn_unconfigured()
        return False
    digest = _Hasher(user_id).hash(key)
    start = _now()
    until = shield_until_for(start, shield_for_days)
    _overlay.setdefault(user_id, {})[digest] = until
    _invalidate(user_id)
    try:
        await get_backend().upsert(
            user_id, digest, scope=scope, actor=actor or user_id,
            forgotten_at=_iso(start), shield_until=until)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_forgotten: could not persist a forget for user=%s (%s) - shielded in-process only",
                       user_id, type(exc).__name__)
        return False
    _invalidate(user_id)
    logger.info("memory_forgotten: recorded a forgotten entity for user=%s scope=%s (hash only)", user_id, scope)
    return True


async def matches(user_id: str, text: str) -> bool:
    """Does ``text`` mention an entity this user has forgotten (a whole word / phrase, case-blind)?"""
    if not user_id or not text or not configured():
        return False
    wanted = await active_hashes(user_id)
    if not wanted:
        return False
    return bool(candidate_hashes(user_id, text, wanted=wanted))


async def keep_unforgotten(user_id: str, items: Iterable[Any], *,
                           text_of: Any = str) -> tuple[list[Any], int]:
    """``items`` without the ones whose text (``text_of(item)``) names a forgotten entity, and how many were
    dropped. Used by the transcript loaders (nightly digest, idle consolidation, open loops): a turn that names
    a forgotten entity is skipped, not re-mined."""
    rows = list(items)
    if not rows or not configured() or not user_id:
        return rows, 0
    wanted = await active_hashes(user_id)
    if not wanted:
        return rows, 0
    kept = [r for r in rows if not candidate_hashes(user_id, text_of(r) or "", wanted=wanted)]
    return kept, len(rows) - len(kept)


async def release(user_id: str, text: str) -> int:
    """An explicit re-teach by the person: drop every active entry whose entity ``text`` names. Returns how many
    were released. Called AFTER the store succeeded (a failed re-teach must keep the shield)."""
    if not user_id or not text or not configured():
        return 0
    wanted = await active_hashes(user_id)
    hit = candidate_hashes(user_id, text, wanted=wanted) if wanted else set()
    if not hit:
        return 0
    ov = _overlay.get(user_id)
    if ov:
        for h in hit:
            ov.pop(h, None)
    _invalidate(user_id)
    try:
        await get_backend().delete(user_id, hit)
    except Exception as exc:  # noqa: BLE001
        # keep the entry shielded rather than half-released: re-add it to the overlay
        for h in hit:
            _overlay.setdefault(user_id, {})[h] = _iso(_now() + timedelta(days=shield_days()))
        logger.warning("memory_forgotten: release failed for user=%s (%s) - the entry stays", user_id,
                       type(exc).__name__)
        return 0
    _invalidate(user_id)
    logger.info("memory_forgotten: released %d entr%s for user=%s (explicit re-teach)", len(hit),
                "y" if len(hit) == 1 else "ies", user_id)
    return len(hit)


def is_explicit_reteach(writer: str, *, user_id: str = "", speaker_verified: Optional[bool] = None) -> bool:
    """May this write bypass the ledger? Only the person's own explicit teach (an explicit writer, in the user
    classes) from a speaker the lane did not reject (``speaker_verified`` is not False)."""
    if speaker_verified is False:
        return False
    w = (writer or "").strip()
    if w not in EXPLICIT_RETEACH_WRITERS:
        return False
    try:
        import memory_authority as _auth
        return _auth.RANK[_auth.writer_class(w, user_id=user_id)] >= _auth.USER_RANK
    except Exception:  # noqa: BLE001 - classification failing means "not provably the user's own"
        return False
