""""Forget that" means the owner's PREVIOUS TURN - never "the newest memory write" (QA round 3b, 2026-11-07).

The bug. ``memory_forget_last`` used to retract the newest memory WRITE of the last 10 minutes. The post-turn capture (regex
extractor + the LLM turn digest + the person extractors) runs 10-30 minutes behind an active chat, so the newest write was
almost never what the owner had just said: "i secretly hate the colour of our kitchen" -> "forget that" erased an unrelated,
important row about a relative's fall, and the kitchen fact landed later anyway. And "no, don't remember that, forget that"
answered by the brain left the previous turn's capture running, so both rows were stored and survived three forgets.

The contract here:

* Every saved user turn is noted (``note_user_turn``, called from ``routers/chat._save_chat_message`` - every lane saves its
  user turns there) as one turn INSTANCE with its own id (``Turn.tid``): the same words said twice are two turns. The text is
  kept in-process for ``TURN_TTL_S`` only, at most ``_MAX_TURNS`` per user.
* ``previous_turn`` is the newest noted turn that is not itself a forget command: what "forget that" points at.
* ``retract`` installs the WALL for that turn instance for ``RETRACT_TTL_S`` (longer than the worst measured capture lag):
  ``blocks_write`` refuses every write that turn's capture makes. The capture runs inside ``capture_scope`` (a context
  variable ``routers/chat`` sets around the capture passes; asyncio tasks inherit it, so the digest's late write carries it),
  which names the turn INSTANCE it captures - another turn's capture, even one with the same words, is never blocked. A write
  made outside any capture scope is refused only when its evidence excerpt IS a retracted turn's words.
  ``MemoryService.ingest`` and ``MemoryService.review(edit)`` consult it (an explicit re-teach by the owner is exempt, exactly
  like the forget ledger).
* ``rows_from_turn`` finds the rows already written FROM that turn: a row carrying a saved-turn id (``source_turn``, stamped at
  write time by ``turn_ref``) by that id alone (``Turn.mids``); an OLD row without one by its ``user_turn_id`` (the extractors'
  hash, or the ask-to-remember id ``ask_to_remember.turn_ids_for``) or a source excerpt that is exactly the turn's words AS SAID -
  never a redacted excerpt, never "contains", and never a row written before the turn was said.
* ``mark_forgotten`` drops the turn's words from the notes, ONLY once every row was removed: until then the target stays, so
  "forget that" again retries the same turn (a store failure must never leave rows behind a "done").

In-process only: a restart drops the notes (and also kills every in-flight capture), so "forget that" after a restart finds no
turn and asks which thing to forget instead of guessing. The durable half (the nightly digest re-reading ``chat_messages``) is
``exact_words.forget_transcript``, which flags the saved turn ``off_record``.
"""
from __future__ import annotations

import contextvars
import hashlib
import itertools
import logging
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: How long a saved user turn stays a "forget that" target.
TURN_TTL_S = 1800.0
#: How long a retracted turn's capture is refused (the measured capture lag reached 30+ minutes, QA round 3b).
RETRACT_TTL_S = 6 * 3600.0
#: A row is "from" a turn only if it was written no earlier than this before the turn was saved (clock slack).
ROW_SLACK_S = 5.0
#: How many recent rows (per status) are read when looking for a turn's rows.
SCAN_LIMIT = 200
_MAX_USERS = 512
_MAX_TURNS = 6
_MAX_RETRACTED = 16

_LOCK = threading.Lock()
_IDS = itertools.count(1)


@dataclass
class Turn:
    text: str
    ts: float
    h: str
    tid: int = 0
    # the saved-turn (``chat_messages.id``) ids of this turn: its identity in every store a forget erases BY ID (the voice lane
    # saves one turn twice, so there can be two)
    mids: tuple = ()


@dataclass(frozen=True)
class _Retracted:
    tid: int
    h: str
    until: float


_turns: "OrderedDict[str, deque]" = OrderedDict()
_retracted: "OrderedDict[str, list]" = OrderedDict()
# (user_id, turn instance id or 0 when the turn was never noted, turn hash) of the capture running in this context
_CAPTURE: contextvars.ContextVar = contextvars.ContextVar("zoe_turn_forget_capture", default=None)


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else float(now)


def norm(text: str) -> str:
    t = (text or "").replace("’", "'").replace("‘", "'").lower()
    return re.sub(r"\s+", " ", t).strip(" \t\r\n.,!?;:\"'")


def turn_hash(text: str) -> str:
    return hashlib.sha1(norm(text).encode("utf-8", "ignore")).hexdigest()[:20]


def extractor_turn_ids(text: str) -> frozenset:
    """The ``user_turn_id`` prefixes the per-turn writers mint for ``text`` (``memory_extractor`` / ``memory_digest``:
    ``sha1(message)[:16]``, on the raw and on the stripped message)."""
    out = set()
    for variant in {text or "", (text or "").strip()}:
        if variant:
            out.add(hashlib.sha1(variant.encode("utf-8", "ignore")).hexdigest()[:16])
    return frozenset(out)


def _tracked(user_id: Optional[str]) -> bool:
    try:
        from user_filters import is_unidentified

        return not is_unidentified(user_id)
    except Exception:  # noqa: BLE001
        return bool((user_id or "").strip()) and (user_id or "").strip().lower() != "guest"


def is_forget_turn(text: str) -> bool:
    """Is ``text`` itself a retraction ("forget that", "no, don't remember that", "... forget that please")? Never raises."""
    t = (text or "").strip()
    if not t:
        return False
    try:
        import memory_quality

        if memory_quality.is_forget_command(t) or memory_quality.has_forget_wording(t):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from intent_router import _FORGET_LAST_RE

        return bool(_FORGET_LAST_RE.match(t))
    except Exception:  # noqa: BLE001
        return False


def instruction_patterns() -> list:
    """The compiled patterns ``is_forget_instruction`` decides by (through ``is_forget_turn``, ``memory_quality``, ``forget_topic``
    and ``intent_router``): ONE list, so a reader that needs a coarse prefilter (``scripts/maintenance/forget_command_rows_audit.py``)
    derives it from the detector itself instead of keeping a second word list. Pinned: blanking every pattern here makes the
    detector accept nothing (``tests/test_forget_command_rows_audit.py``)."""
    import forget_topic
    import memory_quality
    from intent_router import _FORGET_ENTITY_RE, _FORGET_LAST_RE

    return [memory_quality._FORGET_WHOLE_RE, memory_quality._FORGET_I_SAID_RE, memory_quality._FORGET_CLAUSE_RE,
            _FORGET_LAST_RE, _FORGET_ENTITY_RE, forget_topic._TOPIC_RE, forget_topic._CLAUSE_RE]


def is_forget_instruction(text: str) -> bool:
    """Is ``text`` a FORGET COMMAND of any kind: a short retraction of the previous turn (``is_forget_turn``, bounded by
    ``memory_quality.FORGET_MAX_WORDS`` exactly as the chat capture bounds it), a TOPIC forget ("forget that I told you about
    my brother", ``forget_topic.parse``) or an ENTITY forget ("forget everything about Dana", ``intent_router._FORGET_ENTITY_RE``)?
    The union of the existing detectors, no pattern of its own. Pure; never raises.

    Such a turn is an instruction, never a fact: its saved row is flagged off the record and every transcript reader skips it
    (QA round 10a: a topic forget writes no ledger entry, so idle consolidation re-mined the command itself into "the user told
    Zoe about their brother", and Zoe then reported "a record of a past conversation about him")."""
    t = (text or "").strip()
    if not t:
        return False
    try:
        import memory_quality

        if len(t.split()) <= memory_quality.FORGET_MAX_WORDS and is_forget_turn(t):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        import forget_topic

        if forget_topic.parse(t) is not None:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from intent_router import _FORGET_ENTITY_RE

        return bool(_FORGET_ENTITY_RE.match(t))
    except Exception:  # noqa: BLE001
        return False


# ── the turn notes ───────────────────────────────────────────────────────────────────────────────────────────────────

def note_user_turn(user_id: Optional[str], text: str, *, now: Optional[float] = None, mid: str = "") -> None:
    """Record a saved USER turn and its saved-turn id ``mid``. Only identified users; a repeat of the newest turn (the voice lane
    saves its turn twice) is one turn, and its id joins that turn's ``mids``. Never raises."""
    try:
        clean = (text or "").strip()
        if not clean or not _tracked(user_id):
            return
        t = _now(now)
        h = turn_hash(clean)
        with _LOCK:
            dq = _turns.get(user_id)
            if dq is None:
                dq = _turns[user_id] = deque(maxlen=_MAX_TURNS)
                while len(_turns) > _MAX_USERS:
                    _turns.popitem(last=False)
            _turns.move_to_end(user_id)
            if dq and dq[-1].h == h and dq[-1].text:
                if mid and mid not in dq[-1].mids:
                    dq[-1].mids = dq[-1].mids + (str(mid),)
                return
            dq.append(Turn(clean, t, h, next(_IDS), (str(mid),) if mid else ()))
    except Exception:  # noqa: BLE001 - bookkeeping must never lose a turn
        logger.debug("turn_forget.note_user_turn failed", exc_info=True)


def previous_turn(user_id: Optional[str], *, now: Optional[float] = None) -> Optional[Turn]:
    """The owner's newest saved turn that is not itself a forget command (within ``TURN_TTL_S``), or None. A turn already fully
    forgotten comes back with ``text == ""`` - a second "forget that" must answer "already forgotten", never walk on to the
    turn before it."""
    t = _now(now)
    with _LOCK:
        turns = list(_turns.get(user_id or "") or ())
    for turn in reversed(turns):
        if t - turn.ts > TURN_TTL_S:
            return None
        if turn.text and is_forget_turn(turn.text):
            continue
        return Turn(turn.text, turn.ts, turn.h, turn.tid, turn.mids)
    return None


def note_saved_copy(user_id: Optional[str], text: str, mid: str) -> None:
    """A second saved row of an already-noted turn (the voice lane's save with the reply): its id joins that turn's ``mids``, so a
    forget reaches both rows. Never adds a turn. Never raises."""
    try:
        h = turn_hash((text or "").strip())
        with _LOCK:
            for turn in reversed(list(_turns.get(user_id or "") or ())):
                if turn.h == h:
                    if mid and mid not in turn.mids:
                        turn.mids = turn.mids + (str(mid),)
                    return
    except Exception:  # noqa: BLE001
        logger.debug("turn_forget.note_saved_copy failed", exc_info=True)


#: how recent the newest noted turn must be for an unscoped in-turn write to take its id by containment (``turn_ref``)
REF_WINDOW_S = 300.0


def turn_ref(user_id: Optional[str], raw: Any) -> str:
    """The saved-turn id a write derived from the words ``raw`` comes from, resolved at WRITE time while the words are still as
    said: the capture scope's own turn, else the ONE noted turn whose words are ``raw`` or begin with it (a writer may cut the
    excerpt). '' when none or several (the row then has no id, and a forget treats it as an old row). Never raises."""
    try:
        uid = user_id or ""
        cap = _CAPTURE.get()
        with _LOCK:
            turns = list(_turns.get(uid) or ())
        if cap is not None and cap[0] == uid and cap[1]:
            for t in turns:
                if t.tid == cap[1]:
                    return t.mids[0] if t.mids else ""
        key = norm(raw if isinstance(raw, str) else "")
        if len(key) < 6:
            return ""
        hits = {t.mids[0] for t in turns if t.mids and t.text and (norm(t.text) == key or norm(t.text).startswith(key))}
        if len(hits) == 1:
            return next(iter(hits))
        if hits:
            return ""
        # an in-turn write outside a capture scope (a brain tool's span, an ask's clause): the turn being handled is the NEWEST
        # noted one, and only when it holds the words and was said within ``REF_WINDOW_S``
        newest = turns[-1] if turns else None
        if newest is not None and newest.mids and newest.text and time.time() - newest.ts <= REF_WINDOW_S \
                and key in norm(newest.text):
            return newest.mids[0]
        return ""
    except Exception:  # noqa: BLE001
        return ""


def turns_matching(user_id: Optional[str], predicate, *, now: Optional[float] = None) -> list:
    """The owner's noted turns (within ``TURN_TTL_S``, not forget commands themselves) whose words satisfy ``predicate`` - the
    turns a TOPIC forget retracts. Copies; never raises."""
    try:
        t = _now(now)
        with _LOCK:
            turns = list(_turns.get(user_id or "") or ())
        return [Turn(x.text, x.ts, x.h, x.tid, x.mids) for x in turns
                if x.text and t - x.ts <= TURN_TTL_S and not is_forget_turn(x.text) and predicate(x.text)]
    except Exception:  # noqa: BLE001
        return []


def _newest_tid(user_id: str, h: str) -> int:
    with _LOCK:
        for turn in reversed(list(_turns.get(user_id) or ())):
            if turn.h == h:
                return turn.tid
    return 0


# ── retraction ───────────────────────────────────────────────────────────────────────────────────────────────────────

def retract(user_id: str, turn: Turn, *, now: Optional[float] = None) -> None:
    """Install the wall for this turn INSTANCE: its pending capture never lands. Idempotent; the words stay in the notes until
    ``mark_forgotten`` (so a failed cleanup can be retried). Never raises."""
    try:
        t = _now(now)
        with _LOCK:
            live = [e for e in (_retracted.get(user_id) or []) if e.until >= t and e.tid != turn.tid]
            live.append(_Retracted(turn.tid, turn.h, t + RETRACT_TTL_S))
            _retracted[user_id] = live[-_MAX_RETRACTED:]
            _retracted.move_to_end(user_id)
            while len(_retracted) > _MAX_USERS:
                _retracted.popitem(last=False)
        logger.info("TURN_FORGET user=%s retracted=1", user_id)
        import correction_marker

        correction_marker.forget_user(user_id, reason="forget_turn")   # any forget drops the member's pending corrections
    except Exception:  # noqa: BLE001
        logger.warning("turn_forget.retract failed", exc_info=True)


def mark_forgotten(user_id: str, turn: Turn) -> None:
    """Every row of the turn is gone: drop its words from the notes (it keeps its slot - it IS still the previous turn)."""
    with _LOCK:
        for tr in _turns.get(user_id) or ():
            if tr.tid == turn.tid:
                tr.text = ""
    try:  # the rows are flagged off the record by now: clear the pending corrections AGAIN, after the flagging (retract
        import correction_marker  # cleared them before it; a marker set in between must not outlive the forget)

        correction_marker.forget_user(user_id, reason="forget_turn")
    except Exception:  # noqa: BLE001
        logger.warning("turn_forget.mark_forgotten: marker clear failed", exc_info=True)


def _live(user_id: str, t: float) -> list:
    with _LOCK:
        return [e for e in (_retracted.get(user_id) or []) if e.until >= t]


class capture_scope:
    """``with capture_scope(user_id, user_message):`` marks the post-turn capture of that turn INSTANCE (the newest noted turn
    with those words) in this context and every task created inside it, so ``blocks_write`` can refuse its writes once that
    turn is retracted - and only that turn's."""

    def __init__(self, user_id: str, text: str) -> None:
        h = turn_hash(text)
        self._val = (user_id or "", _newest_tid(user_id or "", h), h)
        self._tok = None

    def __enter__(self) -> "capture_scope":
        self._tok = _CAPTURE.set(self._val)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._tok is not None:
            _CAPTURE.reset(self._tok)


def blocks_write(user_id: Optional[str], excerpt: str = "", *, now: Optional[float] = None) -> bool:
    """Would this write store what a retracted turn said? Inside a capture scope: only when the scope's turn INSTANCE was
    retracted (another turn with the same words is never blocked). Outside one: only when the write's evidence excerpt IS a
    retracted turn's words. Never raises."""
    try:
        live = _live(user_id or "", _now(now))
        if not live:
            return False
        cap = _CAPTURE.get()
        if cap is not None and cap[0] == user_id:
            if cap[1]:
                return any(e.tid == cap[1] for e in live)
            return any(e.h == cap[2] for e in live)      # a capture of a turn that was never noted: its words are all there is
        if excerpt and excerpt.strip():
            h = turn_hash(excerpt)
            return any(e.h == h for e in live)
        return False
    except Exception:  # noqa: BLE001
        return False


# ── the rows a turn produced ─────────────────────────────────────────────────────────────────────────────────────────

def _added_ts(meta: dict) -> Optional[float]:
    raw = (meta or {}).get("added_ts")
    if isinstance(raw, (int, float)):
        return float(raw)
    added = str((meta or {}).get("added_at") or "")
    if not added:
        return None
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(added.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:  # noqa: BLE001
        return None


_PLACEHOLDER = "[redacted]"
#: the explicit provenance a row from NO saved turn carries instead of an id (``memory_service`` stamps it at write time)
KIND_KEY = "source_kind"


TURN, KIND, LEGACY = "turn", "kind", "legacy"
_ENTRY_SEP, _ENTRY_KIND = "@", "src:"     # an exact-words entry id: ``xw-<key>[~<tag>]@<saved-turn id>`` or ``...@src:<kind>``


def classify(row: Any) -> tuple[str, str]:
    """THE one place a forget decides what a row IS, per row - for every store a forget reaches:

    * an exact-words entry id (a ``str``): ``@<id>`` / ``@src:<kind>`` / no ``@``;
    * a night-mind observation (a mapping with ``quote`` and ``turn_id``): its ``turn_id`` / none;
    * a memory row (``.metadata``) or its metadata: ``source_turn`` / ``source_kind`` / neither.

    Returns ``(TURN, <saved-turn id>)`` - erased by that id only; ``(KIND, <non-turn source kind>)`` - bound to a source that is
    no saved turn: only that row goes, by its own key, and nothing else is matched for it (no transcript, no words); or
    ``(LEGACY, "")`` - written before provenance was stamped: the ONLY kind a text matcher is ever handed."""
    if isinstance(row, str):
        if _ENTRY_SEP not in row:
            return LEGACY, ""
        p = row.rpartition(_ENTRY_SEP)[2]
        if not p:
            return LEGACY, ""
        return (KIND, p[len(_ENTRY_KIND):]) if p.startswith(_ENTRY_KIND) else (TURN, p)
    meta = getattr(row, "metadata", row)
    meta = meta if isinstance(meta, dict) else {}
    if "quote" in meta and "turn_id" in meta and "source_turn" not in meta:
        tid = str(meta.get("turn_id") or "").strip()
        return (TURN, tid) if tid else (LEGACY, "")
    src = str(meta.get("source_turn") or "")
    if src:
        return TURN, src
    kind = str(meta.get(KIND_KEY) or "")
    return (KIND, kind) if kind else (LEGACY, "")


def provenance(meta: Optional[dict]) -> tuple[str, str]:
    """``classify`` of a memory row's metadata (kept name)."""
    return classify(meta or {})


def split(rows: Any, key: Any = None) -> tuple[list, list]:
    """``(bound, legacy)`` by ``classify`` (``key`` picks what to classify from each row): a bound row (TURN or KIND) never
    meets a text matcher; only a legacy row does. Every store's partition is this."""
    bound, legacy = [], []
    for r in rows or ():
        (legacy if classify(key(r) if key else r)[0] == LEGACY else bound).append(r)
    return bound, legacy


def partition(rows: Any) -> tuple[list, list]:
    """``(bound, legacy)`` of memory rows (``.metadata``): ``split``."""
    return split(rows)


def target_of(entry_id: str = "", meta: Optional[dict] = None) -> tuple[tuple, bool]:
    """What a forget TARGET (an explained answer's exact-words entry and / or its memory row) names: ``(mids, source_only)``.
    ``mids`` = its saved-turn ids; ``source_only`` = it names no saved turn but IS bound to a non-turn source (only that row goes,
    nothing is matched for it); neither = a legacy target (the words fallback)."""
    mids, kinds = [], 0
    for c in (classify(entry_id) if entry_id else (LEGACY, ""), classify(meta) if meta else (LEGACY, "")):
        if c[0] == TURN and c[1] not in mids:
            mids.append(c[1])
        elif c[0] == KIND:
            kinds += 1
    return tuple(mids), bool(kinds and not mids)


def scrubbed_form(text: str) -> str:
    """``text`` as the write boundary would have stored it as an excerpt (what a legacy redacted excerpt is compared with)."""
    try:
        from memory_service import scrub_source_excerpt

        return norm(scrub_source_excerpt(text) or "")
    except Exception:  # noqa: BLE001
        return ""


def _legacy_row_is_from_turn(meta: dict, turn_text: str, turn_ts: Optional[float], turn_ids: frozenset) -> bool:
    """The TEXT matcher, for a LEGACY row only (``partition``): its ``user_turn_id`` is the turn's hash or one of ``turn_ids``
    (the ask-to-remember id), or its excerpt is exactly the turn's words as said - or, an excerpt the boundary redacted, exactly
    the turn's words as the boundary would have stored them (a look-alike turn that redacts alike goes too: over-forgetting is
    accepted HERE, never keeping words the owner forgot). Never "one contains the other", never a row written before the turn."""
    if turn_ts is not None:
        ts = _added_ts(meta)
        if ts is not None and ts < turn_ts - ROW_SLACK_S:
            return False
    tid = str(meta.get("user_turn_id") or "")
    if tid and (tid in turn_ids or any(tid.startswith(p) for p in extractor_turn_ids(turn_text))):
        return True
    ex, key = norm(str(meta.get("source_excerpt") or "")), norm(turn_text)
    if not (ex and key):
        return False
    return ex == key or (_PLACEHOLDER in ex and ex == scrubbed_form(turn_text))


def row_is_from_turn(meta: dict, turn_text: str, turn_ts: Optional[float] = None, *,
                     turn_ids: frozenset = frozenset(), mids: tuple = ()) -> bool:
    """Was a row (its metadata) written FROM this turn? A row with a saved-turn id is the turn's exactly when that id is one of
    ``mids`` - its words are never consulted; a row of a non-turn source kind is no turn's; only a LEGACY row (written before the
    ids) is matched by its words (``_legacy_row_is_from_turn``)."""
    kind, value = provenance(meta)
    if kind == "turn":
        return value in set(mids or ())
    if kind == "kind":
        return False
    return _legacy_row_is_from_turn(meta or {}, turn_text, turn_ts, turn_ids)


async def rows_from_turn(svc: Any, user_id: str, turn_text: str, turn_ts: Optional[float] = None, *,
                         mids: tuple = ()) -> list:
    """The owner's approved / pending rows written from this turn (``mids`` = its saved-turn ids): the bound rows by id alone,
    the legacy rows by their words (``partition``; a legacy match is logged ``TURN_FORGET code=legacy_text n=``). RAISES on a
    store failure (a forget must not be confirmed when the store could not be read)."""
    rows, seen = [], set()
    for status in ("approved", "pending"):
        for r in await svc.list_by_status(user_id=user_id, status=status, limit=SCAN_LIMIT):
            m = r.metadata or {}
            if r.id in seen or (m.get("user_id") or m.get("wing") or user_id) != user_id:
                continue
            seen.add(r.id)
            rows.append(r)
    bound, legacy = partition(rows)
    want = set(mids or ())
    out = [r for r in bound if provenance(r.metadata)[0] == "turn" and provenance(r.metadata)[1] in want]
    if legacy:
        try:
            import ask_to_remember

            turn_ids = ask_to_remember.turn_ids_for(user_id, turn_text)
        except Exception:  # noqa: BLE001
            turn_ids = frozenset()
        old = [r for r in legacy if _legacy_row_is_from_turn(r.metadata or {}, turn_text, turn_ts, turn_ids)]
        if old:
            logger.info("TURN_FORGET code=legacy_text n=%d", len(old))
        out += old
    return out


async def resolve_legacy_ids(user_id: str, texts: Any) -> tuple:
    """The saved-turn ids a LEGACY target's words resolve to (``exact_words.legacy_transcript_ids``: the transcript turns holding
    them, as said or as redacted; over-inclusive by design). Resolved FIRST and handed back to the caller, so everything bound to
    those turns - facts written by the upgraded writers, observations, entries - goes too, not only what carries no id. RAISES on a
    store failure (the caller must not confirm)."""
    import exact_words

    out: list = []
    for w in dict.fromkeys(t for t in (texts or ()) if t and str(t).strip()):
        out += [m for m in await exact_words.legacy_transcript_ids(user_id, w) if m not in out]
    if out:
        logger.info("TURN_FORGET code=legacy_resolved ids=%d", len(out))
    return tuple(out)


async def erase_turn_words(user_id: str, mids: Any = (), texts: Any = (), *, resolved: bool = False) -> int:
    """Erase what a forgotten turn's WORDS left outside the memory rows - the saved transcript turn (flagged ``off_record``), the
    night mind's observations, the exact-words entries - and return how much. THE one dispatcher every turn forget uses:

    Bound versus legacy is decided PER ROW, never per target:

    * BOUND rows (a transcript turn named by id, an observation / entry carrying a saved-turn id) are erased by the ids ``mids``
      only - their words are never compared, however alike.
    * LEGACY rows (an observation / entry written with no id) are matched by the turn's words ``texts`` whether or not the target
      has ids (a turn backfilled after the upgrade has an id while its older rows still do not); over-inclusive there by design.
    * With no ids at all, the words also resolve to the saved turns holding them (``legacy_transcript_ids``), which are then erased
      by id. Logged ``TURN_FORGET code=legacy_text``.

    The transcript and the night mind FIRST, the exact-words entries last: a failure part-way RAISES with the entries still there,
    so a retry reaches everything again. RAISES on a store failure (the caller must not confirm)."""
    import exact_words
    import night_mind

    ids = [str(m) for m in dict.fromkeys(mids or ()) if m]
    # no ids, or ids the caller RESOLVED from a legacy target's words (``resolve_legacy_ids``): the transcript is flagged from the
    # words' own read (``forget_transcript``), exactly the rows those ids came from
    resolve = not ids or resolved
    words = [w for w in dict.fromkeys(texts or ()) if w and w.strip()]
    if not ids and not words:
        return 0
    if words:
        logger.info("TURN_FORGET code=legacy_text texts=%d", len(words))
    n, failure = 0, None

    async def step(coro_fn, *args):
        # fail closed AND erase everything reachable: a store that fails does not stop the erasers after it; the first
        # failure is re-raised once all of them ran, so the caller still does not confirm
        nonlocal n, failure
        try:
            n += int(await coro_fn(*args) or 0)
        except Exception as exc:  # noqa: BLE001
            failure = failure or exc
            logger.warning("TURN_FORGET code=eraser_failed step=%s (%s)", getattr(coro_fn, "__name__", "?"), type(exc).__name__)

    if resolve:
        for w in words:
            await step(exact_words.forget_transcript, user_id, w)                  # the transcript flag, from the one read
            try:
                ids += [m for m in await exact_words.legacy_transcript_ids(user_id, w) if m not in ids]
            except Exception as exc:  # noqa: BLE001
                failure = failure or exc
                logger.warning("TURN_FORGET code=eraser_failed step=legacy_transcript_ids (%s)", type(exc).__name__)
    else:
        await step(exact_words.flag_transcript_turns, user_id, ids)
    for mid in ids:
        await step(night_mind.erase_words, user_id, mid)
    if words:
        await step(night_mind.erase_words, user_id, "", *words)              # LEGACY observations (no turn id), by their words
    # the exact-words entries LAST, and only when everything before held: an entry left behind keeps the retry able to find them
    if failure is None and ids:
        await step(exact_words.erase_turns, user_id, ids)
    if failure is None:
        for w in words:
            await step(exact_words.erase_text, user_id, w)                    # LEGACY entries (no id), by their words
    if failure is not None:
        raise failure
    return n


def reset() -> None:  # tests
    with _LOCK:
        _turns.clear()
        _retracted.clear()


__all__ = [
    "Turn", "note_user_turn", "previous_turn", "retract", "mark_forgotten", "capture_scope", "blocks_write",
    "rows_from_turn", "row_is_from_turn", "is_forget_turn", "reset", "provenance", "partition", "erase_turn_words", "classify",
    "split", "target_of", "resolve_legacy_ids",
]
