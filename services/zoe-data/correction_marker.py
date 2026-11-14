"""The same-turn PENDING-CORRECTION marker (memory redesign step 1, ZOE_CORRECTION_PENDING_MARKER, default OFF).

After a member corrects a fact, the recall packet can serve the OLD stored row (next turn, or a new session on another panel)
until a rewrite lands, and a shape the rewriter misses never closes that gap. Post-turn, a correction-shaped turn of an identified
member leaves a marker (``note_turn``); the member's own packet then carries that turn's words, fetched from ``chat_messages`` BY
TURN ID at read time (``texts_for_packet``). No write power, so over-triggering is fine.

Holds ``{turn_id, said_at, hashed subject keys}`` per member, in process only: NO words, TTL 2 h, <= 16 per member, lost on
restart. Cleared by any forget (``forget_user``), by a completed rewrite (``clear``; the future claim pass), on TTL, and when the
turn is gone. Logs: counts and ids only.
"""
from __future__ import annotations

import hashlib
import re
import logging
import threading
import time
from collections import OrderedDict
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

TTL_S, MAX_PER_USER, MAX_USERS, MAX_SERVED, TEXT_CAP = 2 * 3600.0, 16, 64, 3, 240


class Marker(NamedTuple):
    turn_id: str
    said_at: float
    # sha256(folded subject)[:12], never the subject. Held ON PURPOSE (cross-review, PR 2471): a privacy feature, not a leak -
    # they are what lets a forget-by-subject clear the marker without the marker ever holding the words.
    keys: tuple = ()


_LOCK = threading.Lock()
_by_user: "OrderedDict[str, OrderedDict[str, Marker]]" = OrderedDict()
# Per-member forget generation: ``forget_user`` (which every forget path calls, last of all) bumps it, and ``note_turn`` refuses
# to set a marker if it moved while it awaited. Bounded like the markers; evicting a member bumps ``_epoch`` instead, so an
# in-flight snapshot can never match a reset counter.
_gen: "OrderedDict[str, int]" = OrderedDict()
_epoch = 0


def enabled() -> bool:
    from typed_env import flag

    return bool(flag("ZOE_CORRECTION_PENDING_MARKER"))


def _log(event: str, reason: str, user_id: str, n: int) -> None:
    if n:
        logger.info("PENDING_CORRECTION %s reason=%s user=%s n=%d", event, reason, user_id, n)


def correction_shaped(words: str) -> tuple:
    """(correction-shaped?, subject keys): ``value_correction.statement`` or ``memory_quality.looks_like_correction``."""
    import memory_quality
    import value_correction

    corr = value_correction.statement(words)
    if corr is not None:
        return True, (hashlib.sha256(" ".join(corr.subject.lower().split()).encode()).hexdigest()[:12],)
    return bool(memory_quality.looks_like_correction(words)), ()


def pending(user_id: str, *, now: Optional[float] = None) -> list:
    """The member's live markers, newest first (expired ones are dropped here)."""
    t = time.time() if now is None else now
    with _LOCK:
        marks = _by_user.get(user_id) or OrderedDict()
        old = [k for k, m in marks.items() if t - m.said_at > TTL_S]
        for k in old:
            marks.pop(k)
        live = sorted(marks.values(), key=lambda m: -m.said_at)
    _log("cleared", "ttl", user_id, len(old))
    return live


def put(user_id: str, turn_id: str, *, keys: tuple = (), now: Optional[float] = None) -> None:
    t = time.time() if now is None else now
    pending(user_id, now=t)
    with _LOCK:
        marks = _by_user.setdefault(user_id, OrderedDict())
        marks.pop(turn_id, None)
        marks[turn_id] = Marker(turn_id, t, tuple(keys))
        bound = max(0, len(marks) - MAX_PER_USER)
        for _ in range(bound):
            marks.popitem(last=False)
        _by_user.move_to_end(user_id)
        while len(_by_user) > MAX_USERS:
            _by_user.popitem(last=False)
    _log("cleared", "bound", user_id, bound)
    _log("set", "correction_turn", user_id, 1)


def clear(user_id: str, turn_id: str, reason: str = "rewritten") -> bool:
    """A rewrite for this turn completed (the claim pass calls this). True when a marker went."""
    with _LOCK:
        gone = (_by_user.get(user_id) or {}).pop(turn_id, None) is not None
    _log("cleared", reason, user_id, int(gone))
    return gone


def generation(user_id: str) -> tuple:
    with _LOCK:
        return _epoch, _gen.get(user_id, 0)


def retire_subject(user_id: str, keys: tuple, reason: str = "superseded") -> int:
    """A later correction about the same subject REWROTE the stored row: the member's older pending markers for that subject are
    retired, or the packet would put the older words above the now-correct row. Same subject = a shared subject key (sha256 of the
    folded ``value_correction.statement`` subject); a marker with no key (``looks_like_correction`` only) is never matched."""
    wanted = set(keys or ())
    if not wanted:
        return 0
    with _LOCK:
        marks = _by_user.get(user_id) or {}
        gone = [t for t, m in marks.items() if wanted & set(m.keys)]
        for t in gone:
            marks.pop(t)
    _log("cleared", reason, user_id, len(gone))
    return len(gone)


async def _opted_out(user_id: str) -> bool:
    """The member's ``memory_opt_out`` preference, read through ``user_prefs.is_memory_opted_out`` - the check every memory writer
    makes. Fails CLOSED here (the writers fail open so a fact is never lost; a marker is an extra, so a failed read keeps it out)."""
    try:
        import user_prefs

        return bool(await user_prefs.is_memory_opted_out(user_id))
    except Exception:  # noqa: BLE001
        return True


def forget_user(user_id: str, reason: str = "forget") -> int:
    """ANY forget for the member drops every marker of theirs and moves their forget generation. Sync."""
    global _epoch
    with _LOCK:
        n = len(_by_user.pop(user_id, None) or ())
        _gen[user_id] = _gen.pop(user_id, 0) + 1
        while len(_gen) > MAX_USERS:
            _gen.popitem(last=False)
            _epoch += 1
    _log("cleared", reason, user_id, n)
    return n


def reset() -> None:  # tests
    with _LOCK:
        _by_user.clear()
        _gen.clear()


# ── chat_messages seams (Postgres; the tests replace them) ──────────────────────────────────────────────────────────────

def _sql(tail: str) -> str:
    from memory_provenance import off_record_sql
    from user_filters import message_owner_expr

    return ("SELECT cm.id, cm.content FROM chat_messages cm JOIN chat_sessions cs ON cm.session_id = cs.id WHERE cm.role = 'user' "
            f"AND {message_owner_expr()} = ? AND {off_record_sql('cm')} {tail}")


def _db():
    from db_pool import get_db_ctx

    return get_db_ctx()


async def _rows(sql: str, params: tuple) -> list:
    async with _db() as db:
        rows = await (await db.execute(sql, params)).fetchall()
    return [(str(r["id"]), r["content"] or "") if isinstance(r, dict) else (str(tuple(r)[0]), tuple(r)[1] or "") for r in rows]


async def _exact_turns(user_id: str, session_id: str, want: str) -> tuple:
    """``(an id, how many)`` of the member's on-the-record user turns in the WHOLE session whose saved text equals ``want``,
    counted in SQL (no window: an identical older turn anywhere in the session makes it ambiguous)."""
    sql = _sql("AND cm.session_id = ? AND cm.content = ?").replace("SELECT cm.id, cm.content", "SELECT MIN(cm.id), COUNT(*)", 1)
    async with _db() as db:
        row = (await (await db.execute(sql, (user_id, session_id, want))).fetchall() or [(None, 0)])[0]
    row = tuple(row.values()) if isinstance(row, dict) else tuple(row)
    return (str(row[0]) if row[0] is not None else None), int(row[1] or 0)


def _saved_form(words: str) -> str:
    """The text ``routers.chat._save_chat_message`` stores for this turn: the user's own words (``_capture_words`` drops the voice
    lane's appended reply instructions), ``.strip()``-ed exactly as the save does."""
    try:
        from routers.chat import _capture_words

        words = _capture_words(words)
    except Exception:  # noqa: BLE001 - no chat router: the words are already the saved form
        pass
    return (words or "").strip()


async def _find_turn_id(user_id: str, session_id: str, words: str) -> Optional[str]:
    """``chat_messages.id`` of the ONE saved turn of this member in this session whose text EQUALS these words in saved form.
    None (logged ``not_found`` / ``ambiguous``) when no turn or more than one matches: a marker never binds a guessed turn."""
    want = _saved_form(words)
    turn_id, n = await _exact_turns(user_id, session_id, want) if want else (None, 0)
    if n != 1 or not turn_id:
        logger.info("PENDING_CORRECTION skipped reason=%s user=%s n=%d", "ambiguous" if n > 1 else "not_found", user_id, n)
        return None
    return turn_id


async def _fetch_texts(user_id: str, turn_ids: list) -> dict:
    """``{turn_id: content}`` for the member's OWN on-the-record user turns; a missing id is gone (or forgotten)."""
    return dict(await _rows(_sql(f"AND cm.id IN ({', '.join('?' for _ in turn_ids)})"), (user_id, *turn_ids)))


async def _names_forgotten(user_id: str, text: str) -> bool:
    """Does ``text`` name something the member asked to forget (the durable ledger or the fast tombstone)? The SAME test the
    memory reads and ``MemoryService.ingest`` apply. Fails CLOSED: an error counts as forgotten."""
    try:
        import memory_forgotten
        from memory_tombstones import matching_tombstone

        return bool(matching_tombstone(user_id, text) or await memory_forgotten.matches(user_id, text, strict=True))
    except Exception:  # noqa: BLE001
        return True


def not_replayable(text: str) -> Optional[str]:
    """Why ``text`` may NOT stand as the member's own correction in the packet (a reason code), or None. Checked before a marker
    is created AND again on the text fetched for the packet. Reuses the memory path's own tests, no new detector:

    * ``not_own_words``: ``own_words.analyze`` finds pasted content or third-person direct speech, or ``memory_gate._own_words``
      (quoted spans + reported speech: "mum said ...", "the email says ...") would remove anything. Unsure = not the member's.
    * ``pii``: ``memory_service.scrub_pii`` rejects the text or would redact any of it. The marker is DROPPED rather than replayed
      scrubbed: a redacted correction ("my PIN is [REDACTED], not ...") tells the brain nothing true, and a turn the scrubber
      trips is a secret-bearing turn, kept out of the replay entirely."""
    import memory_gate
    import own_words
    from memory_service import scrub_pii

    flat = " ".join((text or "").split())
    own = own_words.analyze(flat)
    if own.changed or not own.has_own or " ".join(memory_gate._own_words(flat).split()) != flat:
        return "not_own_words"
    scrubbed, reject = scrub_pii(flat)
    if reject or scrubbed != flat:
        return "pii"
    return None


_SUBJECT_STOP = frozenset("my our your his her their the a an this that it its is was not no actually sorry wait".split())


def _terms(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in _SUBJECT_STOP}


def outranked(text: str, rows: list) -> bool:
    """Would folding ``text`` as "these words win" contradict a row a spoken correction may not override? The rows it may be
    about share a word of its subject (``value_correction.statement``'s subject, else its content words: over-matching only
    withholds). Power = a verified member's own words (``memory_authority.USER_RANK``; an unverified speaker never has a marker);
    the target's class and ``may_override`` are memory_authority's own (curated / operator rows are beyond a sentence, whoever
    says it - a child included)."""
    import memory_authority as ma
    import value_correction

    corr = value_correction.statement(text)
    wanted = _terms(corr.subject if corr is not None else text)
    for r in rows or ():
        body = getattr(r, "text", "") or ""
        if wanted & _terms(body) and not ma.may_override(ma.USER_RANK, ma.row_class(getattr(r, "metadata", None) or {}, body)):
            return True
    return False


async def foldable(user_id: str, message: str, texts: list, rows: list) -> list:
    """The pending-correction texts that may be folded as "the newer words win": not outranked by a row they target (dropped, not
    turned into a question - a question still puts the overridden claim in front of the brain), and passed by the SAME restraint
    decision the recall packet's extra blocks get (``restraint.filter_extra``, its mode honoured exactly: ``off`` and ``shadow``
    keep everything, shadow logs; only ``enforce`` withholds). Not pulled: the marker is no lookup the member asked for."""
    if not texts:
        return []
    kept = [t for t in texts if not outranked(t, rows)]
    _log("withheld", "outranked", user_id, len(texts) - len(kept))     # from this packet only (the marker stays)
    if not kept:
        return []
    import restraint

    ok = await restraint.filter_extra(user_id, message, kept, pull=False)
    out = [t for t, k in zip(kept, ok) if k]
    _log("withheld", "restraint", user_id, len(kept) - len(out))
    return out


# ── the two hooks ─────────────────────────────────────────────────────────────────────────────────────────────────────────

async def note_turn(user_message: str, user_id: str, session_id: Optional[str], *, speaker_verified: Optional[bool] = None,
                    rewrote: int = 0) -> bool:
    """Post-turn, from ``memory_extractor.extract_and_ingest`` after ``value_correction.apply`` (inside its identity and
    off-the-record gates). ``rewrote`` > 0: the rewriter already closed this turn. Never raises."""
    try:
        from user_filters import is_unidentified

        if not enabled() or speaker_verified is False or not session_id or is_unidentified(user_id):
            return False
        words = _saved_form(user_message)                       # the member's turn as saved (the voice suffix dropped)
        shaped, keys = correction_shaped(words)
        if rewrote > 0:                                         # the rewriter closed this turn: nothing pending for it, and its
            retire_subject(user_id, keys)                       # subject's older markers would now contradict the stored row
            return False
        if await _opted_out(user_id):                           # memory_opt_out: nothing is recorded
            logger.info("PENDING_CORRECTION skipped reason=opted_out user=%s n=1", user_id)
            return False
        why = not_replayable(words) if shaped else None          # only what the member asserted, and never a secret
        if why:
            logger.info("PENDING_CORRECTION skipped reason=%s user=%s n=1", why, user_id)
            return False
        snap = generation(user_id)                              # BEFORE the first await
        turn_id = await _find_turn_id(user_id, session_id, user_message) if shaped else None
        import turn_forget

        if not turn_id or turn_forget.blocks_write(user_id, user_message):   # not saved, or "forget that" won the race
            return False
        if await _names_forgotten(user_id, user_message):    # a forgotten subject stays forgotten until the member re-teaches it
            logger.info("PENDING_CORRECTION skipped reason=forgotten user=%s n=1", user_id)
            return False
        if generation(user_id) != snap:                         # a forget ran (and finished) while this turn was looked up:
            logger.info("PENDING_CORRECTION skipped reason=forget_raced user=%s n=1", user_id)   # no await between check and put
            return False
        put(user_id, turn_id, keys=keys)
        return True
    except Exception as exc:  # noqa: BLE001 - a marker never costs the turn anything
        logger.debug("PENDING_CORRECTION skipped reason=error user=%s err=%s", user_id, type(exc).__name__)
        return False


async def texts_for_packet(user_id: str, *, now: Optional[float] = None) -> list:
    """The member's <= MAX_SERVED newest pending corrections in their own words, newest first, each <= TEXT_CAP chars. Flag off
    or no marker: [] with no read. Never raises."""
    try:
        live = pending(user_id, now=now)[:MAX_SERVED] if enabled() else []
        if live and await _opted_out(user_id):                  # opted out since they were recorded: never replayed
            forget_user(user_id, reason="opted_out")
            return []
        found = await _fetch_texts(user_id, [m.turn_id for m in live]) if live else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("PENDING_CORRECTION skipped reason=read_error user=%s err=%s", user_id, type(exc).__name__)
        return []
    texts = {}
    for m in live:
        text = " ".join((found.get(m.turn_id) or "").split())
        if not text:
            clear(user_id, m.turn_id, reason="turn_gone")
        elif not_replayable(text):                          # the same wall again on the text actually replayed
            clear(user_id, m.turn_id, reason=not_replayable(text))
        elif await _names_forgotten(user_id, text):        # forgotten since the marker was set: never served
            clear(user_id, m.turn_id, reason="forgotten")
        else:
            texts[m] = text[:TEXT_CAP]
    # LAST, after every await: a forget that landed meanwhile cleared the marker, and a marker re-added for the same turn since is
    # a different marker (its own said_at). Only the very markers that were read are served. (The fetch itself filters
    # off-the-record turns, so a turn the forget flagged is not returned either.)
    still = set(pending(user_id, now=now))
    out = [t for m, t in texts.items() if m in still]
    _log("served", "packet", user_id, len(out))
    return out
