"""ask_to_remember - the owner's EXPLICIT "remember that ..." (Samantha bar S11, ZOE_ASK_TO_REMEMBER).

Why this exists. Before it, an explicit memory ask had no owner. ``intent_router`` detected
``memory_remember`` ("remember that ...") and then never executed it (no handler, the turn fell
through to the brain); the deterministic teach lane (``expert_dispatch.store_fact``) only ran when
the semantic router happened to score the domain ``memory`` and ``ZOE_EXPERT_ALLOW_WRITES`` was on
(and never on voice, which defers the memory domain); everything else rested on the 4B brain
electing to call ``remember_fact`` - which it under-fires - and then answering "I'll remember
that" whether or not a row was written. "Don't forget ...", "keep in mind ..." and "for future
reference ..." were not recognised at all.

What it does, as ONE deterministic tier (``fast_tiers.resolve``, before the router and the brain, on
every channel that uses the core: chat, voice, LiveKit, Telegram):

* ``parse`` recognises the ask - "remember that ...", "don't forget that ...", "keep in mind ...",
  "note that ...", "for future reference, ...", with a polite lead ("can you", "please", "I want you
  to") - and the recall question "do you remember what I asked you to remember?". Pure, no I/O.
  What it does NOT claim: "remember to ..." / "don't forget to ..." (a reminder, not a fact), a
  question, a clause that is an instruction to the assistant ("remember that you must ...").
* ``remember`` stores the owner's words VERBATIM as a ``user_stated`` row (writer ``explicit_teach``:
  ``memory_authority`` TEACH_WRITERS, so the row carries the verbatim utterance as evidence, an
  explicit re-teach lifts a forget shield, and a later owner statement still wins), tagged
  ``ask_to_remember`` so the recall question and the retraction can find it. It goes through the
  shared ``expert_dispatch._ingest_or_supersede`` (idempotent: a repeat is "skip", a changed value of
  the same attribute supersedes) and through ``MemoryService.ingest`` - the one choke point - so the
  walls still apply: PII scrub, tombstones, the forgotten ledger, the identity wall (a name is the
  ACCOUNT's, never a row), the own-words wall (pasted text and another person's quoted speech are not
  the owner's), the speaker gate (an unverified panel voice is not the owner).
* The reply is ONE short sentence, produced only AFTER the write's outcome is known: "I'll remember"
  is said only when a row is stored (or an equivalent already is); every refusal says what did not
  happen. Never narration ("let me save that"), never a claim without a row.
* "note that ..." was a NOTE before this module existed (``note_create``): the memory row is written
  first, then the note is kept too, so the Notes page still has it.
* Retraction is the existing forget path: "forget that" (``memory_forget_last``) and "forget everything
  about X" (``memory_forget_entity``) find the row like any other; ``siblings_of`` makes "forget that"
  take the owner's verbatim row AND the near-identical row the post-turn capture derived from the same
  turn, so neither survives the other.

Voice path: the tier runs inside ``fast_tiers.resolve`` (the voice channel calls it). It adds no work
to a turn that is not an explicit ask (one anchored regex), and on an ask it replaces a brain round
trip (seconds) with one store write.

Everything is read per call; the flag is ``ZOE_ASK_TO_REMEMBER`` (default ON; ``0|false|no|off`` or
set-but-empty = the tier does nothing and every turn is exactly what it was).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_ASK_TO_REMEMBER"
#: the writer label: ``memory_authority`` class ``user_stated`` (TEACH_WRITERS), re-teach lifts a forget shield
WRITER = "explicit_teach"
#: the tag every row this module writes carries (the recall question and the retraction find rows by it)
TAG = "ask_to_remember"
#: the write is awaited this long before Zoe stops waiting (a voice turn must not hang on a loaded embedder); the
#: write itself is NOT cancelled - it is idempotent, so asking again is safe
STORE_BUDGET_S = 8.0
MAX_CLAUSE_CHARS = 400
MAX_UTTERANCE_CHARS = 600
#: "do you remember what I asked you to remember?" lists at most this many rows
RECALL_MAX_ITEMS = 3
RECALL_ITEM_CHARS = 160
#: the post-turn capture writes a near-identical row within this window of the owner's verbatim row
SIBLING_WINDOW_S = 180
SIBLING_MIN_OVERLAP = 0.5

_GUEST_IDS = frozenset({"", "guest", "anonymous", "voice-guest"})


def enabled() -> bool:
    """``ZOE_ASK_TO_REMEMBER`` - default ON, per-call read. ``0|false|no|off`` (or set-but-empty) switches the tier off."""
    from typed_env import env_bool

    return env_bool("ZOE_ASK_TO_REMEMBER", True)


# ── the ask (pure) ───────────────────────────────────────────────────────────────────────────

_LEAD = r"(?:(?:please|hey|hi|ok|okay|zoe|and|also|just|so|now|right|alright|well|oh|um|uh)[,\s]+)*"
_ASKER = (
    r"(?P<asker>(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?"
    r"|i\s+(?:want|need|would\s+like|['’]d\s+like)\s+you\s+to\s+"
    r"|you\s+(?:need|have|ought)\s+to\s+|you\s+should\s+"
    r"|make\s+sure\s+(?:you\s+|to\s+)?|be\s+sure\s+to\s+|try\s+to\s+|please\s+))?"
)
_CUE = (
    r"(?P<cue>remember|never\s+forget|(?:do\s*n['’]?t|do\s+not)\s+forget|keep\s+in\s+mind|bear\s+in\s+mind"
    r"|note|(?:make\s+)?(?:a\s+)?mental\s+note|for\s+future\s+reference|for\s+the\s+record)"
)
_ASK_RE = re.compile(
    rf"^\s*{_LEAD}{_ASKER}{_CUE}(?P<sep>\s*[,:]\s*|\s+)(?P<rest>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
#: what may follow the cue that makes it NOT a fact: a reminder ("remember to call mum"), a narrative or
#: question opener ("remember when ...", "remember how ..."), an address to the speaker ("remember me")
_NOT_A_FACT_OPENER_RE = re.compile(
    r"^(?:to|about|when|how|what|who|whom|whose|why|where|whether|if|which|me|us|the\s+time|time|doing)\b",
    re.IGNORECASE,
)
_QUESTION_OPENER_RE = re.compile(
    r"^(?:what|when|where|who|why|how|which|is|are|do|does|did|can|could|would|should|will)\b", re.IGNORECASE)
#: an instruction addressed to the ASSISTANT is not a fact about the owner
_INSTRUCTION_OPENER_RE = re.compile(
    r"^(?:you\b|your\b|always\b|never\b|please\b|don['’]?t\b|do\s+not\b|stop\b|start\b)", re.IGNORECASE)
_TRAILING_POLITE_RE = re.compile(r"[\s,]*(?:please|thanks|thank\s+you|ok|okay|alright)[\s.!]*$", re.IGNORECASE)
_THIS_RE = re.compile(r"^(?:this|these|the\s+following|the\s+next\s+thing|it)\s*[:,\-]\s*", re.IGNORECASE)
_THAT_RE = re.compile(r"^that\s*[:,]?\s+", re.IGNORECASE)
#: a bare "remember <clause>" (no "that") must read as a statement: a predicate or a first-person marker
_PREDICATE_RE = re.compile(
    r"\b(?:is|are|am|was|were|has|have|had|will|likes?|loves?|hates?|prefers?|lives?|works?|needs?|wants?|"
    r"eats?|drinks?|takes?|uses?|speaks?|plays?|goes|gets|does|doesn['’]?t|don['’]?t|can['’]?t|won['’]?t|"
    r"isn['’]?t|aren['’]?t|allergic|vegetarian|vegan|pescatarian|born)\b|['’](?:s|m|re|ve|d|ll)\b",
    re.IGNORECASE,
)
#: "my name is Jase" / "call me Jase" said as a fact to keep: the account owns who the user is
_FIRST_PERSON_NAME_RE = re.compile(
    r"^(?:my\s+(?:full\s+|first\s+|real\s+|legal\s+|preferred\s+)?name(?:['’]s|\s+is)\b"
    r"|(?:i\s+am|i['’]m)\s+called\b|i\s+go\s+by\b|(?:you\s+can\s+)?call\s+me\b|people\s+call\s+me\b)",
    re.IGNORECASE,
)
_RECALL_ASK_RE = re.compile(
    r"^\s*" + _LEAD + r"(?:"
    r"do\s+you\s+(?:still\s+)?(?:remember|recall|know)\s+(?:what|everything|anything|the\s+things?)\s+(?:that\s+)?i\s+"
    r"(?:asked|told|wanted|said)\s+(?:you\s+)?to\s+(?:remember|keep\s+in\s+mind)"
    r"|what\s+(?:things\s+)?(?:did|have|had)\s+i\s+(?:ask(?:ed)?|told|want(?:ed)?)\s+(?:you\s+)?to\s+"
    r"(?:remember|keep\s+in\s+mind)"
    r"|(?:tell|remind)\s+me\s+(?:what|everything|the\s+things?)\s+(?:that\s+)?i\s+(?:asked|told|wanted)\s+"
    r"(?:you\s+)?to\s+(?:remember|keep\s+in\s+mind)"
    r"|what\s+(?:things\s+)?(?:have\s+)?i\s+(?:asked|told)\s+you\s+to\s+remember"
    r")(?:\s+(?:earlier|before|today|yesterday|recently|so\s+far))?\s*[?.!]*\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Parsed:
    """An explicit memory ask. ``kind`` is ``remember`` (``clause`` = the owner's words after the cue, as said) or
    ``recall`` (the question "do you remember what I asked you to remember?"). ``cue`` is a stable label."""

    kind: str
    clause: str = ""
    cue: str = ""
    utterance: str = ""


def _cue_label(raw: str) -> str:
    c = re.sub(r"\s+", " ", raw.lower().replace("’", "'")).strip()
    if c.startswith("remember"):
        return "remember"
    if "forget" in c:
        return "dont_forget"
    if "mind" in c:
        return "keep_in_mind"
    if "note" in c:
        return "note"
    return "for_the_record"


def parse(text: str) -> Optional[Parsed]:
    """The explicit memory ask in ``text``, or None. Pure: no I/O, no model, no flag. Anchored at the start of the
    turn (after a polite lead), single-clause, and conservative - a turn that is not unmistakably the owner
    telling Zoe to keep a fact is none of this module's business and goes to the brain exactly as before."""
    t = (text or "").strip()
    if not t or len(t) > MAX_UTTERANCE_CHARS or t.count("\n") > 2:
        return None
    if _RECALL_ASK_RE.match(t):
        return Parsed("recall", utterance=t, cue="recall")
    m = _ASK_RE.match(t)
    if not m:
        return None
    cue = _cue_label(m.group("cue"))
    rest = (m.group("rest") or "").strip()
    asker = (m.group("asker") or "").strip().lower()
    if asker.startswith(("can", "could", "would", "will")) and rest.endswith("?"):
        rest = rest[:-1].rstrip()          # "can you remember that my sister is Marisol?" is a request
    rest = _TRAILING_POLITE_RE.sub("", rest).strip()
    rest = _THIS_RE.sub("", rest, count=1)
    explicit_that = False
    mt = _THAT_RE.match(rest)
    if mt:
        rest, explicit_that = rest[mt.end():], True
    if cue == "note" and not explicit_that:
        return None                        # "note down ..." / "note this" are the notes capability's phrasings
    if not explicit_that and _NOT_A_FACT_OPENER_RE.match(rest):
        return None                        # "remember to call mum" / "remember when ..." / "remember me"
    if explicit_that and cue == "remember" and re.match(r"^(?:time|day|night)\b", rest, re.IGNORECASE):
        return None                        # "remember that time we ..." is a story, not a fact
    clause = rest.strip()
    if len(clause) > 1 and clause[0] in "\"“'‘" and clause[-1] in "\"”'’":
        clause = clause[1:-1].strip()      # a whole-clause quote ("..."), not the closing mark of quoted speech
    clause = clause.rstrip(" .!").strip()
    if len(clause) > MAX_CLAUSE_CHARS or len(clause.split()) < 2 or "?" in clause:
        return None
    if _QUESTION_OPENER_RE.match(clause) or _INSTRUCTION_OPENER_RE.match(clause):
        return None
    if not explicit_that and not _PREDICATE_RE.search(clause) and not re.match(
            r"^(?:i|my|we|our)\b", clause, re.IGNORECASE):
        return None                        # "remember milk" is a list item, not a statement
    return Parsed("remember", clause=clause, cue=cue, utterance=t)


# ── the replies ──────────────────────────────────────────────────────────────────────────────

_CONFIRM = ("Got it — I'll remember that.", "Noted — I'll remember that.", "Okay, I'll remember that.")
ALREADY = "I've already got that one."
UNVERIFIED = "I couldn't tell who was speaking, so I haven't saved that — say it again and I will."
NAME_IS_ACCOUNTS = ("Your name comes from your account, so I haven't stored it as a memory — "
                    "say \"call me\" and the name you'd like if you want me to use a different one.")
PRIVATE = "That looks like a private number or password, so I haven't kept it."
NOT_THEIR_WORDS = ("That sounded like something pasted or quoted rather than you telling me, "
                   "so I haven't kept it.")
DROPPED = "I couldn't keep that one — nothing was saved."
NO_STORE = "I couldn't reach my memory just now, so I haven't saved that. Try again in a moment."
SLOW = "That's taking longer than usual, so I can't promise it's saved — ask me again in a moment."
NOTHING_ASKED = "You haven't asked me to remember anything specific yet."
UNVERIFIED_RECALL = "I couldn't tell who was speaking, so I won't read out what's been saved — ask me again in a moment."


def _confirm(clause: str) -> str:
    """One of three one-sentence confirmations, chosen by the clause so the same words always get the same
    reply (a retry is not a different answer)."""
    return _CONFIRM[int(hashlib.sha1(clause.lower().encode("utf-8")).hexdigest(), 16) % len(_CONFIRM)]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def turn_id(user_id: str, clause: str) -> str:
    """A stable idempotency id for (owner, words): the same ask twice is one row."""
    return "atr-" + hashlib.sha1(f"{user_id}|{_norm(clause)}".encode("utf-8")).hexdigest()[:16]


# ── the write ────────────────────────────────────────────────────────────────────────────────

async def _speculation_barrier() -> None:
    """A write on a SPECULATIVE voice turn waits for the daemon's verdict (dropped on cancel) - the same rule
    ``expert_dispatch.dispatch`` applies to every non-read kind. A no-op when nothing is bound."""
    try:
        import voice_speculation as _vs
    except Exception:  # noqa: BLE001 - in-tree; a missing module means no speculation exists
        return
    if _vs.bound_gate() is not None:
        await _vs.await_commit("ask_to_remember")


def _is_guest(user_id: Optional[str]) -> bool:
    u = (user_id or "").strip()
    return u in _GUEST_IDS or u.startswith("guest-")


async def remember(clause: str, user_id: str, *, utterance: str = "", session_id: str = "",
                   speaker_verified: Optional[bool] = None, cue: str = "remember", svc: Any = None) -> Optional[str]:
    """Store ``clause`` as the owner's own words and return the ONE sentence to say, or None when this is not a
    memory ask after all (an instruction to the assistant): the caller then proceeds as it always did.

    The reply is decided by the write's OUTCOME, never before it."""
    clause = (clause or "").strip()
    utterance = (utterance or clause).strip()
    if not clause or _is_guest(user_id):
        return None
    if _INSTRUCTION_OPENER_RE.match(clause):
        return None
    if speaker_verified is False:
        # #1887: an unverified panel voice never becomes the owner's own statement
        logger.info("ASK_TO_REMEMBER user=%s outcome=unverified_speaker", user_id)
        return UNVERIFIED
    # Only the owner's own voice (own_words): a pasted email or a quoted third person is not the owner telling Zoe
    try:
        import own_words

        own = own_words.analyze(clause)
        if own.changed:
            if len(own.text.split()) < 3:
                logger.info("ASK_TO_REMEMBER user=%s outcome=not_owner_words", user_id)
                return NOT_THEIR_WORDS
            clause = own.text.strip()
    except Exception as exc:  # noqa: BLE001 - a guard bug must not take the lane down
        logger.debug("ask_to_remember own_words check skipped: %s", type(exc).__name__)
    # The identity wall: who the user IS comes from the account, never from a recalled row. ``identity_facts``
    # walls the row shape ("User's name is X"); the owner SAYS it in the first person.
    try:
        from identity_facts import is_user_name_assertion

        if is_user_name_assertion(clause) or _FIRST_PERSON_NAME_RE.match(clause):
            logger.info("ASK_TO_REMEMBER user=%s outcome=identity_wall", user_id)
            return NAME_IS_ACCOUNTS
    except Exception as exc:  # noqa: BLE001
        logger.debug("ask_to_remember identity check skipped: %s", type(exc).__name__)
    try:
        from memory_service import get_memory_service, scrub_pii

        scrubbed, reject = scrub_pii(clause)
        if reject or scrubbed != clause:
            # a card / SSN is REJECTED by the store; a password / PIN / key is REDACTED in place - either way the
            # words the owner wants kept are not what would be stored, so nothing is, and Zoe says so
            logger.info("ASK_TO_REMEMBER user=%s outcome=pii_reject pattern=%s", user_id, reject or "redacted")
            return PRIVATE
        await _speculation_barrier()
        svc = svc or get_memory_service()
        from expert_dispatch import _ingest_or_supersede

        write = asyncio.ensure_future(_ingest_or_supersede(
            svc, clause, user_id=user_id, source=WRITER, session_id=session_id or None,
            user_turn_id=turn_id(user_id, clause), memory_type="fact", confidence=0.9,
            tags=["explicit", TAG], source_excerpt=utterance,
            **({} if speaker_verified is None else {"speaker_verified": speaker_verified}),
        ))
        try:
            outcome = await asyncio.wait_for(asyncio.shield(write), timeout=STORE_BUDGET_S)
        except asyncio.TimeoutError:
            logger.warning("ASK_TO_REMEMBER user=%s outcome=slow budget_s=%.1f", user_id, STORE_BUDGET_S)
            write.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)   # never "never retrieved"
            return SLOW
    except Exception as exc:  # noqa: BLE001
        logger.warning("ask_to_remember: store failed (%s)", type(exc).__name__)
        return NO_STORE
    logger.info("ASK_TO_REMEMBER user=%s cue=%s outcome=%s chars=%d", user_id, cue, outcome, len(clause))
    if outcome == "dropped":
        return DROPPED
    # An EXPLICIT teach beats a recent forget - but only once the store succeeded (a failed or refused
    # write must keep the shadow)
    try:
        from memory_tombstones import clear_matching

        clear_matching(user_id, clause)
    except Exception:  # noqa: BLE001
        pass
    if cue == "note":
        await _also_note(clause, user_id)
    if outcome == "skip" and not await _live_equivalent(svc, user_id, clause):
        # "I've already got that one" is said only over a LIVE approved row: a skip with nothing behind it (a
        # rejected / retired row, a stale idempotency key) saved nothing, and Zoe must not claim it did
        logger.info("ASK_TO_REMEMBER user=%s outcome=skip_without_live_row", user_id)
        return DROPPED
    return ALREADY if outcome == "skip" else _confirm(clause)


async def _live_equivalent(svc: Any, user_id: str, clause: str) -> bool:
    """Is there an approved row of this owner's that says what ``clause`` says? Fail-open (True) when the store
    cannot be listed - the skip already came from the store, so a listing blip must not turn it into a refusal."""
    try:
        from memory_service import get_memory_service

        rows = await (svc or get_memory_service()).list_by_status(user_id=user_id, status="approved", limit=1000)
        uid = (user_id or "").strip().lower()
        return any(
            str((r.metadata or {}).get("user_id") or (r.metadata or {}).get("wing") or "").strip().lower() == uid
            and _overlap(clause, r.text or "") >= SIBLING_MIN_OVERLAP
            for r in rows)
    except Exception as exc:  # noqa: BLE001
        logger.debug("ask_to_remember: live-row check skipped (%s)", type(exc).__name__)
        return True


async def _also_note(clause: str, user_id: str) -> None:
    """"note that ..." was a NOTE before this module existed (``intent_router`` note_create, with its own memory
    mirror): keep the note so the Notes page still has it. Best-effort - the memory row is already stored."""
    try:
        from intent_router import Intent, _execute_note_create_direct

        await _execute_note_create_direct(Intent("note_create", {"title": clause[:60], "content": clause}), user_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("ask_to_remember: note mirror skipped (%s)", type(exc).__name__)


# ── the recall question ──────────────────────────────────────────────────────────────────────

def is_ask_row(meta: Optional[dict]) -> bool:
    """Was this row written by an explicit ask (tag ``ask_to_remember``)?"""
    return TAG in str((meta or {}).get("tags") or "").split(",")


async def asked_rows(user_id: str, *, svc: Any = None, limit: int = RECALL_MAX_ITEMS) -> list:
    """The owner's approved ask-to-remember rows, newest first (owner-scoped)."""
    from memory_service import get_memory_service

    svc = svc or get_memory_service()
    rows = await svc.list_by_status(user_id=user_id, status="approved", limit=1000)
    uid = (user_id or "").strip().lower()
    mine = [r for r in rows if is_ask_row(r.metadata)
            and str(r.metadata.get("user_id") or r.metadata.get("wing") or "").strip().lower() == uid]
    return mine[:limit]


async def recall_reply(user_id: str, *, svc: Any = None) -> str:
    """"do you remember what I asked you to remember?" - the owner's own words back, newest first, at most three."""
    try:
        rows = await asked_rows(user_id, svc=svc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ask_to_remember: recall failed (%s)", type(exc).__name__)
        return "I couldn't reach my memory just now, so I can't say."
    if not rows:
        return NOTHING_ASKED
    items = [(r.text or "").strip()[:RECALL_ITEM_CHARS].rstrip(" .") for r in rows]
    if len(items) == 1:
        return f"You asked me to remember: {items[0]}."
    return "You asked me to remember: " + "; ".join(items[:-1]) + "; and " + items[-1] + "."


# ── the entry point ──────────────────────────────────────────────────────────────────────────

async def handle(text: str, user_id: str, session_id: str = "", *, speaker_verified: Optional[bool] = None,
                 svc: Any = None, allow_writes: bool = True) -> Optional[str]:
    """The reply for an explicit memory ask in ``text``, or None (flag off, a guest, not an ask). NEVER raises
    (``SpeculativeTurnCancelled`` is a ``CancelledError``, so a cancelled prefix still propagates and writes nothing)."""
    try:
        if not enabled() or _is_guest(user_id):
            return None
        p = parse(text)
        if p is None:
            return None
        if p.kind == "recall":
            if speaker_verified is False:
                # the owner's rows are read aloud only to the owner: a rejected voice verdict gets nothing
                logger.info("ASK_TO_REMEMBER user=%s outcome=unverified_recall", user_id)
                return UNVERIFIED_RECALL
            return await recall_reply(user_id, svc=svc)
        if not allow_writes:      # a dry replay (explicit allow_writes=False) reads, it never saves / replaces / retracts
            return None
        return await remember(p.clause, user_id, utterance=p.utterance, session_id=session_id,
                              speaker_verified=speaker_verified, cue=p.cue, svc=svc)
    except Exception as exc:  # noqa: BLE001 - a turn is never broken by this tier
        logger.warning("ask_to_remember failed (non-fatal): %s", type(exc).__name__)
        return None


# ── retraction ───────────────────────────────────────────────────────────────────────────────

_STOP = frozenset("a an the and or but of to in on at for with is are am was were be been i my me we our you your "
                  "it its that this these those as by from user users".split())


def _tokens(text: str) -> frozenset:
    return frozenset(t for t in re.findall(r"[a-z0-9]+", (text or "").lower().replace("'", "")) if t not in _STOP)


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _added_ts(meta: dict) -> float:
    try:
        return float(meta.get("added_ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def siblings_of(svc: Any, user_id: str, ref: Any) -> list:
    """The OTHER rows one explicit ask left behind, for "forget that": the owner's verbatim row and the near-identical
    row the post-turn capture derived from the same turn (``ref`` is whichever the forget found first). Same owner,
    within ``SIBLING_WINDOW_S``, the same turn (equal source excerpts, then overlap >= ``SIBLING_MIN_OVERLAP``)
    or, with no excerpt on one side, one row's content words wholly inside the other's; when ``ref`` is the derived row only an
    ask row counts. Never raises."""
    try:
        meta = ref.metadata or {}
        ts = _added_ts(meta)
        if ts <= 0:
            return []
        rows = await svc.list_by_status(user_id=user_id, status="approved", limit=1000)
        ref_is_ask = is_ask_row(meta)
        out = []
        for r in rows:
            if r.id == ref.id:
                continue
            m = r.metadata or {}
            if abs(_added_ts(m) - ts) > SIBLING_WINDOW_S:
                continue
            if not ref_is_ask and not is_ask_row(m):
                continue
            # the same TURN: when both rows carry the words they were written from, those words must agree (the
            # capture truncates, so one containing the other counts); a different turn is a different fact
            ex_a, ex_b = _norm(meta.get("source_excerpt")), _norm(m.get("source_excerpt"))
            same_turn = False
            if ex_a and ex_b:
                if ex_a not in ex_b and ex_b not in ex_a:
                    continue
                same_turn = True
            # no shared turn evidence: the shorter row must sit wholly inside the other (a twin, never a
            # neighbour that merely shares the predicate: "my son Rowan is allergic to peanuts" vs "my daughter
            # Wren is ...")
            if _overlap(ref.text or "", r.text or "") >= (SIBLING_MIN_OVERLAP if same_turn else 1.0):
                out.append(r)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("ask_to_remember: sibling lookup skipped (%s)", type(exc).__name__)
        return []
