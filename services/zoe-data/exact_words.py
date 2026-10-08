"""exact_words - an index of the owner's OWN verbatim turns, so "what exactly did I say about the dentist?" has an answer.

Why (ZMB axis j, measured on Zoe's live code 2026-10-07: J1 0 of 20, J2 0 of 20): Zoe stored distilled FACTS. "What did I
say about X" and "when did I say it" ask for the OWNER'S WORDS and the DAY, and nothing held them for most sentences:

  * the deterministic extractor keeps no row for ordinary speech ("I told Dr Okafor I can only manage Tuesday afternoons
    for the dentist and not Thursday" is not a fact it has a template for), so ``source_excerpt`` - the whole turn, stored
    beside the rows that ARE extracted - exists for a minority of turns only;
  * the brain cannot search ``chat_messages`` (a store that holds every turn of chat AND voice, but is neither indexed by
    word nor safe to read from a model: it holds pasted mail, other people's quoted speech and forgotten names).

So this is a third thing: one row per turn the owner said, written when the turn is (``memory_extractor.extract_and_ingest``
sees every turn of both lanes) and caught up at night from ``chat_messages`` (``backfill_recent``), searched by word.

The rules (each pinned by ``tests/test_exact_words.py``):

* **the owner's own words only** - ``own_words.analyze`` first: a pasted email, a quoted third person and an instruction-shaped
  line are not stored; a turn the speaker gate did not confirm (``speaker_verified is False``) is not stored; a turn that is
  itself a question, or a request for exact words, is not stored (it is not something the owner SAID about anything); a turn
  the PII scrubber refuses (a card number, a PIN) is not stored; guests have no index;
* **per user** - every read and write carries the user id, the key is ``(user_id, turn_id)``, no read crosses users;
* **forgotten means forgotten** - a forget (``erase_entity``, called from ``memory_forget_entity`` beside the ledger write and
  the row erase) DELETES the rows that name the entity; the durable ledger and the 300 s tombstone are ALSO consulted on every
  read and every write, so a name the ledger shields is never served even if a row somehow survived; ``delete_user`` (the
  audited right-to-be-forgotten path) removes every row of a user;
* **off the voice turn's critical path** - writes are a post-turn background step and a nightly catch-up; the read is one
  indexed SELECT bounded to ``MAX_CANDIDATES`` rows and it fires only on a question that asks for the owner's words or a date
  (``wants``: ``memory_gate.is_evidence_question`` "said" / "when" kinds), every other turn is untouched;
* **never raises**: a failure is "no exact words", the turn proceeds as before - except ``delete_user`` and ``erase_entity``, which fail closed (a
  right-to-be-forgotten must not report success over rows it could not erase);
* **the catch-up obeys the hook's walls**: ``backfill_recent`` skips a user who opted out of memory and a voice turn the speaker
  gate rejected (the voice lane persists the verdict in ``chat_messages.metadata``), and reads the whole window page by page.

Physical erase, honestly: the row is DELETEd (Postgres; an in-process dict in the lab). The byte-level machinery of
``memory_residue`` covers the Chroma palace; this table lives in Postgres beside ``chat_messages`` (which keeps the same
turns and is not erased by a forget today: a pre-existing gap this does not widen), where a dead tuple is reclaimed by
autovacuum.

``ZOE_EXACT_WORDS`` = on (default) | off.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Protocol

logger = logging.getLogger(__name__)

ENV = "ZOE_EXACT_WORDS"
MIN_WORDS = 3
MAX_CHARS = 500
MAX_CANDIDATES = 400
DEFAULT_K = 3
#: the whole indexed read: a slow store costs the exact-words block, never the turn
READ_TIMEOUT_S = 1.5
#: a re-delivered turn (a retry, the second ``extract_and_ingest`` of one turn) inside this window is one row
DEDUP_BUCKET_S = 600
_TURN_TTL_S = 300.0
_TURN_MAX = 256


def enabled() -> bool:
    return os.environ.get(ENV, "on").strip().lower() not in ("0", "false", "no", "off")


# ── what is a request for the owner's words ──────────────────────────────────

def wants(message: str) -> bool:
    """Does this message ask what the owner SAID, or WHEN? ("what exactly did I say about the dentist", "read me back what I
    told you about Dana's kids", "did I tell Dana Tuesday or Thursday", "when did I tell you"). The same predicate the recall
    floor uses for evidence-shaped questions (``memory_gate.is_evidence_question``), minus "are you sure". Pure."""
    try:
        from memory_gate import evidence_question_kind
        return evidence_question_kind(message or "") in ("said", "when")
    except Exception:  # noqa: BLE001
        return False


# ── tokens ───────────────────────────────────────────────────────────────────

#: words of the REQUEST (and of every sentence), which say nothing about WHAT was said
_STOP = frozenset("""a an the is are was were be been am do does did have has had what whats when where who whom which how why
me my mine our your you i we he she they it its his her their them of to in on at for from with about and or any some tell told
say said says saying mention mentioned ask asked exactly exact actually literally word words read back remind please again
still now ever really just that this these those there here then than so as if not no yes ok okay
last week month year yesterday today ago earlier recently before after first time day days""".split())
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def _stem(tok: str) -> str:
    t = tok.lower()
    if t.endswith("'s"):
        t = t[:-2]
    t = t.replace("'", "")
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    return t


def content_tokens(text: str) -> list[str]:
    """The stemmed, de-duplicated content words of a text, in order (the words of the request and stop words dropped)."""
    out: list[str] = []
    for m in _TOKEN_RE.finditer((text or "").lower()):
        raw = m.group(0)
        if raw in _STOP or len(raw) < 3:
            continue
        t = _stem(raw)
        if len(t) >= 3 and t not in _STOP and t not in out:
            out.append(t)
    return out


def tokens_column(text: str) -> str:
    """The searchable form stored beside the text: `` tok1 tok2 `` (leading and trailing space so ``LIKE '% tok %'`` is a
    whole-word match)."""
    toks = content_tokens(text)
    return " " + " ".join(toks) + " " if toks else ""


# ── storage ──────────────────────────────────────────────────────────────────

Row = tuple  # (turn_id, text, said_at, tokens)


class Backend(Protocol):
    async def add(self, user_id: str, turn_id: str, said_at: float, text: str, tokens: str, source: str) -> bool: ...

    async def candidates(self, user_id: str, tokens: list[str], limit: int) -> list[Row]: ...

    async def rows_matching(self, user_id: str, needle: str) -> list[tuple[str, str]]: ...

    async def get(self, user_id: str, turn_ids: Iterable[str]) -> list[tuple[str, str, float]]: ...

    async def delete(self, user_id: str, turn_ids: Iterable[str]) -> int: ...

    async def delete_user(self, user_id: str) -> int: ...

    async def count(self, user_id: str) -> int: ...


class MemoryBackend:
    """In-process index (tests, the benchmark lab). Same contract as the SQL one; ``rows`` is exposed so a test can look at
    exactly what is stored."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}

    async def add(self, user_id, turn_id, said_at, text, tokens, source):
        if (user_id, turn_id) in self.rows:
            return False
        self.rows[(user_id, turn_id)] = {"user_id": user_id, "turn_id": turn_id, "said_at": float(said_at), "text": text,
                                          "tokens": tokens, "source": source}
        return True

    async def candidates(self, user_id, tokens, limit):
        hits = [r for (u, _t), r in self.rows.items() if u == user_id and any(f" {t} " in r["tokens"] for t in tokens)]
        hits.sort(key=lambda r: (-r["said_at"], r["turn_id"]))
        return [(r["turn_id"], r["text"], r["said_at"], r["tokens"]) for r in hits[:limit]]

    async def rows_matching(self, user_id, needle):
        n = (needle or "").lower()
        return [(r["turn_id"], r["text"]) for (u, _t), r in self.rows.items() if u == user_id and n in r["text"].lower()]

    async def get(self, user_id, turn_ids):
        out = []
        for t in list(turn_ids):
            r = self.rows.get((user_id, t))
            if r is not None:
                out.append((t, r["text"], r["said_at"]))
        return out

    async def delete(self, user_id, turn_ids):
        n = 0
        for t in list(turn_ids):
            if self.rows.pop((user_id, t), None) is not None:
                n += 1
        return n

    async def delete_user(self, user_id):
        keys = [k for k in self.rows if k[0] == user_id]
        for k in keys:
            del self.rows[k]
        return len(keys)

    async def count(self, user_id):
        return sum(1 for (u, _t) in self.rows if u == user_id)


class SqlBackend:
    """The ``exact_turns`` table (alembic 0039) over ``db_pool``. Portable SQL (``?`` placeholders, ``ON CONFLICT``), so the
    test suite runs these exact statements on SQLite."""

    @staticmethod
    def _ctx():
        from db_pool import get_db_ctx  # type: ignore[import]
        return get_db_ctx()

    async def add(self, user_id, turn_id, said_at, text, tokens, source):
        async with self._ctx() as db:
            cur = await db.execute(
                """INSERT INTO exact_turns (user_id, turn_id, said_at, text, tokens, source)
                   VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (user_id, turn_id) DO NOTHING""",
                (user_id, turn_id, float(said_at), text, tokens, source))
            await db.commit()
            return int(getattr(cur, "rowcount", 1) or 0) > 0

    async def candidates(self, user_id, tokens, limit):
        if not tokens:
            return []
        clause = " OR ".join("tokens LIKE ?" for _ in tokens)
        params = (user_id, *[f"% {t} %" for t in tokens], int(limit))
        async with self._ctx() as db:
            cur = await db.execute(
                f"SELECT turn_id, text, said_at, tokens FROM exact_turns WHERE user_id = ? AND ({clause}) "
                "ORDER BY said_at DESC LIMIT ?", params)
            return [(str(r[0]), str(r[1]), float(r[2]), str(r[3])) for r in await cur.fetchall()]

    async def rows_matching(self, user_id, needle):
        async with self._ctx() as db:
            cur = await db.execute("SELECT turn_id, text FROM exact_turns WHERE user_id = ? AND LOWER(text) LIKE ?",
                                   (user_id, f"%{(needle or '').lower()}%"))
            return [(str(r[0]), str(r[1])) for r in await cur.fetchall()]

    async def get(self, user_id, turn_ids):
        ids = list(turn_ids)
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        async with self._ctx() as db:
            cur = await db.execute(f"SELECT turn_id, text, said_at FROM exact_turns WHERE user_id = ? AND turn_id IN ({marks})",
                                   (user_id, *ids))
            return [(str(r[0]), str(r[1]), float(r[2])) for r in await cur.fetchall()]

    async def delete(self, user_id, turn_ids):
        ids = list(turn_ids)
        if not ids:
            return 0
        marks = ", ".join("?" for _ in ids)
        async with self._ctx() as db:
            cur = await db.execute(f"DELETE FROM exact_turns WHERE user_id = ? AND turn_id IN ({marks})", (user_id, *ids))
            await db.commit()
            return int(getattr(cur, "rowcount", 0) or 0)

    async def delete_user(self, user_id):
        async with self._ctx() as db:
            cur = await db.execute("DELETE FROM exact_turns WHERE user_id = ?", (user_id,))
            await db.commit()
            return int(getattr(cur, "rowcount", 0) or 0)

    async def count(self, user_id):
        async with self._ctx() as db:
            cur = await db.execute("SELECT COUNT(*) FROM exact_turns WHERE user_id = ?", (user_id,))
            row = await cur.fetchone()
            return int(row[0]) if row else 0


_backend: Optional[Backend] = None


def get_backend() -> Backend:
    global _backend
    if _backend is None:
        _backend = SqlBackend()
    return _backend


def set_backend(backend: Optional[Backend]) -> None:
    """Swap the store (tests / lab); ``None`` restores the SQL default."""
    global _backend
    _backend = backend


# ── write ────────────────────────────────────────────────────────────────────

def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def turn_key(user_id: str, text: str, said_at: float) -> str:
    """A STABLE id: the same turn delivered twice inside ``DEDUP_BUCKET_S`` is one row; the same words said on another day are
    another row (they are another saying)."""
    basis = f"{user_id}|{_squash(text).lower()}|{int(said_at // DEDUP_BUCKET_S)}"
    return "xw-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:20]


def _is_a_question(text: str) -> bool:
    """A whole turn that is one question ("what's on tomorrow?"): the owner asked, they did not say anything about it."""
    t = _squash(text)
    return t.endswith("?") and not re.search(r"[.!]\s+\S", t[:-1])


def _cap(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    cut = text[:MAX_CHARS]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,;:") + "..."


async def _shielded(user_id: str, text: str) -> bool:
    """Does the text name an entity the owner asked Zoe to forget (the 300 s tombstone or the durable ledger)? Fail-open
    on a lookup failure only for the ledger (the row's own erase is the first wall)."""
    try:
        from memory_tombstones import matching_tombstone
        if matching_tombstone(user_id, text):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        import memory_forgotten
        return bool(await memory_forgotten.matches(user_id, text))
    except Exception:  # noqa: BLE001
        return False


async def index_turn(user_id: str, text: str, *, said_at: Optional[float] = None, turn_id: Optional[str] = None,
                     source: str = "chat", speaker_verified: Optional[bool] = None) -> bool:
    """Index one owner turn. True when a row was written. Never raises."""
    try:
        if not enabled() or not (user_id or "").strip() or not (text or "").strip():
            return False
        import memory_provenance
        if memory_provenance.is_off_record(user_id, text):   # BM5: off the record is never indexed
            return False
        from memory_service import is_guest_memory_user, scrub_pii
        if is_guest_memory_user(user_id) or speaker_verified is False:
            return False
        import own_words
        own = own_words.analyze(text)
        if not own.has_own:
            return False
        owner_text = _squash(own.text)
        if (len(owner_text.split()) < MIN_WORDS or own_words.instruction_shaped(owner_text)
                or _is_a_question(owner_text) or wants(owner_text)):
            return False
        scrubbed, reject = scrub_pii(owner_text)
        if reject or not scrubbed.strip():
            return False
        if await _shielded(user_id, scrubbed):
            return False
        when = float(said_at) if said_at is not None else time.time()
        kept = _cap(scrubbed)
        cols = tokens_column(kept)
        if not cols:
            return False
        return bool(await get_backend().add(user_id, turn_id or turn_key(user_id, kept, when), when, kept, cols, source))
    except Exception as exc:  # noqa: BLE001 - the index is an extra; a turn never fails on it
        logger.warning("exact_words: turn not indexed (%s)", type(exc).__name__)
        return False


# ── read ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Hit:
    turn_id: str
    text: str
    said_at: float
    score: float


def query_tokens(question: str) -> list[str]:
    return content_tokens(question)


def _score(rows: list[Row], q: list[str]) -> list[Hit]:
    n = len(rows)
    df = {t: sum(1 for r in rows if f" {t} " in r[3]) for t in q}
    idf = {t: math.log((n + 1.0) / (df[t] + 0.5)) + 1.0 for t in q}
    total = sum(idf.values()) or 1.0
    out = [Hit(r[0], r[1], r[2], sum(idf[t] for t in q if f" {t} " in r[3]) / total) for r in rows]
    out.sort(key=lambda h: (-round(h.score, 6), -h.said_at, h.turn_id))
    return out


async def lookup(user_id: str, question: str, *, k: int = DEFAULT_K) -> list[Hit]:
    """The owner's own turns that best match ``question`` (what they said about it), best first, at most ``k``; ``[]`` when
    the index is off, the question names nothing searchable, or nothing matches well. Rows that name a forgotten entity are
    dropped HERE as well (the ledger is consulted on every read), and each row's words are re-checked against the own-words
    wall. Never raises."""
    try:
        if not enabled() or not (user_id or "").strip():
            return []
        q = query_tokens(question)
        if not q:
            return []
        rows = await asyncio.wait_for(get_backend().candidates(user_id, q, MAX_CANDIDATES), READ_TIMEOUT_S)
        if not rows:
            return []
        try:
            import memory_forgotten
            rows, _dropped = await memory_forgotten.keep_unforgotten(user_id, rows, text_of=lambda r: r[1])
        except Exception:  # noqa: BLE001
            pass
        try:
            from memory_tombstones import matching_tombstone
            rows = [r for r in rows if not matching_tombstone(user_id, r[1])]
        except Exception:  # noqa: BLE001
            pass
        import own_words
        safe: list[Row] = []
        for r in rows:
            own = own_words.analyze(r[1])
            if own.has_own and not own_words.instruction_shaped(own.text):
                safe.append((r[0], _squash(own.text), r[2], r[3]))
        ranked = _score(safe, q)
        if not ranked:
            return []
        best = ranked[0].score
        floor = max(0.34, 0.5 * best)
        out: list[Hit] = []
        for h in ranked:
            if h.score < floor:
                break
            # the same words delivered twice (the post-turn hook and the nightly catch-up key a turn by its time bucket)
            if any(o.text == h.text and abs(o.said_at - h.said_at) < 2 * DEDUP_BUCKET_S for o in out):
                continue
            out.append(h)
            if len(out) >= max(1, k):
                break
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("exact_words: lookup failed (%s)", type(exc).__name__)
        return []


def render_block(hits: list[Hit], *, now: Optional[float] = None) -> str:
    """The packet block: the owner's words, each with the day they said it. Quoted, never obeyed."""
    if not hits:
        return ""
    import own_words
    try:
        import recall_evidence
    except Exception:  # noqa: BLE001
        recall_evidence = None  # type: ignore[assignment]
    lines = []
    for h in hits:
        if own_words.instruction_shaped(h.text):
            continue
        try:
            when = recall_evidence.render_date(h.said_at, now=now) if recall_evidence else ""
        except Exception:  # noqa: BLE001
            when = ""
        words = h.text.replace("[", "(").replace("]", ")")
        lines.append(f"- {when + ': ' if when else ''}“{words}”")
    if not lines:
        return ""
    return ("## Your own words (verbatim, from your own turns; the date is when you said it)\n"
            "(When asked what you said, quote these exactly and give the date shown; never paraphrase them or guess a date.)\n"
            + "\n".join(lines))


async def packet_block(user_id: str, question: str, *, k: int = DEFAULT_K) -> str:
    return render_block(await lookup(user_id, question, k=k))


# ── the Flue turn mark: the recall_memory TOOL's gate ────────────────────────
# The tool reaches /for-prompt with the MODEL's query ("dentist"), not the user's words. The Flue seam notes the real user's
# turn here (in-process: zoe-data is a single uvicorn worker); a tool call made during that turn reads it back. Every turn
# overwrites the mark.
_marks: dict[str, tuple[str, float]] = {}


def note_turn(user_id: str, message: str) -> None:
    """Record this user turn when it asks for exact words. No-op when off; never raises."""
    try:
        uid = (user_id or "").strip()
        if not uid or not enabled():
            return
        _marks.pop(uid, None)
        if wants(message):
            _marks[uid] = (str(message or ""), time.monotonic())
            while len(_marks) > _TURN_MAX:
                _marks.pop(next(iter(_marks)))
    except Exception:  # noqa: BLE001
        return


def marked_message(user_id: str) -> str:
    """The user's message of the CURRENT turn when it asked for exact words, else ""."""
    got = _marks.get((user_id or "").strip())
    return got[0] if got and time.monotonic() - got[1] <= _TURN_TTL_S else ""


def question_for(user_id: str, message: str) -> str:
    """What to search by for a /for-prompt call: the message when it itself asks for the owner's words, else - on the tool path,
    where the message is the model's own query - the user's question of this turn plus the query; "" when this is not an
    exact-words turn."""
    if wants(message):
        return message
    marked = marked_message(user_id)
    return f"{marked} {message}".strip() if marked else ""


# ── forgetting and deleting ──────────────────────────────────────────────────

async def erase_entity(user_id: str, name: str) -> int:
    """Delete this user's indexed turns that name ``name`` (a whole word / phrase, case-blind, separator-blind: the same
    pattern the forget sweep uses). Returns the rows removed (0 = nothing matched). A store failure on the read or the delete is
    RAISED, never folded into that 0: a forget that could not erase the words must not be confirmed
    (``memory_forget_entity`` reports the failure instead of "I've forgotten ...")."""
    if not (user_id or "").strip() or not (name or "").strip():
        return 0
    from memory_forgotten import name_pattern, normalise_key
    pat = name_pattern(name)
    key = normalise_key(name)
    needle = (key.split(" ")[0] if key else name.strip().split(" ")[0]).lower()
    backend = get_backend()
    ids = [tid for tid, text in await backend.rows_matching(user_id, needle) if pat.search(text or "")]
    return int(await backend.delete(user_id, ids) or 0) if ids else 0


async def erase_text(user_id: str, text: str) -> int:
    """Delete this user's indexed turns whose words ARE ``text`` (case/space-blind) or contain it - "forget it" after "why did you
    say that?" removes the owner's turn that was just quoted back, not only the fact. Like ``erase_entity`` a store failure is
    RAISED (a forget that could not erase the words must not be confirmed). 0 = nothing matched."""
    key = _squash(text or "").lower().strip(" .!?\"'")
    if not (user_id or "").strip() or len(key) < 6:
        return 0
    backend = get_backend()
    needle = max(re.findall(r"[a-z0-9']+", key), key=len, default="")
    if not needle:
        return 0
    ids = [tid for tid, t in await backend.rows_matching(user_id, needle) if key in _squash(t or "").lower()]
    return int(await backend.delete(user_id, ids) or 0) if ids else 0


async def delete_user(user_id: str) -> int:
    """Remove every indexed turn of a user (the audited right-to-be-forgotten path). Returns the rows removed (0 when there were
    none). UNLIKE every other function here this does NOT swallow a store failure: a right-to-be-forgotten that could not erase the
    words must fail, not report success while the verbatim rows stay readable - ``MemoryService.delete_user`` lets it propagate."""
    if not (user_id or "").strip():
        return 0
    return int(await get_backend().delete_user(user_id) or 0)


# ── the nightly catch-up ─────────────────────────────────────────────────────

#: rows read per page of the catch-up (keyset-paginated, newest first)
BACKFILL_PAGE = 500
#: the most rows one catch-up looks at, however long the window (a safety bound; the OLDEST rows are the ones given up)
BACKFILL_MAX_ROWS = 100_000


async def _opted_out(user_id: str, *, fail_closed: bool) -> bool:
    """Per-user ``memory_opt_out`` (``user_prefs``, cached). The catch-up fails CLOSED (a lookup that cannot say is "skip the
    night", the next run catches up); the hook-side check stays fail-open like the rest of the memory writers."""
    try:
        import user_prefs
        return bool(await user_prefs.is_memory_opted_out(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("exact_words: opt-out lookup failed (%s)", type(exc).__name__)
        return fail_closed


def speaker_rejected(metadata: Any) -> bool:
    """Does a ``chat_messages.metadata`` blob carry the speaker gate's rejection (``{"speaker_verified": false}``, written by
    the voice lane's save)? Malformed / absent metadata is "no verdict" (the lane reported none), never a rejection."""
    try:
        import json
        meta = json.loads(metadata) if isinstance(metadata, (str, bytes)) and metadata else metadata
        return isinstance(meta, dict) and meta.get("speaker_verified") is False
    except Exception:  # noqa: BLE001
        return False


async def _backfill_page(user_id: str, hours: int, cursor: Optional[tuple[str, str]], limit: int) -> list[tuple]:
    """One page of the owner's user turns inside the window, NEWEST first, strictly before ``cursor`` (``(created_at, id)`` of the
    last row of the previous page; None = the first page). Rows: ``(id, content, epoch, metadata, created_at)``. Postgres only
    (``chat_messages.created_at`` is TEXT there); tests replace this seam."""
    from db_pool import get_db_ctx  # type: ignore[import]
    from user_filters import message_owner_expr
    from memory_provenance import off_record_sql
    sql = ("SELECT cm.id, cm.content, EXTRACT(EPOCH FROM cm.created_at::timestamptz), cm.metadata, cm.created_at "
           "FROM chat_messages cm JOIN chat_sessions cs ON cm.session_id = cs.id "
           "WHERE " + message_owner_expr() + " = ? AND cm.role = 'user' AND " + off_record_sql("cm") + " "
           "AND cm.created_at::timestamptz >= (now()::timestamptz - make_interval(hours => ?::int))")
    params: list[Any] = [user_id, int(hours)]
    if cursor is not None:
        sql += " AND (cm.created_at::timestamptz, cm.id) < (?::timestamptz, ?)"
        params += [cursor[0], cursor[1]]
    sql += " ORDER BY cm.created_at::timestamptz DESC, cm.id DESC LIMIT ?"
    params.append(int(limit))
    async with get_db_ctx() as db:
        return [tuple(r) for r in await (await db.execute(sql, tuple(params))).fetchall()]


async def backfill_recent(user_id: str, *, hours: Optional[int] = None) -> int:
    """Index the owner's user turns of the last ``hours`` hours from ``chat_messages`` (chat AND voice are saved there) that the
    post-turn hook missed; idempotent (the key is the content key the hook uses). Only with the SQL index (the lab and tests have
    no ``chat_messages``). The catch-up obeys the SAME walls as the hook: a user who opted out of memory is skipped (before any
    read or write), and a voice turn the speaker gate rejected (``metadata.speaker_verified is false``) is never indexed. The
    window is read page by page (keyset on ``(created_at, id)``, newest first) so a long window reaches every turn, not the
    first 2,000 of it. Returns rows written. Never raises."""
    try:
        if not enabled() or not (user_id or "").strip() or not isinstance(get_backend(), SqlBackend):
            return 0
        if await _opted_out(user_id, fail_closed=True):
            return 0
        if hours is None:
            try:
                hours = int(os.environ.get("ZOE_EXACT_WORDS_BACKFILL_HOURS", "36"))
            except ValueError:
                hours = 36
        wrote = 0
        seen = 0
        cursor: Optional[tuple[str, str]] = None
        while seen < BACKFILL_MAX_ROWS:
            rows = await _backfill_page(user_id, int(hours), cursor, BACKFILL_PAGE)
            if not rows:
                break
            seen += len(rows)
            for row in rows:
                _mid, content, epoch, metadata = row[0], row[1], row[2], row[3]
                if speaker_rejected(metadata):
                    continue
                # no turn id: the content key (the post-turn hook's), so a turn the hook already indexed is one row, not two
                if await index_turn(user_id, str(content or ""), said_at=float(epoch or time.time()), source="backfill"):
                    wrote += 1
            last = rows[-1]
            nxt = (str(last[4]), str(last[0]))
            if len(rows) < BACKFILL_PAGE or nxt == cursor:
                break
            cursor = nxt
        else:
            logger.warning("exact_words: catch-up stopped at the %d-row bound user=%s", BACKFILL_MAX_ROWS, user_id)
        if wrote:
            logger.info("exact_words: caught up %d turn(s) from chat_messages user=%s", wrote, user_id)
        return wrote
    except Exception as exc:  # noqa: BLE001
        logger.debug("exact_words: catch-up skipped (%s)", type(exc).__name__)
        return 0
