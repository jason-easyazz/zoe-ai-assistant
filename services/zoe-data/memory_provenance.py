"""memory_provenance - what the PREVIOUS reply used, and what the owner asked Zoe NOT to keep (BM5, ZOE_MEMORY_PROVENANCE_ANSWERS).

Two small pieces of in-process, per-user state. Neither holds the owner's words for longer than the turn that made it
(the ledger keeps ids and counts; the off-the-record registry keeps a hash and a token set), and neither reads or writes a
store: the spoken answers built on them are ``provenance_answers``.

THE TURN LEDGER ("why did you say that?" needs to know which rows the last reply stood on)
  * ``note_user_turn`` numbers the user's turns (idempotent: the chat save, the voice save and the tier each see the same
    turn, and a turn is counted once).
  * ``note_served`` is called by ``/for-prompt`` (the one packet builder: the Flue recall floor, the ``recall_memory`` tool,
    the core lane) with the rows and the owner-turns it handed the brain.
  * ``commit_brain_reply`` runs when a brain reply ends (``brain_dispatch``): of the rows served during that turn it keeps,
    best first, the ones the reply actually restates (content-word overlap with the reply), as IDS, plus how many were served.
    ``note_direct_reply`` records a deterministic tier's reply (no memory used).
  * ``previous_reply`` returns the record of the turn immediately before the one being asked about - and ``None`` when there
    is no such record (a lane that records nothing, a restart, a stale turn). "No record" is never answered as "no memory used":
    the answer then says it cannot tell. Explaining a reply Zoe did not make is the failure this guards against.
  * ``mark_explained`` / ``explained`` carry the row an answer just named to the NEXT turn only, so "forget it" / "that's wrong,
    it's X" mean that row and not whatever was written last.

OFF THE RECORD ("off the record: ...", "don't remember this", "this stays between us")
  * ``parse_off_record`` is the pure cue parser. ``claim_turn`` is called at the first sight of a user turn (the chat/voice
    save, then the tier - idempotent): a cue WITH a payload marks that turn; a BARE cue arms the NEXT turn; a pending bare cue
    claims the turn after it.
  * A marked turn is remembered as a hash and a token set for ``MARK_TTL_S``. ``is_off_record(user, text)`` is the exact-turn
    test every per-turn writer asks before it extracts, digests or indexes (``routers/chat.py``, ``routers/voice_tts.py``,
    ``memory_extractor``, ``exact_words``); ``blocks_write(user, text, excerpt)`` is the content test at the ONE write choke point
    (``MemoryService.ingest``), so a tool the brain calls DURING the marked turn cannot store what the owner just asked it not
    to keep ("never on the brain's say-so"). A write that merely shares a word with the marked turn is not blocked: the test is
    that the row's content words are mostly the marked turn's.
  * The off-record flag also travels with the persisted transcript row (``chat_messages.metadata.off_record``) so the readers
    that rebuild memory from the transcript at night skip it; see ``off_record_sql``.
  * Audit: ONE log line per marked turn - ``OFF_RECORD user=<id> cue=<label> mode=<same_turn|next_turn> chars=<n>`` - never the
    words.

Everything here is a no-op with the flag off, so an operator who switches the feature off gets exactly the pre-BM5 behaviour.
Time is injectable (``now=``) in every function. Single event loop, no locks.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

ENV = "ZOE_MEMORY_PROVENANCE_ANSWERS"

#: a reply is explained for this long after it was made (a "why did you say that?" a quarter of an hour later is about
#: something else)
REPLY_TTL_S = 900.0
#: "forget it" / "that's wrong, it's X" mean the explained row for this long, and only on the very next turn
EXPLAINED_TTL_S = 300.0
#: a marked turn stays marked this long (the post-turn writers and the nightly catch-up run well inside it)
MARK_TTL_S = 900.0
#: a bare "off the record" arms the next turn for this long
PENDING_TTL_S = 600.0
#: two sightings of one turn closer than this are the same turn (chat save + tier, voice save + tier)
SAME_TURN_S = 2.5
#: the marked turn counts as "in flight" (every text variant of it is off the record) for this long
TURN_IN_FLIGHT_S = 120.0
#: fraction of a ROW's content words a marked turn must hold for a write to be "that turn's content"
BLOCK_OVERLAP = 0.6
_MAX_USERS = 512
_MAX_MARKS = 8

#: the literal the transcript readers look for: ``chat_messages.metadata`` is TEXT holding ``json.dumps`` output
OFF_RECORD_JSON_MARK = '"off_record": true'


def off_record_sql(alias: str = "cm") -> str:
    """SQL condition excluding off-the-record transcript rows (``alias`` is the chat_messages alias). ``COALESCE`` so a NULL
    metadata column (most rows) stays included. No question marks: safe beside the asyncpg positional-compat layer."""
    return f"COALESCE({alias}.metadata, '') NOT LIKE '%{OFF_RECORD_JSON_MARK}%'"


def enabled() -> bool:
    """``ZOE_MEMORY_PROVENANCE_ANSWERS`` - default ON, per-call read; ``0|false|no|off`` (or set-but-empty) = every function
    here does nothing and every turn is exactly what it was before BM5."""
    from typed_env import env_bool

    return env_bool("ZOE_MEMORY_PROVENANCE_ANSWERS", True)


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else float(now)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def text_hash(text: str) -> str:
    return hashlib.sha1(_norm(text).encode("utf-8")).hexdigest()[:20]


def content_words(text: str) -> frozenset:
    """The stemmed content words of a text (``exact_words.content_tokens``), as a set. Pure."""
    from exact_words import content_tokens

    return frozenset(content_tokens(text))


# ── state ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Source:
    """One thing a reply stood on. ``kind`` is ``row`` (a memory row id) or ``xw`` (one of the owner's own verbatim turns, an
    ``exact_words`` turn id); ``said_at`` is the turn's epoch for ``xw`` (0 for a row: the row has its own dates)."""

    kind: str
    id: str
    said_at: float = 0.0


@dataclass(frozen=True)
class ReplyRecord:
    seq: int                       # the user turn this reply answered
    ts: float
    kind: str                      # "brain" | "direct"
    tier: str = ""                 # for "direct": the tier label (identity, correction, tier1.5, ...)
    domain: str = ""               # for "direct": the expert domain that answered (time, lists, people, ...)
    sources: tuple = ()            # tuple[Source, ...], best first - IDS only, no text
    served: int = 0                # how many rows the packet held (used or not)
    session_id: str = ""
    extra: tuple = ()              # other stored-context blocks the reply was built with: "offer", "raise", "brief"


@dataclass
class Explained:
    """The row an answer just named. ``text`` is held in memory for one turn so a fragment correction can be applied to it."""

    seq: int
    row_id: str
    ts: float
    text: str = ""
    quote: str = ""
    awaiting_fix: bool = False    # Zoe asked "what's the right answer?" - the next turn is the answer
    turn_id: str = ""             # the answer quoted one of the owner's verbatim turns (exact_words) and named no row


@dataclass
class _Mark:
    h: str
    words: frozenset
    expires: float


@dataclass
class _State:
    seq: int = 0
    last_note_h: str = ""
    last_note_norm: str = ""
    last_note_session: str = ""
    last_note_ts: float = -1e9
    begin_ts: float = 0.0
    served: list = field(default_factory=list)       # [(ts, [(row_id, text)], [(turn_id, said_at, text)])]
    extras: set = field(default_factory=set)         # context blocks (offer / raise / brief) this brain turn carried
    last: Optional[ReplyRecord] = None
    explained: Optional[Explained] = None
    marks: deque = field(default_factory=lambda: deque(maxlen=_MAX_MARKS))
    pending_otr_until: float = 0.0
    cur_marked_seq: int = -1        # the turn (seq) that is off the record, whatever text variant a later hook sees
    cur_marked_ts: float = 0.0


_STATES: "OrderedDict[str, _State]" = OrderedDict()


#: ids that share one identity across many people - no per-user state is kept for them (a guest's turn is nobody's to explain)
UNTRACKED_USERS = frozenset({"", "guest", "anonymous", "voice-guest", "voice-daemon"})


def _uid(user_id: str) -> str:
    return (user_id or "").strip()


def _state(user_id: str, *, create: bool = True) -> Optional[_State]:
    uid = _uid(user_id)
    if uid.lower() in UNTRACKED_USERS or uid.lower().startswith("guest-"):
        return None
    st = _STATES.get(uid)
    if st is None:
        if not create:
            return None
        st = _STATES[uid] = _State()
        while len(_STATES) > _MAX_USERS:
            _STATES.popitem(last=False)
    else:
        _STATES.move_to_end(uid)
    return st


def reset(user_id: Optional[str] = None) -> None:
    """Forget the in-process state (tests; a user's right-to-be-forgotten path)."""
    if user_id is None:
        _STATES.clear()
    else:
        _STATES.pop(_uid(user_id), None)


# ── the turn ledger ──────────────────────────────────────────────────────────

def note_user_turn(user_id: str, text: str, session_id: str = "", *, now: Optional[float] = None) -> int:
    """Count one user turn and return its sequence number. The same turn seen twice (chat save + tier; voice save + tier)
    is one turn: a second sighting within ``SAME_TURN_S`` with the same text (or, in the same session, text that holds / is held by
    the first - the same turn under another wrapping) is not counted again.
    0 when the feature is off or there is no user. Never raises."""
    try:
        if not enabled():
            return 0
        st = _state(user_id)
        if st is None:
            return 0
        t = _now(now)
        norm, sid = _norm(text), (session_id or "").strip()
        h = text_hash(text)
        if t - st.last_note_ts <= SAME_TURN_S and (
                h == st.last_note_h
                # the same turn under another wrapping ("/openclaw x" -> "x"): same session and one holds the other
                or (sid and sid == st.last_note_session and len(min(norm, st.last_note_norm, key=len)) >= 4
                    and (norm in st.last_note_norm or st.last_note_norm in norm))):
            return st.seq
        st.seq += 1
        st.last_note_h, st.last_note_norm, st.last_note_session, st.last_note_ts = h, norm, sid, t
        return st.seq
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory_provenance.note_user_turn failed (%s)", type(exc).__name__)
        return 0


def current_seq(user_id: str) -> int:
    st = _state(user_id, create=False)
    return st.seq if st else 0


def begin_turn(user_id: str, *, now: Optional[float] = None) -> tuple:
    """A brain turn starts: ``(seq, begin_ts)`` for ``commit_brain_reply``. The served bucket of an earlier turn is dropped."""
    try:
        if not enabled():
            return (0, 0.0)
        st = _state(user_id)
        if st is None:
            return (0, 0.0)
        st.begin_ts = _now(now)
        st.served = []
        st.extras = set()
        return (st.seq, st.begin_ts)
    except Exception:  # noqa: BLE001
        return (0, 0.0)


def note_served(user_id: str, rows: Sequence = (), xw: Sequence = (), *, now: Optional[float] = None) -> None:
    """``/for-prompt`` handed the brain these rows (``(id, text)``) and owner turns (``(turn_id, said_at, text)``).
    Held until the reply ends, then reduced to ids. Bounded. Never raises."""
    try:
        if not enabled() or (not rows and not xw):
            return
        st = _state(user_id)
        if st is None:
            return
        st.served.append((_now(now), [(str(i), str(t or "")) for i, t in rows],
                          [(str(i), float(s or 0.0), str(t or "")) for i, s, t in xw]))
        del st.served[:-12]
    except Exception:  # noqa: BLE001
        return


def note_context(user_id: str, *kinds: str) -> None:
    """The Flue seam built this turn's message with stored context that does not come through ``/for-prompt`` (a pending contact
    offer, a proactive raise, the day brief): "why did you say that?" must not call such a reply memory-free. Labels only."""
    try:
        if not enabled() or not kinds:
            return
        st = _state(user_id)
        if st is not None:
            st.extras.update(k for k in kinds if k in ("offer", "raise", "brief"))
    except Exception:  # noqa: BLE001
        return


_STOPISH = frozenset("user users zoe know told tell said mention mentioned note notes".split())


def _used_score(fact_words: frozenset, reply_words: frozenset) -> tuple:
    """(shared count, shared fraction of the fact's words) - how much of a stored fact the reply restates."""
    if not fact_words:
        return (0, 0.0)
    shared = fact_words & reply_words
    return (len(shared), len(shared) / len(fact_words))


def _is_used(shared: int, frac: float) -> bool:
    return shared >= 2 or (shared >= 1 and frac >= 0.25)


def rank_sources(served: Iterable, reply: str, message: str = "") -> tuple:
    """``(sources, served_count)``: of the served rows / owner turns, those the reply restates, best first. Pure.

    A source is "used" when the reply shares at least two content words with it, or one that is a quarter of it. Ranked by the
    reply's overlap, then by the user's question's overlap (the better-matched fact when two both fit), then served order."""
    reply_w, msg_w = content_words(reply) - _STOPISH, content_words(message) - _STOPISH
    scored: list = []
    seen: set = set()
    order = 0
    total = 0
    for _ts, rows, xw in served:
        for rid, text in rows:
            if rid in seen:
                continue
            seen.add(rid)
            total += 1
            fw = content_words(text) - _STOPISH
            n, frac = _used_score(fw, reply_w)
            if _is_used(n, frac):
                scored.append((-n, -frac, -len(fw & msg_w), order, Source("row", rid)))
            order += 1
        for tid, said_at, text in xw:
            key = "xw:" + tid
            if key in seen:
                continue
            seen.add(key)
            total += 1
            fw = content_words(text) - _STOPISH
            n, frac = _used_score(fw, reply_w)
            if _is_used(n, frac):
                scored.append((-n, -frac, -len(fw & msg_w), order, Source("xw", tid, said_at)))
            order += 1
    scored.sort(key=lambda s: s[:4])
    return tuple(s[4] for s in scored), total


def commit_brain_reply(user_id: str, reply: str, message: str = "", session_id: str = "", *,
                       token: tuple = (0, 0.0), now: Optional[float] = None) -> Optional[ReplyRecord]:
    """A brain reply ended: record what it stood on. ``token`` is ``begin_turn``'s. Returns the record (None when the feature
    is off, there is no reply, or no user). Never raises."""
    try:
        if not enabled() or not (reply or "").strip():
            return None
        st = _state(user_id)
        if st is None:
            return None
        seq, begin_ts = token if token and token[0] else (st.seq, st.begin_ts)
        window = [s for s in st.served if s[0] >= begin_ts - 0.001]
        sources, total = rank_sources(window, reply, message)
        rec = ReplyRecord(seq=seq, ts=_now(now), kind="brain", sources=sources, served=total, session_id=session_id or "",
                          extra=tuple(sorted(st.extras)))
        st.last = rec
        st.served = []
        st.extras = set()
        logger.info("PROVENANCE_REPLY user=%s seq=%d kind=brain served=%d used=%d", _uid(user_id), seq, total, len(sources))
        return rec
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory_provenance.commit_brain_reply failed (%s)", type(exc).__name__)
        return None


def note_direct_reply(user_id: str, tier: str, session_id: str = "", *, domain: str = "",
                      now: Optional[float] = None) -> Optional[ReplyRecord]:
    """A deterministic tier answered this turn (no memory packet): record it so "why did you say that?" can say so."""
    try:
        if not enabled():
            return None
        st = _state(user_id)
        if st is None:
            return None
        rec = ReplyRecord(seq=st.seq, ts=_now(now), kind="direct", tier=(tier or "")[:40], domain=(domain or "")[:40],
                          session_id=session_id or "")
        st.last = rec
        return rec
    except Exception:  # noqa: BLE001
        return None


def previous_reply(user_id: str, *, now: Optional[float] = None) -> Optional[ReplyRecord]:
    """The record of the reply to the turn BEFORE the current one, or None. None means "I cannot tell", never "no memory"."""
    try:
        if not enabled():
            return None
        st = _state(user_id, create=False)
        if st is None or st.last is None:
            return None
        if st.last.seq != st.seq - 1 or _now(now) - st.last.ts > REPLY_TTL_S:
            return None
        return st.last
    except Exception:  # noqa: BLE001
        return None


def mark_explained(user_id: str, row_id: str, *, text: str = "", quote: str = "", awaiting_fix: bool = False,
                   turn_id: str = "", now: Optional[float] = None) -> None:
    """An answer named this row (or, with ``turn_id``, quoted this verbatim turn of the owner's): the NEXT turn's "forget it" /
    "that's wrong, it's X" mean it."""
    try:
        if not enabled():
            return
        st = _state(user_id)
        if st is not None and (row_id or turn_id):
            st.explained = Explained(seq=st.seq, row_id=row_id, ts=_now(now), text=text, quote=quote,
                                     awaiting_fix=awaiting_fix, turn_id=turn_id)
    except Exception:  # noqa: BLE001
        return


def explained(user_id: str, *, now: Optional[float] = None) -> Optional[Explained]:
    """The row the previous answer named, while this is the turn right after it (and within ``EXPLAINED_TTL_S``)."""
    try:
        if not enabled():
            return None
        st = _state(user_id, create=False)
        if st is None or st.explained is None:
            return None
        if st.explained.seq != st.seq - 1 or _now(now) - st.explained.ts > EXPLAINED_TTL_S:
            return None
        return st.explained
    except Exception:  # noqa: BLE001
        return None


def clear_explained(user_id: str) -> None:
    st = _state(user_id, create=False)
    if st is not None:
        st.explained = None


# ── off the record ───────────────────────────────────────────────────────────

_FILLER = r"(?:(?:ok(?:ay)?|hey|hi|so|right|now|look|listen|alright|well|um+|uh+|zoe|and|just|oh|please)[,.\s]+)*"
_OTR_CUE = (
    r"(?P<cue>off[\s\-]+the[\s\-]+record"
    r"|(?:do\s*n['’]?t|do\s+not)\s+(?:remember|save|store|keep|log|record)\s+(?:this|what\s+i['’]?m\s+(?:about\s+to\s+)?(?:say|tell)|what\s+i\s+(?:say|tell))"
    r"|(?:this|it|that)\s+(?:stays|is\s+just|is|stays\s+just)\s+between\s+(?:us|you\s+and\s+me)"
    r"|(?:keep|leave)\s+(?:this|it)\s+between\s+(?:us|you\s+and\s+me)"
    r"|(?:just\s+)?between\s+(?:us|you\s+and\s+me))"
)
_SEP = r"(?:\s*[,:;.\-–—]+\s*|\s+)"
#: cue first: "Off the record: my brother ...", "Off the record." (bare), "Don't remember this, I'm ..."
_OTR_LEAD_RE = re.compile(
    rf"^\s*{_FILLER}(?:(?:this\s+is|that['’]?s|keep\s+this|can\s+we\s+go|could\s+we\s+go|going|let['’]?s\s+go|i['’]?m\s+going)\s+)?"
    rf"{_OTR_CUE}(?:\s+(?:please|ok|okay))?\s*[?!]*(?P<rest>(?:{_SEP}.*)?)$", re.IGNORECASE | re.DOTALL)
#: cue last: "My sister is pregnant, this stays between us"
_OTR_TAIL_RE = re.compile(
    rf"^(?P<payload>.{{6,}}?)[,.;:\-–—]\s*(?:and\s+)?(?:please\s+)?{_OTR_CUE}\W*$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class OffRecord:
    """An off-the-record cue. ``payload`` is what followed (or preceded) it in the SAME utterance - "" for a bare cue, which
    arms the next turn."""

    cue: str
    payload: str


def _cue_label(raw: str) -> str:
    c = re.sub(r"[\s\-]+", " ", raw.lower().replace("’", "'")).strip()
    if "record" in c and "off" in c:
        return "off_the_record"
    if "between" in c:
        return "between_us"
    return "dont_remember"


def parse_off_record(text: str) -> Optional[OffRecord]:
    """The off-the-record cue in ``text`` or None. Pure. The cue must OPEN the turn (after a filler like "ok" / "zoe") or CLOSE it
    after a comma: "what does off the record mean?" and "is this off the record?" are questions about the phrase, not cues;
    "can we go off the record?" is one (a bare cue)."""
    t = (text or "").strip()
    if not t or len(t) > 1200:
        return None
    m = _OTR_LEAD_RE.match(t)
    if m:
        rest = (m.group("rest") or "").strip(" \t\r\n,:;.-–—")
        return OffRecord(_cue_label(m.group("cue")), rest)
    m = _OTR_TAIL_RE.match(t)
    if m:
        payload = m.group("payload").strip()
        return OffRecord(_cue_label(m.group("cue")), payload)
    return None


def _live_marks(st: _State, t: float) -> list:
    return [m for m in st.marks if m.expires >= t]


def _in_flight(st: _State, t: float) -> bool:
    """Is the user turn being processed right now (the latest numbered one) the marked one?"""
    return st.seq > 0 and st.cur_marked_seq == st.seq and t - st.cur_marked_ts <= TURN_IN_FLIGHT_S


def _mark_current(st: _State, t: float) -> None:
    st.cur_marked_seq, st.cur_marked_ts = st.seq, t


def _add_mark(st: _State, text: str, t: float) -> None:
    st.marks.append(_Mark(text_hash(text), content_words(text), t + MARK_TTL_S))


def claim_turn(user_id: str, text: str, *, now: Optional[float] = None) -> bool:
    """Is this user turn off the record? Called at the FIRST sight of a turn (and again, idempotently, by every later hook).

    * the turn is already marked (same words, inside ``MARK_TTL_S``)        -> True
    * the turn carries a cue AND a payload                                   -> mark it, True (audit ``same_turn``)
    * the turn is a BARE cue                                                 -> arm the next turn, False (the cue itself is nothing)
    * a bare cue is pending (``PENDING_TTL_S``) and this is the next turn     -> mark it, True (audit ``next_turn``)

    Never raises; False when the feature is off."""
    try:
        if not enabled() or not (text or "").strip():
            return False
        st = _state(user_id)
        if st is None:
            return False
        t = _now(now)
        h = text_hash(text)
        if any(m.h == h for m in _live_marks(st, t)):
            if st.last_note_h == h:
                _mark_current(st, t)
            return True
        cue = parse_off_record(text)
        if cue is not None:
            if cue.payload:
                _add_mark(st, text, t)
                _mark_current(st, t)
                _audit(user_id, cue.cue, "same_turn", text)
                st.pending_otr_until = 0.0
                return True
            if st.pending_otr_until < t:      # a repeat of the bare cue within its window is not a new arming
                _audit(user_id, cue.cue, "armed", "")
            st.pending_otr_until = t + PENDING_TTL_S
            return False
        if st.pending_otr_until >= t:
            st.pending_otr_until = 0.0
            _add_mark(st, text, t)
            _mark_current(st, t)
            _audit(user_id, "pending", "next_turn", text)
            return True
        return False
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory_provenance.claim_turn failed (%s)", type(exc).__name__)
        return False


def is_off_record(user_id: str, text: str, *, now: Optional[float] = None) -> bool:
    """Exact-turn test for the per-turn writers (extractor, digest, person extractors, exact-words index, transcript save): is
    THIS turn marked? Does not arm or claim anything. Never raises."""
    try:
        if not enabled() or not (text or "").strip():
            return False
        st = _state(user_id, create=False)
        if st is None:
            return False
        h = text_hash(text)
        t = _now(now)
        if _in_flight(st, t):      # the turn in flight is marked, whichever text variant a hook holds
            return True
        return any(m.h == h for m in _live_marks(st, t))
    except Exception:  # noqa: BLE001
        return False


def reply_is_off_record(user_id: str, *, now: Optional[float] = None) -> bool:
    """Is the user's CURRENT turn (the one a reply is being saved for) off the record? The assistant's reply to a marked turn is
    not indexed either - it can restate the words."""
    try:
        if not enabled():
            return False
        st = _state(user_id, create=False)
        if st is None or not st.last_note_h:
            return False
        t = _now(now)
        if _in_flight(st, t):
            return True
        return any(m.h == st.last_note_h for m in _live_marks(st, t))
    except Exception:  # noqa: BLE001
        return False


def blocks_write(user_id: str, text: str, excerpt: str = "", *, now: Optional[float] = None) -> bool:
    """Would storing this row store what a marked turn said? True when the row's evidence IS a marked turn (same hash), or
    when most of the row's content words (``BLOCK_OVERLAP``) are the marked turn's. The ONE choke point is
    ``MemoryService.ingest``. A row about something else is never blocked. Never raises."""
    try:
        if not enabled():
            return False
        st = _state(user_id, create=False)
        if st is None:
            return False
        marks = _live_marks(st, _now(now))
        if not marks:
            return False
        if excerpt and any(m.h == text_hash(excerpt) for m in marks):
            return True
        rw = content_words(text)
        if len(rw) < 2:
            return False
        return any(len(rw & m.words) / len(rw) >= BLOCK_OVERLAP for m in marks)
    except Exception:  # noqa: BLE001
        return False


def _audit(user_id: str, cue: str, mode: str, text: str) -> None:
    logger.info("OFF_RECORD user=%s cue=%s mode=%s chars=%d", _uid(user_id), cue, mode, len(text or ""))
