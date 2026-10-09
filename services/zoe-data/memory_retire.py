"""Quote-backed retirement: a one-word change of state retires the right fact, with the owner's words attached.

Samantha bar S10 ("I play the cello" ... later "I gave up the cello"): the MemPalace deep dive
(docs/research/mempalace-deep-dive-2026-10-06.md section 4.4, 6.3) measured WHERE the gap is. Finding
the row is solved - the owner's own embedder puts the right current row in the top 3 on 29-30 of 30
changes. Zoe's deterministic rule (``memory_supersede.same_topic``) finds it on 7 of 30, and retrieval's
top-1 on a NON-change ("I saw a cello today") is the merely-mentioned row on 34 of 40, so "retire the
top-1" would be wrong most of the time. The part nothing deterministic can do is the JUDGEMENT: does
this sentence END one of these notes, and which one? So a model makes that one call and everything around
it is a wall that does not trust the model:

    prefilter  a change cue is present in the OWNER'S OWN words (memory_supersede's cue table plus the
               ending / replacing verbs it lacks), the sentence is not a question, a wish, a plan or a
               negated / "almost" change. No cue, no candidates, no model call.
    candidates the owner's top 3 CURRENT rows on the sentence (``MemoryService.search``), each one the
               owner's own, about the owner (or about someone the sentence names), a person-fact type,
               not a tombstone, not pasted, not above a user's own words in authority.
    judge      BOTH lanes, off the turn: the per-turn digest (``memory_digest.run_turn_digest``, which already runs
               after the reply) asks the local model the same question (``distill_turn``). CHAT also has the Flue brain's
               ``memory_retire`` tool (two calls: it is shown the three notes, then names one number or 0) as an optional
               extra - the 4B brain does not call a tool on a statement that asks it nothing, so the tool alone was
               never reached live (S10, 2026-10-09). The judge sees numbers; it never supplies text.
    wall       enforced HERE, whatever the judge said (``decide``): (a) the choice is one of the three
               offered rows; (b) the quote is the owner's verbatim sentence, cut from their own words, and is
               stored with the retirement; (c) the speaker is the verified owner - an authenticated chat
               turn, or a voice turn the server's own speaker gate confirmed (None = no verdict = no
               retirement) - and the sentence is not pasted text or a third person's quoted speech
               (``own_words``); (d) the row is the owner's own and about the owner, never another person's
               copy; a forgotten entity in the sentence retires nothing.
    effect     ``MemoryService.retire_with_quote``: status ``superseded`` (never deleted), ``invalid_at`` /
               ``expired_at`` (the two timelines: ``search(as_of=)`` before the change still returns the
               old fact), and the verbatim sentence on the row (``retire_quote``) so Zoe can show where
               she learned it. Forgetting an entity erases a row that cites a quote naming it.

``ZOE_QUOTE_RETIRE`` = ``shadow`` (DEFAULT: log the decision, change nothing) | ``enforce`` | ``off``. The
mode is read per call. Shadow still runs the prefilter, the candidates and (voice) the judge, so the
decision can be measured before anything is applied; the logs carry ids, ranks and counts, never text.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional

import memory_authority as _auth
import memory_supersede as _sup
import own_words

logger = logging.getLogger(__name__)

ENV = "ZOE_QUOTE_RETIRE"
ACTOR = "quote_retire"
N_CANDIDATES = 3          # the judge is shown this many of the owner's current rows
SEARCH_LIMIT = 12         # rows read to find them (ineligible ones are skipped, never offered)
QUOTE_MAX = 300           # the verbatim sentence kept with the retirement (also the scrub's cap)
LANES = ("chat", "voice")

#: the lanes' reasons a retirement is refused or not offered (labels only - they go in the log)
R_OFF = "off"
R_NO_CUE = "no_cue"
R_NOT_OWNER_WORDS = "not_owner_words"
R_SPEAKER = "speaker_unverified"
R_NOT_OWNER = "not_the_owner"
R_LANE = "unknown_lane"
R_VOICE_IN_TURN = "voice_lane_is_off_turn"
R_FORGOTTEN = "forgotten_entity"
R_NO_CANDIDATES = "no_candidates"
R_OUT_OF_RANGE = "pick_out_of_range"
R_NOT_OFFERED = "row_not_offered"
R_NOT_ELIGIBLE = "row_not_eligible"
R_NO_TURN = "no_turn_noted"
R_STORE = "store_refused"
R_JUDGE_FAILED = "judge_unavailable"


def mode() -> str:
    """``shadow`` (default) | ``enforce`` | ``off``. Per-call env read; a typo means ``shadow`` (applies nothing)."""
    from typed_env import env_str

    v = env_str("ZOE_QUOTE_RETIRE", "shadow").lower()      # absent / blank = shadow
    if v in ("0", "false", "no", "off"):
        return "off"
    if v in ("1", "true", "yes", "on", "enforce"):
        return "enforce"
    return "shadow"


# ── the prefilter: is there a state-change cue in the owner's own words? ───────────────────────

# memory_supersede.CUES carries the cues that appear in a stored FACT ("no longer", "gave up", "stopped"). The
# measured gap is wider: the owner SAYS "I sold the Corolla", "the goldfish died", "I got rid of the desk",
# "I cycle to work now" and the table catches 15 of 30 such sentences. These are the ending and replacing verbs
# it lacks. This is a RECALL filter - a cue only opens the door to the judge, never retires anything.
_EXTRA_CUE = re.compile(
    r"\b(?:sold|gave\s+(?:\w+\s+){0,2}away|gave\s+(?:it|that|this|them)\s+up|stepped\s+down|got\s+rid\s+of|threw\s+(?:\w+\s+)?(?:out|away)|ripped\s+out|scrapped|binned|"
    r"left|resigned|retired|finished|ended|closed|deleted|lapsed?|expired|died|passed\s+away|hung\s+up|"
    r"took\s+over|taken\s+over|(?:took|taken)\s+(?:me|us|him|her|them)\s+off|let\s+(?:\w+\s+){1,3}lapse|"
    r"swapped|replaced|upgraded|downgraded|moved|started|begun|began|bought|again|now|switched|changed|"
    r"stopped|quit|dropped|cancel(?:l)?ed|any\s?more)\b", re.I)
# A cue that does not END the thing: a question, a wish, a plan, a hypothetical.
_HYPOTHETICAL = re.compile(
    r"^\W*(?:(?:and|but|so|well|oh|um)\s+)?(?:if|what\s+if|should\s+i|shall\s+i|maybe|perhaps|supposing|someday|one\s+day|"
    r"i\s+(?:might|may|could|would|wish|want\s+to|plan\s+to|intend\s+to|hope\s+to)\b|"
    r"i(?:'m|\s+am)\s+(?:thinking|considering|planning|going|about)\b|i(?:'ll|\s+will)\b|"
    r"(?:do\s+you\s+think|would\s+you)\b)", re.I)
_QUESTION = re.compile(
    r"^\W*(?:did|do|does|have|has|had|should|would|could|can|will|what|why|how|when|where|who|which|is|are|was|were|shall)\b",
    re.I)
# What stands just BEFORE a cue and stops it ending anything: "I haven't given up", "almost sold", "thought about quitting".
_NEG_BEFORE = re.compile(r"(?:\bnot|n't|\bnever)\s+(?:\w+\s+){0,2}$", re.I)
_SOFT_BEFORE = re.compile(
    r"\b(?:almost|nearly|barely|thought\s+about|thinking\s+(?:of|about)|considered|tempted\s+to|wanted\s+to|"
    r"want\s+to|about\s+to|going\s+to|trying\s+to|try\s+to)\s+(?:\w+\s+){0,2}$", re.I)
# Somebody ELSE saying it, or the owner relaying hearsay or advice: not the owner stating that a note of theirs ended
# ("Quentin says: the goldfish died", "I heard they sold the Corolla", "the doctor said I should stop").
_ATTRIBUTED_BEFORE = re.compile(
    r"\b(?:says?|said|told\s+(?:me|us)|tells?\s+(?:me|us)|wrote|texted|emailed|claims?|reckons?|heard|thinks?|thought|suggested?|"
    r"recommended?|advised?|asked)\b", re.I)
_ENDS_ANYWAY = re.compile(r"\bno\s+longer\b|\bany\s?more\b", re.I)    # "not ... any more" IS a change
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


#: cues that are about CORRECTING a statement, not about something ending (the correction path owns those)
_NOT_ENDINGS = frozenset({"correction", "change of plan"})


def _cue_hits(sentence: str) -> "list[tuple[str, re.Match]]":
    hits: "list[tuple[str, re.Match]]" = []
    for cue in _sup.CUES:
        if cue.name in _NOT_ENDINGS:
            continue
        m = cue.pattern.search(sentence)
        if m and not (cue.name in _sup._ONE_OFF_CUES and _sup._ONE_OFF.search(sentence)):
            hits.append((cue.name, m))
    m = _EXTRA_CUE.search(sentence)
    if m:
        hits.append((re.sub(r"\s+", " ", m.group(0).lower()), m))
    return hits


def _is_change_sentence(sentence: str) -> Optional[str]:
    """The cue label when ``sentence`` is a plain statement that something changed, else None."""
    s = sentence.strip()
    if len(s) < 6 or s.endswith("?") or _QUESTION.match(s) or _HYPOTHETICAL.match(s):
        return None
    for label, m in _cue_hits(s):
        before = s[:m.start()]
        if _SOFT_BEFORE.search(before) or _ATTRIBUTED_BEFORE.search(before):
            continue
        if _NEG_BEFORE.search(before) and not _ENDS_ANYWAY.search(s):
            continue
        return label
    return None


def pick_quote(own_text: str) -> Optional[tuple[str, str]]:
    """``(verbatim sentence, cue label)`` from the OWNER'S OWN words, or None. The sentence is a substring of
    ``own_text`` (cut on sentence ends), capped at ``QUOTE_MAX``; the first plain change sentence wins."""
    for sentence in _SENT_SPLIT.split(own_text or ""):
        label = _is_change_sentence(sentence)
        if label:
            return sentence.strip()[:QUOTE_MAX], label
    return None


# ── candidates: the owner's own current rows on the sentence ─────────────────────────────────────

def _words(text: str) -> "set[str]":
    return set(re.findall(r"[a-z0-9']+", (text or "").lower()))


def subject_ok(quote: str, row_text: str) -> bool:
    """Is this row about the OWNER, or about someone the sentence NAMES? A row about "User's sister Priya" is never
    offered for "I gave up the cello" (the other person's copy); it is offered for "Priya gave up the cello" or for
    "my sister gave up the cello". A row that names nobody and no relation is the owner's own."""
    q = _words(quote)
    names = _sup.subject_names(row_text)
    rels = set(_sup._relations(row_text))
    if not names and not rels:
        return True
    qf = {_sup._fold_token(w) for w in q}
    return (any(tok in q for n in names for tok in n.split())
            or any(_sup._fold_token(r) in qf for r in rels))


def eligible(meta: dict, text: str, quote: str, user_id: str) -> bool:
    """May this row be OFFERED to the judge (and, again, retired)? The wall's rule (d): the owner's own current
    person-fact, about the owner or someone the sentence names; not a tombstone, not pasted text, not a row only an
    operator may change."""
    meta = meta or {}
    if (meta.get("user_id") or meta.get("wing")) != user_id:
        return False
    if not _sup._is_target(meta):
        return False
    if own_words.is_pasted_row(meta):
        return False
    if not subject_ok(quote, text):
        return False
    return _auth.may_override(_auth.USER_RANK, _auth.row_class(meta, text))


async def candidates(svc: Any, user_id: str, quote: str) -> list:
    """The owner's top ``N_CANDIDATES`` eligible current rows for ``quote``, best first. Never raises."""
    try:
        rows = await svc.search(quote, user_id=user_id, limit=SEARCH_LIMIT, timeout_s=3.0, history=False)
    except Exception as exc:  # noqa: BLE001 - a lookup failure offers nothing
        logger.debug("quote_retire: candidate search failed (%s)", type(exc).__name__)
        return []
    out = []
    for r in rows or []:
        if eligible(getattr(r, "metadata", None) or {}, getattr(r, "text", "") or "", quote, user_id):
            out.append(r)
        if len(out) >= N_CANDIDATES:
            break
    return out


# ── the wall's checks (module-level so a bench control can break each one alone) ─────────────────

def check_speaker(lane: str, user_id: str, speaker_verified: Optional[bool]) -> str:
    """"" when the speaker is the verified owner for this lane, else the refusal label. CHAT: an authenticated, named
    account (a guest has no store). VOICE: the server's own speaker gate said True; no verdict (None) is NOT a yes."""
    if lane not in LANES:
        return R_LANE
    try:
        from memory_service import is_guest_memory_user
        if is_guest_memory_user(user_id):
            return R_NOT_OWNER
    except Exception:  # noqa: BLE001 - fail closed
        return R_NOT_OWNER
    if lane == "voice" and speaker_verified is not True:
        return R_SPEAKER
    return ""


def check_offered(row_id: str, offered: "Iterable[Any]") -> bool:
    """Is ``row_id`` one of the rows the judge was shown THIS turn? (a)"""
    return bool(row_id) and row_id in {getattr(r, "id", "") for r in offered}


def check_row(meta: dict, text: str, quote: str, user_id: str) -> bool:
    """The chosen row is still the owner's own eligible row. (d)"""
    return eligible(meta, text, quote, user_id)


def own_part(text: str) -> "tuple[str, bool]":
    """``(the owner's own words, whether anything was removed)``. (c)"""
    own = own_words.analyze(text)
    return (own.text if own.changed else (text or "")).strip(), own.changed


async def _names_forgotten(user_id: str, text: str) -> bool:
    try:
        import memory_tombstones
        if memory_tombstones.matching_tombstone(user_id, text):
            return True
        import memory_forgotten
        return await memory_forgotten.matches(user_id, text)
    except Exception:  # noqa: BLE001 - the ledger being down must not retire anything it could have shielded
        return False


# ── decisions ───────────────────────────────────────────────────────────────────────────────────

@dataclass
class Decision:
    """What happened to one candidate state change. ``action``: ``off`` | ``nothing_to_offer`` | ``none`` (the judge
    said no row) | ``refused`` | ``shadow`` (would have retired ``row_id``) | ``retired``. Never carries the text."""
    action: str
    reason: str = ""
    lane: str = ""
    row_id: str = ""
    rank: int = 0
    n_candidates: int = 0
    cue: str = ""
    mode: str = ""
    quote: str = field(default="", repr=False)
    row_text: str = field(default="", repr=False)


@dataclass
class Prepared:
    """The deterministic half of a decision: either a ``Decision`` that already ends it (nothing to offer / refused),
    or the quote and the offered rows the judge now chooses among."""
    decision: Optional[Decision]
    quote: str = ""
    cue: str = ""
    candidates: list = field(default_factory=list)
    lane: str = ""
    turn_ref: str = ""


def _log(user_id: str, d: Decision) -> None:
    logger.info("QUOTE_RETIRE user=%s lane=%s mode=%s action=%s reason=%s cue=%s rank=%d cands=%d row=%s",
                user_id, d.lane, d.mode, d.action, d.reason or "-", d.cue or "-", d.rank, d.n_candidates,
                (d.row_id or "-")[:8])


def turn_ref_for(text: str) -> str:
    return "qr-" + hashlib.sha1((text or "").encode("utf-8", "ignore")).hexdigest()[:16]


async def prepare(svc: Any, user_id: str, text: str, *, lane: str, speaker_verified: Optional[bool] = None,
                  mode_override: Optional[str] = None) -> Prepared:
    """The deterministic half: mode, speaker, the owner's own words, the cue, the forgotten ledger, the candidates.
    No model call, no write. A ``Prepared`` whose ``decision`` is set is already over."""
    m = mode_override or mode()

    def end(action: str, reason: str = "", **kw: Any) -> Prepared:
        d = Decision(action, reason, lane=lane, mode=m, **kw)
        if m != "off":
            _log(user_id, d)
        return Prepared(d, lane=lane)

    if m == "off":
        return end("off", R_OFF)
    why = check_speaker(lane, user_id, speaker_verified)
    if why:
        return end("refused", why)
    owner_text, changed = own_part(text)
    picked = pick_quote(owner_text)
    if picked is None:
        if changed and pick_quote(text or "") is not None:       # the cue is only in pasted text / a third person's speech
            return end("refused", R_NOT_OWNER_WORDS)
        return end("nothing_to_offer", R_NO_CUE)
    quote, cue = picked
    if await _names_forgotten(user_id, quote):
        return end("refused", R_FORGOTTEN, cue=cue)
    cands = await candidates(svc, user_id, quote)
    if not cands:
        return end("nothing_to_offer", R_NO_CANDIDATES, cue=cue)
    return Prepared(None, quote=quote, cue=cue, candidates=cands, lane=lane, turn_ref=turn_ref_for(owner_text))


async def decide(svc: Any, user_id: str, prep: Prepared, *, pick: Optional[int] = None,
                 row_id: Optional[str] = None, mode_override: Optional[str] = None) -> Decision:
    """The judge's choice meets the wall. ``pick`` is a 1-based number into the offered rows (0 = none); ``row_id``
    names a row directly (a caller that is not a model). Whatever it is, the row must be one of the rows offered this
    turn (a), still the owner's eligible row (d), and only enforce mode writes - with the verbatim sentence (b)."""
    if prep.decision is not None:
        return prep.decision
    m = mode_override or mode()
    lane, cands, quote = prep.lane, prep.candidates, prep.quote

    def done(action: str, reason: str = "", row: Any = None, rank: int = 0) -> Decision:
        d = Decision(action, reason, lane=lane, row_id=getattr(row, "id", "") if row is not None else "", rank=rank,
                     n_candidates=len(cands), cue=prep.cue, mode=m, quote=quote,
                     row_text=getattr(row, "text", "") if row is not None else "")
        _log(user_id, d)
        return d

    if m == "off":
        return done("off", R_OFF)
    chosen = None
    rank = 0
    if row_id is not None and str(row_id).strip():
        rid = str(row_id).strip()
        if not check_offered(rid, cands):
            return done("refused", R_NOT_OFFERED)
        for i, r in enumerate(cands, 1):
            if r.id == rid:
                chosen, rank = r, i
        if chosen is None:                      # (only reachable with check_offered broken) resolve it from the store: the
            try:                                # eligibility check below is then the only wall left
                chosen = await svc.get(rid)
            except Exception:  # noqa: BLE001
                chosen = None
    elif pick is not None:
        try:
            if isinstance(pick, bool):
                raise ValueError("a bool is not a pick")
            n = int(pick)
        except (TypeError, ValueError):
            return done("refused", R_OUT_OF_RANGE)
        if n == 0:
            return done("none", "judge_said_none")
        if not 1 <= n <= len(cands):
            return done("refused", R_OUT_OF_RANGE)
        chosen, rank = cands[n - 1], n
    else:
        return done("none", "no_choice")
    if chosen is None:
        return done("refused", R_NOT_OFFERED)
    if not check_row(getattr(chosen, "metadata", None) or {}, getattr(chosen, "text", "") or "", quote, user_id):
        return done("refused", R_NOT_ELIGIBLE, chosen, rank)
    if m == "shadow":
        return done("shadow", "would_retire", chosen, rank)
    ok = await svc.retire_with_quote(user_id, chosen.id, quote=quote, turn_ref=prep.turn_ref, lane=lane, cue=prep.cue)
    if not ok:
        return done("refused", R_STORE, chosen, rank)
    return done("retired", "", chosen, rank)


# ── the judge (the voice lane's idle distiller; the chat lane's judge is the brain) ─────────────

def judge_prompt(quote: str, rows: list) -> str:
    notes = "\n".join(f"{i}. {getattr(r, 'text', '')}" for i, r in enumerate(rows, 1))
    return (
        "You keep notes about a person. They just said one sentence. Decide whether the sentence says that exactly "
        "ONE of the numbered notes is NO LONGER TRUE.\n"
        f"Sentence: \"{quote}\"\n"
        f"Notes:\n{notes}\n"
        "Answer 0 when the sentence only mentions the topic, is about something else, is a plan, a wish, a question, "
        "a joke, says it almost happened, or is about another person's life.\n"
        "Answer with JSON only: {\"pick\": N} where N is 1-" + str(len(rows)) + ", or 0 for none.")


def parse_pick(raw: str, n: int) -> int:
    """The judge's number, or 0 (none) for anything that is not exactly an integer in range: a muddled answer retires nothing."""
    s = (raw or "").strip()
    m = re.search(r"\{[^{}]*\}", s)
    val: Any = None
    if m:
        try:
            val = json.loads(m.group(0)).get("pick")
        except (ValueError, AttributeError):
            val = None
    elif re.fullmatch(r"\d", s):
        val = int(s)
    if isinstance(val, bool) or not isinstance(val, int):
        return 0
    return val if 0 <= val <= n else 0


async def ask_judge(quote: str, rows: list, *, timeout_s: float = 20.0, base_url: Optional[str] = None) -> Optional[int]:
    """One bounded call to the local model (the same endpoint and model the per-turn digest uses; ``base_url`` points the
    S10x live tier at the bake-off clone brain). ``None`` = the model could not be reached (not "no row": nothing is decided
    on a failure)."""
    import httpx
    from memory_digest import _GEMMA_URL

    payload = {
        "model": os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
        "messages": [{"role": "system", "content": "You are a careful judge. Return ONLY valid JSON."},
                     {"role": "user", "content": judge_prompt(quote, rows)}],
        "max_tokens": 24, "temperature": 0.0, "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(f"{(base_url or _GEMMA_URL).rstrip('/')}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        logger.debug("quote_retire: judge call failed (%s)", type(exc).__name__)
        return None
    return parse_pick(raw, len(rows))


async def distill_turn(user_id: str, user_message: str, *, speaker_verified: Optional[bool] = None,
                       judge: "Optional[Callable[[str, list], Awaitable[Optional[int]]]]" = None,
                       svc: Any = None, lane: str = "voice") -> Decision:
    """The off-the-turn judge, called by the per-turn digest after the reply was given. VOICE (``lane="voice"``): the
    only judge a spoken turn has. CHAT (``lane="chat"``): the DETERMINISTIC judge of every typed turn - the brain's
    ``memory_retire`` tool is optional on top of it, because a 4B model does not call a tool on a statement that asks it
    nothing (the S10 live failure 2026-10-09: the tool was disclosed and never called, so the path was never reached, not
    even in shadow). A chat turn the brain already decided is not judged twice. Every deterministic check runs first, so a
    turn with no change cue, an unverified speaker or no candidate row costs no model call. Never raises."""
    ln = lane if lane in LANES else "voice"
    try:
        if mode() == "off":
            return Decision("off", R_OFF, lane=ln, mode="off")
        if svc is None:
            from memory_service import get_memory_service
            svc = get_memory_service()
        if ln == "chat" and brain_decided(user_id, user_message):
            return Decision("none", "brain_already_decided", lane=ln, mode=mode())
        prep = await prepare(svc, user_id, user_message, lane=ln, speaker_verified=speaker_verified)
        if prep.decision is not None:
            return prep.decision
        pick = await (judge or ask_judge)(prep.quote, prep.candidates)
        if pick is None:
            d = Decision("refused", R_JUDGE_FAILED, lane=ln, n_candidates=len(prep.candidates), cue=prep.cue,
                         mode=mode())
            _log(user_id, d)
            return d
        return await decide(svc, user_id, prep, pick=pick)
    except Exception as exc:  # noqa: BLE001 - the digest must never fail on this
        logger.warning("quote_retire: distill failed user=%s: %s", user_id, type(exc).__name__)
        return Decision("refused", "error", lane=ln, mode=mode())


# ── the CHAT lane: the Flue brain's ``memory_retire`` tool (via intent-dispatch) ────────────────

_TURN_TTL_S = 300.0
_TURN_MAX = 256
#: user id -> (the turn's message, voice lane?, noted at)
_turns: "dict[str, tuple[str, bool, float]]" = {}
#: user id -> (turn ref, the offered rows, offered at): what the brain was SHOWN, so what it names is checked against it
_offers: "dict[str, tuple[str, list, float]]" = {}
#: user id -> (turn ref, decided at): the turns the brain's tool already put to the wall, so the digest does not judge them twice
_decided: "dict[str, tuple[str, float]]" = {}


def brain_decided(user_id: str, message: str) -> bool:
    """Did the brain's ``memory_retire`` tool already make a CHOICE (a pick, even 0) on this owner sentence in the last few minutes?"""
    got = _decided.get((user_id or "").strip())
    if got is None or time.monotonic() - got[1] > _TURN_TTL_S:
        return False
    own, _ = own_part(message)
    return got[0] == turn_ref_for(own)


def note_turn(user_id: str, message: str, *, voice: bool = False) -> None:
    """Record this user's current brain turn (called by ``zoe_flue_client`` as the turn starts). The ``memory_retire`` tool
    sends only a number: the sentence it is about is the one HERE, put there by trusted server code and never by the
    model. No-op when off; never raises (a turn must not break on this)."""
    try:
        uid = (user_id or "").strip()
        if not uid or mode() == "off":
            return
        _turns.pop(uid, None)
        _offers.pop(uid, None)
        _turns[uid] = (str(message or ""), bool(voice), time.monotonic())
        while len(_turns) > _TURN_MAX:
            _turns.pop(next(iter(_turns)))
        while len(_offers) > _TURN_MAX:
            _offers.pop(next(iter(_offers)))
    except Exception:  # noqa: BLE001
        return


def _noted(user_id: str) -> "Optional[tuple[str, bool]]":
    got = _turns.get((user_id or "").strip())
    if got is None or time.monotonic() - got[2] > _TURN_TTL_S:
        return None
    return got[0], got[1]


def reset_state() -> None:
    _turns.clear()
    _offers.clear()
    _decided.clear()


def _listing(rows: list) -> str:
    notes = " ".join(f"{i}) {getattr(r, 'text', '')}" for i, r in enumerate(rows, 1))
    return ("Which ONE of these saved notes does what they just said END (make no longer true)? "
            f"{notes} -- Call memory_retire again with pick=<that number>, or pick=0 if their sentence does not end any "
            "of them (a mention, a plan, a question, something about someone else). Do not tell them about this step.")


async def handle(user_id: str, slots: dict, *, svc: Any = None) -> str:
    """The ``memory_retire`` intent. No ``pick`` (or ``row_id``): show the brain the offered notes. With one: the wall
    decides. The reply is for the BRAIN to phrase from; it never claims a change in shadow mode."""
    uid = (user_id or "").strip()
    m = mode()
    if m == "off":
        return "Noted."
    if svc is None:
        from memory_service import get_memory_service
        svc = get_memory_service()
    got = _noted(uid)
    if got is None:
        _log(uid, Decision("refused", R_NO_TURN, lane="chat", mode=m))
        return "Noted."
    text, voice = got
    if voice:
        # the voice lane's judge is the digest, off the turn; a brain tool call made during a spoken turn changes nothing
        _log(uid, Decision("refused", R_VOICE_IN_TURN, lane="voice", mode=m))
        return "Noted."
    slots = slots or {}
    has_choice = slots.get("pick") is not None or bool(str(slots.get("row_id") or "").strip())
    offered = _offers.get(uid)
    if not has_choice:
        prep = await prepare(svc, uid, text, lane="chat")
        if prep.decision is not None:
            return "Nothing saved needs changing for that."
        _offers[uid] = (prep.turn_ref, prep.candidates, time.monotonic())
        return _listing(prep.candidates)
    # a choice: the rows it is checked against are the ones this turn's listing showed; a brain that skipped the listing
    # gets them recomputed (so a row id it invents is still not among them)
    prep = await prepare(svc, uid, text, lane="chat")
    if prep.decision is not None:
        return "Noted."
    if offered and offered[0] == prep.turn_ref and time.monotonic() - offered[2] <= _TURN_TTL_S:
        prep.candidates = list(offered[1])        # the notes the brain was SHOWN: its number means those
    d = await decide(svc, uid, prep, pick=slots.get("pick"), row_id=slots.get("row_id"))
    _offers.pop(uid, None)
    _decided[uid] = (prep.turn_ref, time.monotonic())
    while len(_decided) > _TURN_MAX:
        _decided.pop(next(iter(_decided)))
    if d.action == "retired":
        return ("Done: that note is now marked as no longer true, and I kept their own sentence as the reason. "
                "Acknowledge the change briefly in your own words.")
    if d.action == "shadow":
        return "Noted. Acknowledge the change briefly in your own words; do not say you changed or removed anything."
    return "Noted."
