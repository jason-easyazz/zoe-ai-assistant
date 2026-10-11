"""Ask when ambiguous, do when clear (Samantha person bench P7, ZOE_ASK_WHEN_AMBIGUOUS).

The failure (person-likeness bench, 2026-10-09, live baseline): "Tell me about Marisol." with two Marisols in the
household's contacts got exactly one question that names the choice in only 4 of 20 asks; the other 16 guessed
("Marisol Okafor is listed as a colleague, but I don't have any information on where she lives"), listed both
without asking, or answered about neither. The clear turns ("Tell me about <the only Percival>") were answered with no
question 20 of 20 and must stay that way: a question is a cost, and it is worth it only when the answer changes
what Zoe will do (docs/research/person-likeness-2026-10-09.md WRM6).

What this does, as a deterministic tier ahead of the router, the keyword intents and the brain
(``fast_tiers.resolve`` -> ``_conversation_quality_tier``; chat, voice, LiveKit, Telegram), so an ambiguous
target is asked about BEFORE any tool call or write:

* ``ambiguity`` - a REQUEST turn (a question, or a command: call / text / remind / tell me about / remember that)
  that names a person by a first name two or more of THIS user's contacts share, with nothing that settles which
  ("Marisol Okafor", "my sister Marisol", "Marisol from work"), gets ONE question that names the choice:
  "Which Marisol do you mean: Marisol Okafor, your colleague, or Marisol Vance, your sister?" A statement
  ("Marisol is coming over") is never asked about. A clear turn - one match, a full name, a settling role word -
  is never asked.
* ``remember`` / ``resolve_followup`` - the question is remembered for the session; the owner's short answer
  ("the sister", "Okafor", "the first one") is rewritten, for the brain, into the original request with the full
  name ("Tell me about Marisol Vance."). One question per request: an answer that settles nothing is dropped and
  the turn goes on as it did before (no loop).

Which light / which list item / which event are asked by their own resolvers already
(``smart_home_service`` "Which one?", the lists "Which one should I edit?"); this module is the people half and
the registry is open (``_RESOLVERS``). It reads this user's ``people`` rows only, through a per-user cache of the
first names that repeat, valid while a one-row fingerprint of the contacts is unchanged (60 s at most): a request
turn costs one aggregate query, and a contact written a moment ago is never missed.

``ZOE_ASK_WHEN_AMBIGUOUS`` = ``shadow`` (default: detect and log ``ASK_WHEN_AMBIGUOUS mode=shadow``, ask nothing)
| ``enforce`` | ``off`` (reads nothing). Counts and labels are logged, never names. Never raises.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Optional

from typed_env import env_str

logger = logging.getLogger(__name__)

ENV = "ZOE_ASK_WHEN_AMBIGUOUS"
ROSTER_TTL_S = 60.0
PENDING_TTL_S = 120.0
MAX_CANDIDATES = 4
MAX_ROSTER = 500
_GUEST_IDS = ("", "guest", "anonymous", "voice-guest", "voice-daemon")


def mode() -> str:
    """``shadow`` (default, unset/unknown) | ``enforce`` | ``off``. Per-call env read."""
    raw = env_str("ZOE_ASK_WHEN_AMBIGUOUS").lower()
    if raw in ("0", "false", "no", "off", "disabled"):
        return "off"
    if raw in ("1", "true", "yes", "on", "enforce"):
        return "enforce"
    return "shadow"


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


@dataclass(frozen=True)
class Candidate:
    person_id: str
    name: str
    relationship: str = ""

    @property
    def first(self) -> str:
        return _fold(self.name.split()[0]) if self.name.split() else ""

    @property
    def last(self) -> str:
        parts = self.name.split()
        return _fold(parts[-1]) if len(parts) > 1 else ""


@dataclass(frozen=True)
class Ambiguity:
    kind: str                      # "person"
    handle: str                    # the word the owner used ("Marisol")
    candidates: tuple              # of Candidate
    question: str


# -- is this a turn Zoe will ACT or ANSWER on? (a statement is never asked about) ------------------

_ASK_VERBS = (
    "call", "ring", "phone", "text", "message", "email", "dm", "send", "remind", "add", "invite", "show", "open",
    "find", "look", "tell", "give", "get", "pull", "set", "book", "schedule", "move", "cancel", "delete", "remove",
    "share", "forward", "ask", "check", "remember", "note", "save", "store", "update", "change", "edit", "mention",
    "introduce", "play", "put", "list", "search", "who", "what", "whats", "where", "when", "how", "which", "whose",
    "why", "is", "are", "was", "were", "does", "do", "did", "has", "have", "had", "can", "could", "will", "would",
    "should", "shall", "isn't", "wasn't", "doesn't", "didn't",
)
_LEAD_RX = re.compile(
    r"^\W*(?:(?:hey|hi|ok|okay|so|um+|uh+|please|zoe|and|also|right|now|well|actually|yeah)[\s,.!]+)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|i\s+(?:want|need|d\s+like|would\s+like)\s+(?:you\s+)?to\s+|"
    r"i['’]d\s+like\s+(?:you\s+)?to\s+|please\s+)?", re.IGNORECASE)
_ASK_RX = re.compile(r"^(?:" + "|".join(re.escape(v) for v in _ASK_VERBS) + r")\b", re.IGNORECASE)


def is_request(text: str) -> bool:
    """A question or a command - the turns where the answer changes what Zoe does. Pure."""
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith("?"):
        return True
    body = t[_LEAD_RX.match(t).end():] if _LEAD_RX.match(t) else t
    return bool(_ASK_RX.match(body.strip()))


# names that are also everyday words: lower-case in a sentence they are the word, not the person
_WORDY_NAMES = frozenset({
    "will", "mark", "bill", "grace", "rose", "hope", "joy", "art", "pat", "sue", "dawn", "june", "april", "may", "summer",
    "faith", "ray", "rob", "frank", "jack", "bob", "page", "bay", "chase", "dean", "drew", "gene", "guy", "ivy", "jay",
    "jo", "lane", "miles", "pearl", "penny", "ruby", "sandy", "sky", "stone", "wade", "wren", "bell", "case", "clay",
    "dale", "eve", "fay", "hunter", "iris", "lily", "mac", "max", "noel", "olive", "pierce", "rich", "sage", "star",
})


def _norm_words(text: str) -> list:
    """Lower-case ASCII words, possessives stripped ("Okafor's" -> "okafor"). Pure."""
    return [re.sub(r"'s?$", "", w) or w for w in re.findall(r"[a-z0-9']+", _fold(text))]


# -- the roster ---------------------------------------------------------------------------------

_ROSTER: dict = {}      # user_id -> (loaded_at, fingerprint, {first_name: [Candidate, ...]}) - only first names that REPEAT


async def _load_people(user_id: str) -> list:
    """This user's live ``people`` rows as ``Candidate``s, bounded. Raises on a read failure (the caller decides)."""
    from db_pool import get_db_ctx

    async with get_db_ctx() as db:
        async with db.execute(
            "SELECT id, name, relationship FROM people WHERE user_id = ? AND deleted = 0 ORDER BY name LIMIT ?",
            (user_id, MAX_ROSTER),
        ) as cur:
            rows = await cur.fetchall()
    return [Candidate(str(r[0]), str(r[1]).strip(), str(r[2] or "").strip()) for r in rows if r[1] and str(r[1]).strip()]


async def _people_fingerprint(user_id: str):
    """A cheap fingerprint of this user's live ``people`` rows (count, newest ``updated_at``, total name length): the
    cached roster is valid only while it is unchanged. Raises on a read failure (the caller serves the cache)."""
    from db_pool import get_db_ctx

    async with get_db_ctx() as db:
        async with db.execute(
            "SELECT COUNT(*), COALESCE(MAX(updated_at), ''), COALESCE(SUM(LENGTH(name)), 0) "
            "FROM people WHERE user_id = ? AND deleted = 0",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    return tuple(str(v) for v in row) if row else ("0", "", "0")


def repeated_first_names(people: list) -> dict:
    """``{first name: [Candidate, ...]}`` for the first names two or more people share. Pure."""
    by: dict = {}
    for c in people:
        if c.first and len(c.first) >= 2:
            by.setdefault(c.first, []).append(c)
    return {k: v for k, v in by.items() if len(v) >= 2}


async def roster(user_id: str) -> dict:
    """The repeating first names for ``user_id``. {} when none, or when the roster could not be read.

    The cache is keyed on the contacts themselves, not only on the clock: a 60 s TTL alone let a roster read in the
    middle of "Save a contact for my colleague Marisol Okafor" / "...my sister Marisol Vance" - one Marisol, so
    nobody ambiguous - answer "no ambiguity" for the next minute while the second Marisol was already on the list
    (live person bench, 2026-10-10: the first 12 of 20 ambiguous asks went unasked, then 8 in a row were asked at the
    second the cache expired). A write to the contacts changes the fingerprint, so the very next request turn
    re-reads. A failed read is never cached: the next turn tries again."""
    now = time.monotonic()
    hit = _ROSTER.get(user_id)
    try:
        fp = await _people_fingerprint(user_id)
    except Exception as exc:  # noqa: BLE001 - no fingerprint: serve a still-fresh cache, else read for real
        logger.debug("ask_when_ambiguous: fingerprint read failed (%s)", type(exc).__name__)
        fp = None
    if hit and now - hit[0] < ROSTER_TTL_S and (fp is None or fp == hit[1]):
        return hit[2]
    try:
        rep = repeated_first_names(await _load_people(user_id))
    except Exception as exc:  # noqa: BLE001 - no roster is "nobody is ambiguous", never a failed turn
        logger.debug("ask_when_ambiguous: roster read failed (%s)", type(exc).__name__)
        _ROSTER.pop(user_id, None)
        return {}
    if len(_ROSTER) > 256:
        _ROSTER.clear()
    _ROSTER[user_id] = (now, fp, rep)
    return rep


def forget_roster(user_id: str = "") -> None:
    """Drop the cached roster (one user, or all) - tests, and a contact write that wants the next turn fresh."""
    if user_id:
        _ROSTER.pop(user_id, None)
    else:
        _ROSTER.clear()


# -- detection (pure) ---------------------------------------------------------------------------

def _rel_words(rel: str) -> list:
    return [w for w in _norm_words(rel) if w not in {"the", "a", "an", "of", "my", "your", "his", "her", "their", "to"}]


def _settles(text: str, handle_rx: "re.Match[str]", group: list) -> bool:
    """Does the message already say WHICH one? A full name or surname of one of them, or a role word that
    belongs to exactly one of them ("my sister Marisol", "Marisol from work" for a colleague)."""
    words = _norm_words(text)
    lasts = {c.last for c in group if c.last}
    if lasts and any(w in lasts for w in words):
        return True
    owners = []
    for c in group:
        rw = _rel_words(c.relationship)
        if rw and any(w in words for w in rw):
            owners.append(c)
    if len(owners) == 1:
        return True
    if "work" in words and sum(1 for c in group if any(w in _rel_words(c.relationship) for w in
                                                       ("colleague", "coworker", "boss", "manager", "work"))) == 1:
        return True
    return False


def _phrase(c: Candidate) -> str:
    rw = _rel_words(c.relationship)
    if len(rw) == 1 and re.fullmatch(r"[a-z-]{2,20}", rw[0]):
        return f"{c.name}, your {rw[0]}"
    return f"{c.name}, {c.relationship}" if c.relationship else c.name


def build_question(handle: str, group: list) -> str:
    """ONE question that names the choice. Pure."""
    shown = group[:MAX_CANDIDATES]
    parts = [_phrase(c) for c in shown]
    if len(parts) == 2:
        listing = f"{parts[0]}, or {parts[1]}"
    else:
        listing = ", ".join(parts[:-1]) + f", or {parts[-1]}"
    return f"Which {handle} do you mean: {listing}?"


def ambiguity(text: str, rep: dict) -> Optional[Ambiguity]:
    """The ambiguity in ``text`` given the repeating first names ``rep`` (``roster``), or None. Pure."""
    if not rep or not is_request(text):
        return None
    t = text or ""
    lowered = t == t.lower()                                # an all-lower-case (dictated) message
    for first, group in rep.items():
        for m in re.finditer(rf"(?<![\w'])({re.escape(first)})(?:['’]s)?(?![\w])", _fold(t), re.IGNORECASE):
            raw = t[m.start(1):m.end(1)] if len(t) == len(_fold(t)) else m.group(1)
            if first in _WORDY_NAMES and not raw[:1].isupper():
                continue
            if lowered and first in _WORDY_NAMES:
                continue
            if _settles(t, m, group):
                return None
            handle = raw[:1].upper() + raw[1:].lower() if raw else first.title()
            return Ambiguity("person", handle, tuple(group[:MAX_CANDIDATES]), build_question(handle, group))
    return None


# -- the pending question and the owner's short answer --------------------------------------------

@dataclass
class _Pending:
    original: str
    handle: str
    candidates: tuple
    at: float


_PENDING: dict = {}        # (user_id, session_id) -> _Pending


def _key(user_id: str, session_id: str) -> tuple:
    return ((user_id or "").strip(), (session_id or "").strip())


def remember(user_id: str, session_id: str, original: str, amb: Ambiguity) -> None:
    if len(_PENDING) > 256:
        _PENDING.clear()
    _PENDING[_key(user_id, session_id)] = _Pending(original, amb.handle, amb.candidates, time.monotonic())


def has_pending(user_id: str, session_id: str) -> bool:
    p = _PENDING.get(_key(user_id, session_id))
    return bool(p and time.monotonic() - p.at < PENDING_TTL_S)


_ORDINALS = (("first", 0), ("1st", 0), ("second", 1), ("2nd", 1), ("third", 2), ("3rd", 2), ("fourth", 3),
             ("last", -1))


def pick(answer: str, candidates: tuple) -> Optional[Candidate]:
    """The candidate a short answer names - by role ("the sister"), surname/full name, or ordinal ("the first
    one") - or None when it settles nothing or settles two. Pure."""
    words = _norm_words(answer)
    if not words or len(words) > 8:
        return None
    named = [c for c in candidates if (c.last and c.last in words)]
    if len(named) == 1:
        return named[0]
    role = [c for c in candidates if any(w in words for w in _rel_words(c.relationship))]
    if len(role) == 1:
        return role[0]
    if "work" in words:
        work = [c for c in candidates if any(w in _rel_words(c.relationship)
                                             for w in ("colleague", "coworker", "boss", "manager", "work"))]
        if len(work) == 1:
            return work[0]
    for word, idx in _ORDINALS:
        if word in words:
            if idx == -1:
                return candidates[-1]
            if idx < len(candidates):
                return candidates[idx]
    return None


_REFUSAL_RX = re.compile(
    r"\b(?:not|no|nope|never|neither|nor|none|isn['’]?t|wasn['’]?t|aren['’]?t|don['’]?t|doesn['’]?t|didn['’]?t|"
    r"other\s+than|except|besides|instead\s+of|anyone\s+but)\b", re.IGNORECASE)


def resolve_followup(message: str, user_id: str, session_id: str) -> Optional[str]:
    """The original request with the full name in place of the bare first name, when ``message`` answers the
    question Zoe asked; else None. The pending question is consumed either way - one question per request, no
    loop. Pure but for the session's pending state."""
    p = _PENDING.pop(_key(user_id, session_id), None)
    if p is None or time.monotonic() - p.at >= PENDING_TTL_S:
        return None
    if _REFUSAL_RX.search(message or ""):
        return None                                # "not my sister" refuses a person; it never chooses one
    chosen = pick(message, p.candidates)
    if chosen is None:
        return None
    pat = re.compile(rf"(?<![\w'])({re.escape(p.handle)})(?=['’]s\b|[^\w]|$)", re.IGNORECASE)
    if pat.search(p.original):
        return pat.sub(chosen.name, p.original, count=1)
    return f"{p.original} (I mean {chosen.name}.)"


# -- the tier --------------------------------------------------------------------------------------

async def handle(message: str, user_id: str, session_id: str) -> str:
    """The ONE clarifying question for the fast tier, or '' (the turn goes on untouched). ``shadow`` logs what it
    would ask and returns ''; ``off`` reads nothing. A request that follows an unanswered question of ours is
    not asked again. NEVER raises."""
    m = mode()
    if m == "off" or (user_id or "").strip().lower() in _GUEST_IDS:
        return ""
    try:
        text = message or ""
        if has_pending(user_id, session_id):
            return ""                                  # we asked once; the answer is resolve_followup's, not a new ask
        if len(text) > 400 or not is_request(text):
            return ""
        amb = ambiguity(text, await roster(user_id))
        if amb is None:
            return ""
        logger.info("ASK_WHEN_AMBIGUOUS mode=%s kind=%s candidates=%d", m, amb.kind, len(amb.candidates))
        if m != "enforce":
            return ""
        remember(user_id, session_id, text, amb)
        return amb.question
    except Exception as exc:  # noqa: BLE001 - the tier must never break a turn
        logger.warning("ask_when_ambiguous.handle failed (non-fatal): %s", type(exc).__name__)
        return ""
