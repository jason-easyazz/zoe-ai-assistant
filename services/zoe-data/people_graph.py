"""The people graph's invariants in one place (docs/adr/ADR-relationship-memory.md, "Invariants"). Stdlib only.

1. Resolution (``resolve_person``): exact name (case / accents / punctuation folded, script-agnostic), then a whole
   token, then a prefix; the first tier with a hit decides; two or more hits in it are ``ambiguous`` and come back
   with the candidates (the shape ``ask_when_ambiguous`` reads) - never "whichever row came first". No SQL ``LIKE`` is
   built from the user's words (``%`` / ``_`` are plain characters); the read is one indexed fetch of the user's live
   people (``people_user_live_idx``, migration 0043) folded in Python, because accent / CJK folding is not a B-tree job.
2. Atomic change (``replace_current_edge``): close-old + insert-new in ONE transaction under a per-(user, pair)
   advisory lock - no crash, failed insert or concurrent writer leaves zero or two current edges.
3. Invalidate, never delete (``close_edge``): an edge gets ``valid_to`` + ``close_reason`` (superseded | user_removed |
   user_edited | merged_duplicate | merged_self_edge | corrected_pet | corrected_pet_duplicate | forgotten).
4. Temporal read (``edges_for_person``): current edges by default, ``as_of`` an instant, or ``history``. Timestamps
   are written in ONE form (``now_iso``) and read through ``parse_ts`` (the columns also hold ``NOW()::text``).
5. Evidence (``Evidence``): ``turn_id`` (the content id memory rows use), ``quote_span`` (``start:end:hash`` - a pointer,
   never the words) and ``speaker_rank`` (``memory_authority.RANK``). NULL on rows older than migration 0043.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# -- time ---------------------------------------------------------------------------------------------


def now_iso(now: Optional[datetime] = None) -> str:
    """The one text form edge timestamps are written in: UTC, microseconds, ``Z`` (``isoformat()`` drops a zero
    fraction, which is how the column came to hold two widths)."""
    n = now or datetime.now(timezone.utc)
    if n.tzinfo is not None:
        n = n.astimezone(timezone.utc).replace(tzinfo=None)
    return n.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


_TS_RE = re.compile(
    r"^\s*(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})(?:[T ](?P<h>\d{2}):(?P<mi>\d{2})(?::(?P<s>\d{2})(?:[.,](?P<f>\d{1,9}))?)?)?"
    r"\s*(?P<tz>Z|z|[+-]\d{2}(?::?\d{2})?)?\s*$")


def parse_ts(value: Any) -> Optional[datetime]:
    """A UTC-aware datetime from any form the tables hold (``...T..Z`` with / without fraction, ``NOW()::text``
    ``... ...+00``, a bare date, a datetime, an epoch); naive = UTC; ``None`` when empty or unreadable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    m = _TS_RE.match(str(value))
    if not m:
        return None
    try:
        dt = datetime(int(m["y"]), int(m["mo"]), int(m["d"]), int(m["h"] or 0), int(m["mi"] or 0), int(m["s"] or 0),
                      int((m["f"] or "0")[:6].ljust(6, "0")), tzinfo=timezone.utc)
        tz = m["tz"]
        if tz and tz not in ("Z", "z"):
            digits = tz[1:].replace(":", "")
            dt -= (-1 if tz[0] == "-" else 1) * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0))
        return dt
    except ValueError:
        return None


# -- names --------------------------------------------------------------------------------------------

_APOSTROPHES = "'’ʼ`´"
#: scripts whose combining marks are optional decoration and fold away; in kana, Hangul and Indic text they are letters
_FOLDABLE = ("LATIN", "GREEK", "CYRILLIC", "ARABIC", "HEBREW")
MIN_PREFIX_CHARS = 2
MAX_CANDIDATES = 6


def fold_name(text: str) -> str:
    """Case, accents, apostrophes, punctuation folded; tokens joined by single spaces. Script-agnostic: Latin / Greek /
    Cyrillic / Arabic / Hebrew lose accents, CJK / kana / Hangul / Indic keep every letter and mark."""
    out: list[str] = []
    base = ""
    for ch in unicodedata.normalize("NFKD", str(text or "")):
        cat = unicodedata.category(ch)
        if ch in _APOSTROPHES:
            continue
        if cat == "Mn":
            if not (base and unicodedata.name(base, "").startswith(_FOLDABLE)):
                out.append(ch)
            continue
        base = ch
        out.append(ch if cat[0] in "LMN" else " ")
    return " ".join(unicodedata.normalize("NFC", "".join(out)).casefold().split())


def name_tokens(text: str) -> tuple:
    return tuple(fold_name(text).split())


def match_tier(query: str, names: list, *, allow_prefix: bool = True) -> tuple:
    """``(tier, [index, ...])`` of ``names`` in the query's BEST tier: ``exact`` (folded whole name), ``token`` (every
    query token is a whole name token), ``prefix`` (every query token starts a name token, >= 2 chars); else ``("", [])``."""
    q = name_tokens(query)
    if not q:
        return "", []
    folded = [name_tokens(n) for n in names]
    for tier, ok in (("exact", lambda t: t == q), ("token", lambda t: set(q) <= set(t))):
        hit = [i for i, t in enumerate(folded) if ok(t)]
        if hit:
            return tier, hit
    if allow_prefix and len("".join(q)) >= MIN_PREFIX_CHARS:
        hit = [i for i, t in enumerate(folded) if t and all(any(n.startswith(w) for n in t) for w in q)]
        if hit:
            return "prefix", hit
    return "", []


@dataclass(frozen=True)
class PersonMatch:
    """One candidate: the fields ``ask_when_ambiguous.Candidate`` takes (id, name, relationship) + what ordered it."""

    person_id: str
    name: str
    relationship: str = ""
    is_partial: bool = False
    has_current_edge: bool = False
    last_evidence: float = 0.0          # epoch seconds of the newest thing known about them (0 = nothing readable)


@dataclass(frozen=True)
class Resolution:
    """``status``: ``unique`` | ``ambiguous`` (``matches`` in a stable order) | ``none``; ``tier``: ``exact`` | ``token`` |
    ``prefix`` | ``""``. A writer attaches only to ``unique``; a caller that talks asks about ``ambiguous``."""

    status: str
    tier: str = ""
    handle: str = ""
    matches: tuple = ()

    @property
    def person_id(self) -> Optional[str]:
        return self.matches[0].person_id if self.status == "unique" and self.matches else None

    @property
    def ambiguous(self) -> bool:
        return self.status == "ambiguous"

    def ask_payload(self) -> dict:
        """What the ask-when-ambiguous tier needs; this module never calls that tier."""
        return {"kind": "person", "handle": self.handle, "tier": self.tier,
                "candidates": [{"person_id": m.person_id, "name": m.name, "relationship": m.relationship}
                               for m in self.matches[:MAX_CANDIDATES]]}


async def _all(db, sql: str, params: tuple = ()) -> list:
    cur = await db.execute(sql, params)
    try:
        return list(await cur.fetchall())
    finally:
        close = getattr(cur, "close", None)
        if close is not None:
            try:
                res = close()
                if hasattr(res, "__await__"):
                    await res
            except Exception:  # noqa: BLE001 - a cursor that will not close is not a failed read
                pass


async def _ordered(db, user_id: str, rows: list) -> tuple:
    """``PersonMatch`` for the (id, name) rows, in a total order: people with a CURRENT edge first, then the newest
    evidence (last contact / update, newest edge), then folded name, then id. Missing columns are skipped."""
    ids = [str(r[0]) for r in rows]
    info = {i: {"rel": "", "partial": False, "ts": 0.0, "edge": False} for i in ids}
    marks = ",".join("?" * len(ids))

    def bump(d, *values):
        for v in values:
            t = parse_ts(v)
            if t is not None:
                d["ts"] = max(d["ts"], t.timestamp())

    for cols in ("id, relationship, is_partial, created_at, updated_at, last_contacted_at", "id, relationship, is_partial"):
        try:
            got = await _all(db, f"SELECT {cols} FROM people WHERE user_id = ? AND id IN ({marks})", (user_id, *ids))
        except Exception:  # noqa: BLE001 - a database without that column
            continue
        for r in got:
            d = info[str(r[0])]
            d["rel"], d["partial"] = str(r[1] or "").strip(), bool(r[2])
            bump(d, *tuple(r)[3:])
        break
    try:
        edges = await _all(
            db, "SELECT person_a_id, person_b_id, valid_to, valid_from, updated_at FROM person_relationships "
                f"WHERE user_id = ? AND (person_a_id IN ({marks}) OR person_b_id IN ({marks}))", (user_id, *ids, *ids))
    except Exception:  # noqa: BLE001
        edges = []
    for a, b, valid_to, valid_from, updated in edges:
        for pid in (str(a), str(b)):
            if pid in info:
                info[pid]["edge"] = info[pid]["edge"] or valid_to in (None, "")
                bump(info[pid], valid_from, updated)
    out = [PersonMatch(str(r[0]), str(r[1] or "").strip(), info[str(r[0])]["rel"], info[str(r[0])]["partial"],
                       info[str(r[0])]["edge"], info[str(r[0])]["ts"]) for r in rows]
    return tuple(sorted(out, key=lambda m: (not m.has_current_edge, -m.last_evidence, fold_name(m.name), m.person_id)))


async def resolve_person(db, user_id: str, name: str, *, allow_prefix: bool = True) -> Resolution:
    """Resolve ``name`` among this user's live people (rule 1). Never raises: a failed read is ``none``."""
    handle = str(name or "").strip()
    if not handle or not user_id:
        return Resolution("none")
    try:
        rows = await _all(db, "SELECT id, name FROM people WHERE user_id = ? AND deleted = 0", (user_id,))
    except Exception as exc:  # noqa: BLE001
        logger.debug("people_graph: roster read failed (%s)", type(exc).__name__)
        return Resolution("none")
    tier, idx = match_tier(handle, [str(r[1] or "") for r in rows], allow_prefix=allow_prefix)
    if not idx:
        return Resolution("none", handle=handle)
    hit = [rows[i] for i in idx]
    if len(hit) == 1:
        return Resolution("unique", tier, handle, (PersonMatch(str(hit[0][0]), str(hit[0][1] or "").strip()),))
    try:
        matches = await _ordered(db, user_id, hit)
    except Exception:  # noqa: BLE001 - ordering is a courtesy; the set is what matters
        matches = tuple(sorted((PersonMatch(str(r[0]), str(r[1] or "").strip()) for r in hit),
                               key=lambda m: (fold_name(m.name), m.person_id)))
    return Resolution("ambiguous", tier, handle, matches)


async def resolve_person_id(db, user_id: str, name: str, *, allow_prefix: bool = True) -> Optional[str]:
    """The id when the name is UNIQUE, else ``None`` (nobody, or more than one: never a guess)."""
    return (await resolve_person(db, user_id, name, allow_prefix=allow_prefix)).person_id


# -- evidence -----------------------------------------------------------------------------------------

def content_turn_id(user_id: str, text: str) -> Optional[str]:
    """The stable content id of a user turn - the formula memory rows use for ``user_turn_id``
    (``memory_authority.turn_evidence``): ``ut-`` + sha1 of ``user|squashed lower text``."""
    t = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    return "ut-" + hashlib.sha1(f"{user_id}|{t}".encode("utf-8")).hexdigest()[:16] if t else None


def quote_span(turn_text: str, quote: str) -> Optional[str]:
    """``start:end:hash12`` of ``quote`` inside ``turn_text`` - a pointer, not the words (forgetting the turn leaves no
    verbatim text in the graph). ``None`` when the quote is not in the turn."""
    turn, q = str(turn_text or ""), str(quote or "").strip()
    start = turn.find(q) if q else -1
    if start < 0 and q:
        start = turn.lower().find(q.lower())
    if start < 0:
        return None
    return f"{start}:{start + len(q)}:{hashlib.sha1(turn[start:start + len(q)].encode('utf-8')).hexdigest()[:12]}"


@dataclass(frozen=True)
class Evidence:
    """Where an edge came from; every field nullable (older rows, and paths with no turn such as a REST edit)."""

    turn_id: Optional[str] = None
    quote_span: Optional[str] = None
    speaker_rank: Optional[int] = None


def evidence_for(user_id: str, turn_text: str, quote: str = "", *, rank: Optional[int] = None) -> Evidence:
    return Evidence(content_turn_id(user_id, turn_text), quote_span(turn_text, quote) if quote else None, rank)


_RANKS = {"user_confirmed": 5, "user_stated": 4, "inferred": 0}     # memory_authority.RANK by authority label


def rank_for_authority(authority: Optional[str]) -> Optional[int]:
    return _RANKS.get(str(authority or "").strip().lower())


# -- edge writes --------------------------------------------------------------------------------------

#: columns added after the table's first shape; a database may lack any of them (0037, 0043)
OPTIONAL_COLUMNS = ("authority", "origin", "close_reason", "turn_id", "quote_span", "speaker_rank")
LOCK_TIMEOUT_MS = 3000


def _pg_conn(db):
    """The asyncpg connection behind ``db`` (the compat wrapper's ``_conn``; a raw connection is itself); ``None`` for
    anything else - aiosqlite's ``_conn`` is a sqlite3 one, which has no ``transaction``."""
    for c in (getattr(db, "_conn", None), db):
        if c is not None and hasattr(c, "transaction") and hasattr(c, "fetchval"):
            return c
    return None


async def edge_columns(db) -> frozenset:
    """Which ``OPTIONAL_COLUMNS`` this database has, from the catalog (a failing probe would poison an open Postgres
    transaction). Empty on failure: writers then use the original columns, as before the migrations."""
    try:
        if _pg_conn(db) is not None:
            rows = await _all(db, "SELECT column_name FROM information_schema.columns "
                                  "WHERE table_name = 'person_relationships' AND table_schema = current_schema()")
            have = {str(r[0]) for r in rows}
        else:
            have = {str(r[1]) for r in await _all(db, "PRAGMA table_info(person_relationships)")}
    except Exception:  # noqa: BLE001
        return frozenset()
    return frozenset(c for c in OPTIONAL_COLUMNS if c in have)


@asynccontextmanager
async def edge_transaction(db, user_id: str, person_a: str, person_b: str):
    """One transaction around an edge change holding the pair's advisory lock (key order-insensitive, so A->B and B->A
    queue). The asyncpg compat layer's ``commit()`` is a no-op - each statement is its own transaction unless one is
    opened - which is how close-then-insert could be torn. aiosqlite: the implicit transaction, committed on success,
    rolled back on any exception."""
    conn = _pg_conn(db)
    if conn is not None:
        lo, hi = sorted((str(person_a), str(person_b)))
        async with conn.transaction():
            await conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'")
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"person_edge|{user_id}|{lo}|{hi}")
            yield
    elif hasattr(db, "rollback"):
        try:
            yield
        except BaseException:
            await db.rollback()
            raise
        await db.commit()
    else:
        yield


@dataclass(frozen=True)
class EdgeSpec:
    """What the new current edge says."""

    rel_type: str
    rel_a_to_b: str
    rel_b_to_a: str
    rel_group: str
    notes: Optional[str] = None
    authority: Optional[str] = None
    origin: Optional[str] = None
    evidence: Optional[Evidence] = None


@dataclass(frozen=True)
class EdgeChange:
    """``status``: inserted | superseded | unchanged (already says this) | exists (lost an insert race) | stale (the
    current edge is not the one the caller decided about; nothing written)."""

    status: str
    edge_id: Optional[str] = None
    closed_id: Optional[str] = None


class EdgeWriteError(RuntimeError):
    """An edge change that could not be completed whole; the transaction was rolled back."""


ANY = object()      # replace_current_edge(expect_old_id=ANY): whatever is current


async def current_edge(db, user_id: str, person_a: str, person_b: str) -> Optional[tuple]:
    """``(id, rel_type)`` of the pair's CURRENT edge (``valid_to IS NULL``), or ``None``."""
    rows = await _all(db, "SELECT id, rel_type FROM person_relationships "
                          "WHERE user_id = ? AND person_a_id = ? AND person_b_id = ? AND valid_to IS NULL ORDER BY id",
                      (user_id, person_a, person_b))
    return (rows[0][0], rows[0][1]) if rows else None


async def close_edge(db, user_id: str, edge_id: str, *, reason: str, now: Optional[str] = None,
                     superseded_by: Optional[str] = None, cols: Optional[frozenset] = None) -> bool:
    """Close a CURRENT edge (``valid_to`` + ``close_reason`` [+ ``superseded_by``]); the row stays (rule 3). ``False``
    when there was no current edge to close. The caller owns the transaction."""
    cols = await edge_columns(db) if cols is None else cols
    now = now or now_iso()
    sets, params = ["valid_to = ?", "updated_at = ?"], [now, now]
    for col, val in (("superseded_by", superseded_by), ("close_reason", reason if "close_reason" in cols else None)):
        if val:
            sets.append(f"{col} = ?")
            params.append(val)
    cur = await db.execute(
        f"UPDATE person_relationships SET {', '.join(sets)} WHERE id = ? AND user_id = ? AND valid_to IS NULL",
        (*params, edge_id, user_id))
    return (getattr(cur, "rowcount", 1) or 0) > 0


async def insert_edge(db, *, user_id: str, edge_id: str, person_a_id: str, person_b_id: str, spec: EdgeSpec,
                      now: Optional[str] = None, cols: Optional[frozenset] = None, ignore_conflict: bool = True) -> bool:
    """Insert a CURRENT edge (``valid_from`` = now) with its authority / origin / evidence where the database has the
    columns. ``ignore_conflict``: a current edge already there -> no-op ``False``; else the violation is raised so a
    transaction rolls back."""
    cols = await edge_columns(db) if cols is None else cols
    now = now or now_iso()
    ev = spec.evidence or Evidence()
    row = {"id": edge_id, "user_id": user_id, "person_a_id": person_a_id, "person_b_id": person_b_id,
           "rel_type": spec.rel_type, "rel_a_to_b": spec.rel_a_to_b, "rel_b_to_a": spec.rel_b_to_a,
           "rel_group": spec.rel_group, "valid_from": now, "created_at": now, "updated_at": now}
    if spec.notes is not None:
        row["notes"] = spec.notes
    for col, v in (("authority", spec.authority), ("origin", spec.origin), ("turn_id", ev.turn_id),
                   ("quote_span", ev.quote_span), ("speaker_rank", ev.speaker_rank)):
        if col in cols and v is not None:
            row[col] = v
    tail = " ON CONFLICT (user_id, person_a_id, person_b_id) WHERE valid_to IS NULL DO NOTHING" if ignore_conflict else ""
    cur = await db.execute(f"INSERT INTO person_relationships ({', '.join(row)}) VALUES ({','.join('?' * len(row))}){tail}",
                           tuple(row.values()))
    return (getattr(cur, "rowcount", 1) or 0) > 0


async def replace_current_edge(db, user_id: str, person_a: str, person_b: str, spec: EdgeSpec, *,
                               expect_old_id: Any = ANY, close_reason: str = "superseded",
                               now: Optional[str] = None) -> EdgeChange:
    """Make ``spec`` the pair's current edge, closing the one it replaces - ATOMICALLY (rule 2): in one transaction
    holding the pair's lock, re-read the current edge (decide on what is true NOW), close it (``superseded_by`` = new
    id), insert the new one; if the insert does not land everything rolls back (``EdgeWriteError``) and the old edge is
    still current. ``expect_old_id``: ``ANY``, ``None`` (the caller saw no current edge) or the id the caller's
    authority check was about - a different current edge returns ``stale`` and writes nothing."""
    now = now or now_iso()
    cols = await edge_columns(db)
    new_id = str(uuid.uuid4())
    async with edge_transaction(db, user_id, person_a, person_b):
        cur = await current_edge(db, user_id, person_a, person_b)
        if expect_old_id is not ANY and (cur[0] if cur else None) != expect_old_id:
            return EdgeChange("stale", closed_id=cur[0] if cur else None)
        if cur is not None and cur[1] == spec.rel_type:
            return EdgeChange("unchanged", edge_id=cur[0])
        if cur is None:
            landed = await insert_edge(db, user_id=user_id, edge_id=new_id, person_a_id=person_a, person_b_id=person_b,
                                       spec=spec, now=now, cols=cols)
            return EdgeChange("inserted" if landed else "exists", edge_id=new_id if landed else None)
        if not await close_edge(db, user_id, cur[0], reason=close_reason, now=now, superseded_by=new_id, cols=cols):
            raise EdgeWriteError("the current edge could not be closed")
        if not await insert_edge(db, user_id=user_id, edge_id=new_id, person_a_id=person_a, person_b_id=person_b,
                                 spec=spec, now=now, cols=cols, ignore_conflict=False):
            raise EdgeWriteError("the replacement edge did not land")
        return EdgeChange("superseded", edge_id=new_id, closed_id=cur[0])


# -- temporal read ------------------------------------------------------------------------------------

_FIELDS = ("id", "person_a_id", "person_b_id", "rel_type", "rel_a_to_b", "rel_b_to_a", "rel_group", "notes",
           "valid_from", "valid_to", "superseded_by", "created_at", "updated_at")


def edge_in_force(edge: dict, at: datetime) -> bool:
    """Was this edge true at ``at``? Window ``[valid_from, valid_to)``; no ``valid_from`` (the REST path never set one)
    -> ``created_at``; an unreadable ``valid_to`` is an unknown end, so a past instant is NOT claimed."""
    start = parse_ts(edge.get("valid_from")) or parse_ts(edge.get("created_at"))
    if start is not None and at < start:
        return False
    if edge.get("valid_to") in (None, ""):
        return True
    end = parse_ts(edge["valid_to"])
    return end is not None and at < end


async def edges_for_person(db, user_id: str, person_id: str, *, as_of: Optional[datetime] = None,
                           history: bool = False) -> list:
    """The owner's edges touching ``person_id`` as dicts (rule 4): CURRENT by default, those in force at ``as_of``, or
    every edge with ``history``; newest start first; each carries ``current`` and, where present, ``close_reason``."""
    cols = await edge_columns(db)
    fields = list(_FIELDS) + [c for c in OPTIONAL_COLUMNS if c in cols]
    where = "user_id = ? AND (person_a_id = ? OR person_b_id = ?)" + ("" if as_of or history else " AND valid_to IS NULL")
    rows = await _all(db, f"SELECT {', '.join(fields)} FROM person_relationships WHERE {where}", (user_id, person_id, person_id))
    edges = [dict(zip(fields, r)) for r in rows]
    for e in edges:
        e["current"] = e["valid_to"] in (None, "")
    if as_of is not None:
        edges = [e for e in edges if edge_in_force(e, as_of)]
    far = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(edges, key=lambda e: (parse_ts(e["valid_from"]) or parse_ts(e["created_at"]) or far, e["id"]), reverse=True)
