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

NEAR SPELLINGS. ``add`` also stores hashed edit-distance probes of the name (``forget_match``: <= 2 edits for 7+ codepoints, 1 for
5-6, none for shorter; a split spelling one less; accents folded; any script), scope ``near``, plus a presence marker. The write
guards (``MemoryService.ingest``, the transcript loaders) pass ``near=True``: a text that names a near spelling is HELD OUT (not
stored, not mined) and the owner is asked "did you also mean ...?" (``memory_forget_alias``); nothing already stored is ever
erased on a near match. An answered "no" records the spelling as ``distinct`` and it passes. Reads (hiding recalled rows) stay exact.

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

import forget_match as fm

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
#: A spelling the owner CONFIRMED is the same forgotten name (``memory_forget_alias``): forgotten through the same path,
#: labelled so the audit can tell "I forgot Dana" from "I also meant Dayna".
SCOPE_ALIAS = "alias"
SCOPE_NEAR = "near"          # a hashed edit-distance probe of a forgotten name (never a name)
SCOPE_DISTINCT = "distinct"  # a spelling the owner said is someone else: passes the near guard
NEAR_ENV = "ZOE_FORGET_NEAR"
#: Longest joined name (codepoints) that gets near probes (<= 211 rows); longer names are matched exactly only.
MAX_NEAR_LEN = 20
_NEAR_FLAG = "nearflag"      # hashed marker row: this user has near probes (so users without them pay nothing)
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


def near_enabled() -> bool:
    """Near-spelling probes on (default) unless ``ZOE_FORGET_NEAR`` is 0 / off."""
    return (os.environ.get("ZOE_FORGET_NEAR") or "").strip().lower() not in ("0", "off", "false", "no", "disabled")


def normalise_key(name: str) -> str:
    """The canonical entity key: NFKC, case-folded words joined by one space (at most ``MAX_KEY_TOKENS``)."""
    text = unicodedata.normalize("NFKC", str(name or "")).casefold()
    return " ".join(_WORD_RE.findall(text)[:MAX_KEY_TOKENS])


def name_pattern(name: str) -> "re.Pattern[str]":
    """The whole-word, case-blind regex for a forgotten name, agreeing with the ledger's tokens: the name's words are joined by ANY run of
    non-word characters ("Mari sol" finds "Mari-sol", as the ledger already hashes them alike). No word characters: the literal."""
    words = _WORD_RE.findall(unicodedata.normalize("NFKC", str(name or "")).strip())
    if not words:
        return re.compile(r"\b" + re.escape(str(name or "").strip()) + r"\b", re.IGNORECASE)
    # a name in a script written without spaces has no word boundary inside a run of text: no \b on that side
    head = "" if fm.is_unspaced(words[0][0]) else r"\b"
    tail = "" if fm.is_unspaced(words[-1][-1]) else r"\b"
    return re.compile(head + r"[\W_]+".join(re.escape(w) for w in words) + tail, re.IGNORECASE)


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


# ── storage ──────────────────────────────────────────────────────────────────

class Backend(Protocol):
    async def upsert(self, user_id: str, key_hash: str, *, scope: str, actor: str,
                     forgotten_at: str, shield_until: str) -> None: ...

    async def active_hashes(self, user_id: str, now_iso: str) -> set[str]: ...

    async def delete(self, user_id: str, key_hashes: Iterable[str]) -> int: ...

    async def users(self) -> list[str]: ...        # every user with an active entry (maintenance: redacting old copies)


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

    async def users(self):
        return sorted({u for (u, _h), r in self.rows.items() if r["shield_until"] > _iso(_now())})

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

    async def users(self):
        async with self._ctx() as db:
            cur = await db.execute("SELECT DISTINCT user_id FROM memory_forgotten WHERE shield_until > ?", (_iso(_now()),))
            return sorted(str(r[0]) for r in await cur.fetchall())

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
    await _add_near(user_id, name, start=start, until=until, actor=actor or user_id)
    logger.info("memory_forgotten: recorded a forgotten entity for user=%s scope=%s (hash only)", user_id, scope)
    return True


async def _put(user_id: str, digest: str, *, scope: str, actor: str, start: datetime, until: str) -> None:
    _overlay.setdefault(user_id, {})[digest] = until
    await get_backend().upsert(user_id, digest, scope=scope, actor=actor, forgotten_at=_iso(start), shield_until=until)


def near_hashes(user_id: str, name: str) -> list[str]:
    """The hashed edit-distance probes of ``name`` (see ``forget_match``) - none when the name is too short to allow an edit."""
    joined = fm.joined_key(name)
    if len(joined) > MAX_NEAR_LEN:      # the probe count grows with the square of the length: a longer name keeps its exact entry only
        return []
    k = fm.max_edits(joined)
    h = _Hasher(user_id).hash
    return [h(f"near{k}|{key}") for key in fm.probe_keys(joined, k)] if joined else []


async def _add_near(user_id: str, name: str, *, start: datetime, until: str, actor: str) -> None:
    """Store the near probes of ``name`` (best effort: the exact entry above is the forget; these widen what it catches)."""
    if not near_enabled() or not fm.joined_key(name):
        return
    try:
        for digest in near_hashes(user_id, name) + [_Hasher(user_id).hash(_NEAR_FLAG)]:
            await _put(user_id, digest, scope=SCOPE_NEAR, actor=actor, start=start, until=until)
        _invalidate(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_forgotten: could not persist the near probes for user=%s (%s)", user_id, type(exc).__name__)


async def add_distinct(user_id: str, spelling: str) -> bool:
    """The owner said ``spelling`` is NOT a forgotten name (they declined "did you also mean ...?"): it passes the near guard
    from now on. Stores the hash of the spelling, permanently."""
    key = fm.joined_key(spelling)
    if not user_id or not key or not configured():
        return False
    try:
        await _put(user_id, _Hasher(user_id).hash("distinct|" + key), scope=SCOPE_DISTINCT, actor=user_id,
                   start=_now(), until=PERMANENT_UNTIL)
        _invalidate(user_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory_forgotten: could not persist a distinct spelling for user=%s (%s)", user_id, type(exc).__name__)
        return False


def spans_in(user_id: str, text: str, wanted: "frozenset[str]", *, near: bool) -> "tuple[list[tuple[int, int]], list[tuple[int, int]]]":
    """``(exact spans, near-only spans)`` of ``text`` against the user's forgotten set. Near spans are only computed when asked,
    when the user has near probes, and they exclude a spelling the owner declared ``distinct`` and anything an exact span covers."""
    h = _Hasher(user_id).hash
    exact = fm.merge(fm.exact_spans(text, h, wanted, max_tokens=MAX_KEY_TOKENS))
    if not (near and near_enabled() and h(_NEAR_FLAG) in wanted):
        return exact, []
    out = []
    for s, e in fm.merge(fm.near_spans(text, h, wanted)):
        if any(s < xe and xs < e for xs, xe in exact):
            continue
        if h("distinct|" + fm.joined_key(text[s:e])) in wanted:
            continue
        out.append((s, e))
    return exact, out


async def spans(user_id: str, text: str, *, near: bool = True) -> "list[tuple[int, int]]":
    """Every span of ``text`` that names a forgotten entity (exact, and with ``near`` the near spellings), merged. What a
    redaction replaces; consults the ledger only (no name is needed)."""
    if not user_id or not text or not configured():
        return []
    wanted = await active_hashes(user_id)
    if not wanted:
        return []
    exact, nr = spans_in(user_id, text, wanted, near=near)
    return fm.merge(exact + nr)


async def matches(user_id: str, text: str, *, near: bool = False) -> bool:
    """Does ``text`` mention an entity this user has forgotten (a whole word / phrase, case-blind)? With ``near`` (the write
    guards) a near spelling of one counts too - and the owner is asked about it (``memory_forget_alias``)."""
    if not user_id or not text or not configured():
        return False
    wanted = await active_hashes(user_id)
    if not wanted:
        return False
    exact, nr = spans_in(user_id, text, wanted, near=near)
    if nr:
        await _ask_about(user_id, text, nr)
    return bool(exact or nr)


async def _ask_about(user_id: str, text: str, nr: "list[tuple[int, int]]") -> None:
    """Queue "did you also mean ...?" for the near spellings that held a write out (best effort, never raises, never a name)."""
    try:
        import memory_forget_alias
        await memory_forget_alias.queue_spellings(user_id, [text[s:e] for s, e in nr])
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory_forgotten: could not queue the near-spelling question (%s)", type(exc).__name__)


async def keep_unforgotten(user_id: str, items: Iterable[Any], *,
                           text_of: Any = str, near: bool = True) -> tuple[list[Any], int]:
    """``items`` without the ones whose text (``text_of(item)``) names a forgotten entity - or a near spelling of one - and how many
    were dropped. Used by the transcript loaders (nightly digest, idle consolidation, open loops): a turn that names a forgotten
    entity is skipped, not re-mined."""
    rows = list(items)
    if not rows or not configured() or not user_id:
        return rows, 0
    wanted = await active_hashes(user_id)
    if not wanted:
        return rows, 0
    kept = []
    for r in rows:
        text = text_of(r) or ""
        exact, nr = spans_in(user_id, text, wanted, near=near)
        if nr:
            await _ask_about(user_id, text, nr)
        if not (exact or nr):
            kept.append(r)
    return kept, len(rows) - len(kept)


async def release(user_id: str, text: str) -> int:
    """An explicit re-teach by the person: drop every active entry whose entity ``text`` names (and that entity's near probes).
    Returns how many entities were released. Called AFTER the store succeeded (a failed re-teach must keep the shield)."""
    if not user_id or not text or not configured():
        return 0
    wanted = await active_hashes(user_id)
    h = _Hasher(user_id).hash
    exact = fm.merge(fm.exact_spans(text, h, wanted, max_tokens=MAX_KEY_TOKENS)) if wanted else []
    hit = {h(normalise_key(text[s:e])) for s, e in exact} & set(wanted)
    if not hit:
        return 0
    drop = set(hit)
    for s, e in exact:
        drop.update(x for x in near_hashes(user_id, text[s:e]) if x in wanted)
    ov = _overlay.get(user_id)
    if ov:
        for d in drop:
            ov.pop(d, None)
    _invalidate(user_id)
    try:
        await get_backend().delete(user_id, drop)
    except Exception as exc:  # noqa: BLE001
        # keep the entry shielded rather than half-released: re-add it to the overlay
        for d in drop:
            _overlay.setdefault(user_id, {})[d] = _iso(_now() + timedelta(days=shield_days()))
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
