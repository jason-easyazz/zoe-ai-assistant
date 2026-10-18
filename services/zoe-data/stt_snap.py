"""Misheard-command recovery ("STT snap"): one word of a spoken command heard as a sound-alike.

Live class (2026-10): a device command with ONE word misheard - "two on the light" for "turn on the light", "turn of
the lights", "set a time for 5 minutes" - matches no deterministic matcher, so it falls through every fast path to the
slow brain. This module asks one narrow question about such a transcript: is there exactly ONE command a single
sound-alike repair of its first three words would make, and is that command one of Zoe's own?

How (pure, stdlib, no model, no I/O beyond reading one committed vocabulary file once):

1. It runs only on a transcript NO deterministic matcher claims (``skybridge_service.classify_skybridge_intent`` with
   the panel's context, then ``intent_router.detect_intent``), that is not a question or statement ABOUT a write
   (``command_shapes.asks_or_mentions``). The voice lane calls it after the conversation tiers ran and missed, and
   skips it when the domain experts could act on the turn (``expert_may_claim``) - so a turn any fast tier owns is
   never touched.
2. Candidates: each of the first three words of the command (``command_shapes.command_core``: wake / politeness words
   off) may be swapped for ONE word of the vocabulary seen at that position in the commands Zoe's matchers handle
   correctly (``lexicons_data/stt_snap_vocab.json``, DERIVED from the HA/OVOS phrasing corpus + the command verbs of
   ``command_shapes`` - regenerated and drift-pinned by ``tests/test_stt_snap.py``). A swap must sound alike: the same
   onset and a cost <= ``MAX_COST`` (character similarity, or a consonant skeleton that drops a non-rhotic "r", a
   silent "w" / "h" / final "e"; a final "n" heard as the start of the next vowel-initial word: "turn on" -> "two on").
   Polarity words (on/off/up/down/...) and negations are never swapped, a number word only as the FIRST word: a changed
   one is a different command, not a mishearing. The swapped-in word must sit next to a neighbour it is seen with in a
   handled command, and the words before it must already open one (only ONE word is misheard).
3. Every candidate is re-run through the same two matchers. It snaps only when the claims name exactly ONE command
   (grouped by intent + its slots), in a domain the snap may serve, the runner-up command (if any) costs at least
   ``MARGIN`` more, and no repair LIKELIER by ``MARGIN`` makes a command Zoe cannot serve ("pours" is "pause", which
   Zoe cannot do for a TV - never "play").
4. Safety (``Snap.safety``): ``read`` (time, weather, a list / timer / calendar read) and ``act`` (a reversible
   low-risk write: one light on/off, music play/pause/resume, one item added to a list, one new timer) run as the
   snapped command, whose spoken receipt says it back ("Turned the bedroom light on."); every other write - cancel /
   delete / remove / complete, a timer change, scenes, brightness, volume, calendar, "all"/"everything", plural device
   groups, locks / doors / alarm / thermostat - gets ``confirm``: one "Did you mean ...?" question, never a silent act.

Flag ``ZOE_STT_SNAP`` = off | shadow (default) | on, read per call. ``shadow`` decides and logs (and feeds the struggle
detector) but changes nothing; ``on`` acts. Contract + corpus numbers: docs/knowledge/voice-pipeline.md -> *STT snap*.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

import command_shapes

logger = logging.getLogger(__name__)

VOCAB_FILE = Path(__file__).with_name("lexicons_data") / "stt_snap_vocab.json"
#: how many leading words of the command may be repaired
POSITIONS = 3
#: the most a single swap may cost (0 = the same sound, 1 = nothing alike)
MAX_COST = 0.45
#: a second, DIFFERENT command must cost at least this much more than the first, or the snap abstains
MARGIN = 0.15
#: added to a swap of a word that already opens Zoe's commands at that place (it is likely what was said)
IN_VOCAB_PENALTY = 0.3
#: what reading a target's final "n" / "m" as the start of a following vowel-initial word costs ("two on" -> "turn on")
LIAISON_COST = 0.2
#: two words whose consonant skeletons are further apart than this never sound alike, however they are spelled
SKELETON_MAX = 0.6
#: candidates re-run through the matchers, cheapest first (bounds the work on a long miss)
MAX_CANDIDATES = 40
MIN_TOKENS, MAX_TOKENS = 2, 14

MODES = ("off", "shadow", "on")

#: a swap never touches these: a changed polarity / negation / number is a different command, not a mishearing
_NEVER_REPAIR = frozenset("""
on off up down open close start stop lock unlock more less next previous louder quieter higher lower brighter dimmer
in out not no never don't dont do does did all every both none any each everything nothing
""".split())
#: number words are slots ("set a timer for two minutes") - repaired only as the FIRST word, where a command opens with a
#: verb and "two" / "to" is the classic mishearing of "turn" before a vowel
_NUMBER_WORDS = frozenset(command_shapes.WORD_NUMBERS)

_TOKEN_RE = re.compile(r"[a-z0-9']+")

# ── claims ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Claim:
    """A deterministic matcher's claim, in the phrasing corpus' tag form (``sky:<domain>/<action>`` /
    ``ir:<intent>[/<sub>]``) plus the slots that make two claims the same command."""

    tag: str
    key: str
    seconds: Optional[int] = None
    item: str = ""
    target: str = ""


def claim_of(text: str, context: Optional[dict] = None) -> Optional[Claim]:
    """The voice cascade's claim for ``text``: Skybridge first, ``intent_router.detect_intent`` only when Skybridge
    claimed nothing (the order of ``tests/test_ha_phrasing_corpus.py``)."""
    import intent_router
    import skybridge_service

    sky = skybridge_service.classify_skybridge_intent(text, context)
    if sky is not None:
        secs = int(sky.duration_seconds) if sky.domain == "timer" and sky.duration_seconds else None
        tag = f"sky:{sky.domain}/{sky.action}"
        target = (sky.query or sky.entity_id or "").strip().lower()
        item = (sky.item_text or "").strip().lower()
        key = "|".join((tag, target, item, (sky.list_name or sky.list_type or "").lower(), str(secs or ""),
                        str(sky.start_seconds or ""), (sky.range_label or "").lower()))
        return Claim(tag, key, secs, item, target)
    intent = intent_router.detect_intent(text, log_miss=False)
    if intent is None:
        return None
    slots = intent.slots or {}
    sub = slots.get("action") or slots.get("command")
    tag = f"ir:{intent.name}" + (f"/{sub}" if isinstance(sub, str) and sub else "")
    secs = None
    if intent.name == "timer_create":
        try:
            secs = int(round(float(slots.get("minutes")) * 60))
        except (TypeError, ValueError):
            secs = None
    item = str(slots.get("item") or "").strip().lower()
    target = str(slots.get("device") or slots.get("entity") or slots.get("query") or slots.get("room") or "").lower()
    key = "|".join((tag, json.dumps(slots, sort_keys=True, default=str)))
    return Claim(tag, key, secs, item, target)


# ── safety ──────────────────────────────────────────────────────────────────

READ, ACT, CONFIRM = "read", "act", "confirm"

#: the Skybridge / intent_router claims a snap may READ (and so answer directly)
_READ_SKY = re.compile(r"^sky:(?:clock/.*|weather/.*|timer/status|lists/(?:show|contains)|calendar/show"
                       r"|smart_home/status|music/status|[a-z_]+/delayed)$")
_READ_IR = frozenset({"ir:time_query", "ir:date_query", "ir:weather", "ir:list_show", "ir:calendar_show",
                      "ir:timer_status", "ir:reminder_list"})
#: the low-risk, reversible writes a snap may run without asking (each speaks its own receipt)
_ACT_SKY = frozenset({"sky:smart_home/turn_on", "sky:smart_home/turn_off", "sky:music/play", "sky:music/pause",
                      "sky:music/resume", "sky:lists/add_item", "sky:timer/create"})
#: domains a snap may serve at all (a write in one of these that is not low-risk asks first). Anything else -
#: people, memory, notes, greetings, panels, engineering - is never a snap target.
_SNAP_DOMAINS = re.compile(r"^(?:sky:(?:clock|weather|timer|lists|calendar|smart_home|music)/"
                           r"|ir:(?:time_query|date_query|weather|list_show|list_add|list_remove|calendar_show"
                           r"|calendar_create|timer_create|timer_status|timer_cancel|reminder_list|reminder_create"
                           r"|smart_home|music_play|music_control|music_volume|set_volume)\b)")
#: more than one device, or a device whose state is not trivially reversible / is security-relevant
_MULTI_RE = re.compile(r"\b(?:all|every|everything|both|whole|entire|house|home|lights|lamps|switches|plugs|fans"
                       r"|outlets|devices)\b")
_SENSITIVE_RE = re.compile(r"\b(?:lock|locks|door|doors|gate|garage|alarm|alarms|security|camera|cameras|thermostat"
                           r"|heater|heating|heat|cooling|air\s*con|aircon|ac|oven|stove|hob|kettle|blinds?|curtains?"
                           r"|shutters?|vacuum|mower|sprinklers?|valve|water)\b")


def snap_allowed(claim: Optional[Claim]) -> bool:
    """True when a snap may land on ``claim`` at all."""
    return claim is not None and bool(_SNAP_DOMAINS.match(claim.tag))


def safety(claim: Claim, snapped: str) -> str:
    """``read`` | ``act`` | ``confirm`` for a snapped command. Default is ``confirm``: an unlisted write always asks."""
    if _READ_SKY.match(claim.tag) or claim.tag in _READ_IR:
        return READ
    if claim.tag not in _ACT_SKY:
        return CONFIRM
    words = command_shapes.command_core(snapped)
    if claim.tag.startswith("sky:smart_home/"):
        if _MULTI_RE.search(words) or _SENSITIVE_RE.search(words):
            return CONFIRM
        return ACT
    if claim.tag == "sky:lists/add_item":
        item = claim.item
        if not command_shapes.plain_list_item(item) or re.search(r"\band\b|,|&", item):
            return CONFIRM          # one item only: "salt and pepper" asks
        return ACT
    if claim.tag == "sky:timer/create":
        return ACT if (claim.seconds or 0) > 0 else CONFIRM
    if claim.tag.startswith("sky:music/"):
        return CONFIRM if _SENSITIVE_RE.search(words) else ACT
    return CONFIRM


# ── sound-alike cost ────────────────────────────────────────────────────────

_VOICING = {frozenset(p) for p in ("td", "pb", "kg", "fv", "sz", "mn", "lr")}


def skeleton(word: str) -> str:
    """A rough consonant skeleton of how ``word`` SOUNDS in non-rhotic English ("turn" -> "tn", "two" -> "t", "timer" ->
    "tm", "of" / "off" -> "f", "had" / "add" -> "d"). Vowels go; so do the silent letters STT's sound-alikes differ by.
    "sh" / "ch" / "th" keep their own symbols (S / C / 0): "ship" is not "switch", "what" is not "weather"."""
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    if not w:
        return ""
    for a, b in (("tch", "C"), ("dge", "j"), ("ph", "f"), ("gh", ""), ("ck", "k"), ("wh", "w"), ("wr", "r"),
                 ("kn", "n"), ("qu", "kw"), ("sh", "S"), ("ch", "C"), ("th", "0"), ("x", "ks"), ("z", "s"), ("q", "k")):
        w = w.replace(a, b)
    w = re.sub(r"c(?=[eiy])", "s", w)
    w = w.replace("c", "k")
    if len(w) > 2 and w.endswith("e") and w[-2] not in "aeiou":
        w = w[:-1]                                   # silent final e: "time" -> "tim"
    w = re.sub(r"^h(?=[aeiou])", "", w)              # a dropped "h": "had" ~ "add"
    w = re.sub(r"(?<=[^aeiou])w(?=[aeiouy])", "", w)  # "two" -> "to"
    w = re.sub(r"(?<=[aeiouy])r(?![aeiouy])", "", w)  # non-rhotic: "turn" -> "tun", "timer" -> "time"
    w = re.sub(r"[aeiouy]", "", w)
    return re.sub(r"(.)\1+", r"\1", w)


def _weighted_lev(a: str, b: str) -> float:
    prev = [float(j) for j in range(len(b) + 1)]
    for i, ca in enumerate(a, 1):
        cur = [float(i)]
        for j, cb in enumerate(b, 1):
            sub = 0.0 if ca == cb else (0.4 if frozenset((ca, cb)) in _VOICING else 1.0)
            cur.append(min(prev[j] + 1.0, cur[j - 1] + 1.0, prev[j - 1] + sub))
        prev = cur
    return prev[-1]


def cost(heard: str, meant: str, *, before_vowel: bool = False) -> float:
    """0 (the same sound) .. 1 (nothing alike): the cheaper of character dissimilarity and skeleton distance.

    ``before_vowel``: the next word starts with a vowel, so a final "n" / "m" of ``meant`` may have been heard as the start
    of that word ("turn on" -> "tur non" -> "two on"); that reading costs ``LIAISON_COST`` on top."""
    a, b = heard.lower(), meant.lower()
    if a == b:
        return 0.0
    sa, sb = skeleton(a), skeleton(b)
    sk = _weighted_lev(sa, sb) / max(len(sa), len(sb)) if (sa or sb) else 1.0
    if before_vowel and len(sb) >= 2 and sb[-1] in "nm":
        sb2 = sb[:-1]
        sk = min(sk, _weighted_lev(sa, sb2) / max(len(sa), len(sb2), 1) + LIAISON_COST)
    if sk > SKELETON_MAX:
        return 1.0                 # spelled alike, but the consonants say otherwise ("front" / "forecast", "so" / "song")
    char = 1.0 - SequenceMatcher(None, a, b).ratio()
    return round(min(char, sk), 3)


def same_onset(heard: str, meant: str) -> bool:
    """The words start with the same sound (a dropped "h" allowed): STT keeps the onset far more often than not."""
    a = re.sub(r"^h(?=[aeiou])", "", heard.lower())
    b = re.sub(r"^h(?=[aeiou])", "", meant.lower())
    if not a or not b:
        return False
    if a[0] in "aeiou" or b[0] in "aeiou":
        return a[0] == b[0]
    sa, sb = skeleton(a), skeleton(b)
    return bool(sa) and bool(sb) and sa[0] == sb[0]


# ── the vocabulary ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Vocabulary:
    """What the first ``POSITIONS`` words of a command Zoe handles look like (``lexicons_data/stt_snap_vocab.json``)."""

    positions: tuple[frozenset[str], ...]
    bigrams: frozenset[str]     # "<i>|<word at i>|<word at i+1>"
    skipgrams: frozenset[str]   # "<i>|<word at i>|<word at i+2>"

    def supported(self, toks: list[str], i: int, word: str) -> bool:
        """Does ``word`` at place ``i`` sit next to a neighbour it is SEEN with in a command Zoe handles (the word before
        it, the word after it, or the word after that)? A swap with no such neighbour would make a command nobody says."""
        if i >= 1 and f"{i - 1}|{toks[i - 1]}|{word}" in self.bigrams:
            return True
        if i + 1 < len(toks) and i + 1 < POSITIONS and f"{i}|{word}|{toks[i + 1]}" in self.bigrams:
            return True
        return i + 2 < len(toks) and i + 2 < POSITIONS and f"{i}|{word}|{toks[i + 2]}" in self.skipgrams

    def opening_intact(self, toks: list[str], i: int) -> bool:
        """Only ONE word is misheard: the words BEFORE place ``i`` must already open a command Zoe handles ("turn" before a
        swapped second word, "set a" before a swapped third). "as the front door" never becomes "as the forecast door"."""
        if i == 0:
            return True
        if i == 1:
            return any(b.startswith(f"0|{toks[0]}|") for b in self.bigrams)
        return all(f"{j}|{toks[j]}|{toks[j + 1]}" in self.bigrams for j in range(i - 1))


_EMPTY = Vocabulary(tuple(frozenset() for _ in range(POSITIONS)), frozenset(), frozenset())


@lru_cache(maxsize=1)
def vocabulary() -> Vocabulary:
    """The committed, generated vocabulary (read once). A missing / broken file disables the snap, never the turn."""
    try:
        data = json.loads(VOCAB_FILE.read_text(encoding="utf-8"))
        return Vocabulary(tuple(frozenset(p) for p in data["positions"][:POSITIONS]),
                          frozenset(data["bigrams"]), frozenset(data["skipgrams"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("stt_snap: vocabulary unavailable (%s): snap disabled", type(exc).__name__)
        return _EMPTY


def command_verbs() -> list[str]:
    """The command verbs ``command_shapes`` reads (its cancel / change shapes): the first word of each alternative of its
    verb patterns. Added to position 0 of the derived vocabulary."""
    groups = [command_shapes._CANCEL_VERB, command_shapes._INCREASE_VERBS, command_shapes._DECREASE_VERBS]
    for pattern, _direction in command_shapes._ADJUST_RES:
        # only the LEADING verb group of an adjust shape ("^(?:add|put|give|stick)\s+..." / "^make\s+...")
        m = re.match(r"\^(?:\(\?:(?P<alts>[a-z|]+)\)|(?P<lit>[a-z]+)\\s)", pattern.pattern)
        if m:
            groups.append(m.group("alts") or m.group("lit"))
    out: set[str] = set()
    for group in groups:
        for alt in re.sub(r"^\(\?:|\)$", "", group).split("|"):
            m = re.match(r"([a-z]{2,})", alt)
            if m:
                out.add(m.group(1))
    return sorted(out)


def _lead(text: str) -> list[str]:
    return _TOKEN_RE.findall(command_shapes.command_core(text))[:POSITIONS]


def derive_vocabulary(command_texts: list[str]) -> dict[str, Any]:
    """The vocabulary file's content from the commands Zoe's matchers handle CORRECTLY (the corpus rows the test selects)
    plus ``command_verbs``. Deterministic (sorted lists). Number words never enter: a number is a slot, not a sound."""
    positions: list[set[str]] = [set() for _ in range(POSITIONS)]
    bigrams: set[str] = set()
    skipgrams: set[str] = set()
    for text in command_texts:
        lead = _lead(text)
        for i, tok in enumerate(lead):
            if re.fullmatch(r"[a-z][a-z']+", tok) and tok not in command_shapes.WORD_NUMBERS:
                positions[i].add(tok)
            if i + 1 < len(lead):
                bigrams.add(f"{i}|{tok}|{lead[i + 1]}")
            if i + 2 < len(lead):
                skipgrams.add(f"{i}|{tok}|{lead[i + 2]}")
    positions[0] |= set(command_verbs())
    return {"positions": [sorted(p) for p in positions], "bigrams": sorted(bigrams), "skipgrams": sorted(skipgrams)}


# ── the decision ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Snap:
    heard: str
    text: str            # the snapped command (wake / politeness words off)
    claim: Claim
    safety: str
    cost: float
    position: int
    swapped: tuple[str, str]
    runner_up: Optional[float] = None

    @property
    def from_hash(self) -> str:
        return hashlib.sha1(self.heard.strip().lower().encode("utf-8")).hexdigest()[:12]

    @property
    def intent(self) -> str:
        return self.claim.tag


@dataclass
class Decision:
    snap: Optional[Snap]
    reason: str
    candidates: int = 0
    details: dict[str, Any] = field(default_factory=dict)


def decide(text: str, *, context: Optional[dict] = None,
           claim_fn: Callable[[str, Optional[dict]], Optional[Claim]] = claim_of) -> Decision:
    """Whether ``text`` snaps, and to what. Deterministic; never raises (a failure is ``reason="error"``, no snap)."""
    try:
        return _decide(text, context, claim_fn)
    except Exception as exc:  # noqa: BLE001 - the snap must never break a turn
        logger.warning("stt_snap: decide failed (%s)", type(exc).__name__)
        return Decision(None, "error")


def _decide(text: str, context: Optional[dict], claim_fn) -> Decision:
    core = command_shapes.command_core(text)
    spans = [(m.start(), m.end(), m.group(0)) for m in _TOKEN_RE.finditer(core)]
    if not (MIN_TOKENS <= len(spans) <= MAX_TOKENS):
        return Decision(None, "length")
    if claim_fn(text, context) is not None or (core != (text or "").strip().lower() and claim_fn(core, context) is not None):
        return Decision(None, "claimed")
    if command_shapes.asks_or_mentions(core, destructive=True):
        return Decision(None, "question")
    vocab = vocabulary()
    toks = [w for _s, _e, w in spans]
    cands: list[tuple[float, int, str, str]] = []
    likeliest = 1.0          # the cheapest sound-alike swap anywhere, whether or not it makes a command Zoe serves
    for i, word in enumerate(toks[:POSITIONS]):
        if len(word) < 2 or word in _NEVER_REPAIR or not re.fullmatch(r"[a-z][a-z']*", word):
            continue
        if (word in _NUMBER_WORDS and i > 0) or not vocab.opening_intact(toks, i):
            continue
        # a word that already opens Zoe's commands at this place is LIKELY what was said: swapping it costs more
        penalty = IN_VOCAB_PENALTY if word in vocab.positions[i] else 0.0
        before_vowel = i + 1 < len(toks) and toks[i + 1][:1] in "aeiou"
        for v in vocab.positions[i]:
            # a swap may land ON a polarity word ("turn of the lights" -> "off"): the margin rule below is what stops
            # it choosing between "off" and "on" on a coin toss
            if v == word or not same_onset(word, v):
                continue
            c = round(cost(word, v, before_vowel=before_vowel) + penalty, 3)
            if c > MAX_COST:
                continue
            likeliest = min(likeliest, c)
            if vocab.supported(toks, i, v):
                cands.append((c, i, word, v))
    if not cands:
        return Decision(None, "no_candidate")
    cands.sort(key=lambda x: (x[0], x[1], x[3]))
    cands = cands[:MAX_CANDIDATES]
    groups: dict[str, tuple[float, int, str, str, str, Claim]] = {}
    for c, i, word, v in cands:
        s, e, _w = spans[i]
        new = core[:s] + v + core[e:]
        claim = claim_fn(new, None)
        if not snap_allowed(claim):
            continue
        best = groups.get(claim.key)
        if best is None or c < best[0]:
            groups[claim.key] = (c, i, word, v, new, claim)
    if not groups:
        return Decision(None, "no_match", len(cands))
    ranked = sorted(groups.values(), key=lambda g: (g[0], g[1]))
    c, i, word, v, new, claim = ranked[0]
    runner = ranked[1][0] if len(ranked) > 1 else None
    if runner is not None and runner - c < MARGIN:
        return Decision(None, "ambiguous", len(cands), {"intents": [g[5].tag for g in ranked[:3]]})
    if c - likeliest >= MARGIN:
        # a clearly LIKELIER repair ("pours" -> "pause", or "his" -> "is" elsewhere in the words) makes no command Zoe
        # serves: snapping to a less likely one ("play") would answer a request nobody made
        return Decision(None, "likelier_unserved", len(cands))
    return Decision(Snap(heard=text, text=new, claim=claim, safety=safety(claim, new), cost=c, position=i,
                         swapped=(word, v), runner_up=runner), "snapped", len(cands))


# ── runtime helpers (the voice lane) ────────────────────────────────────────


def mode() -> str:
    raw = (os.environ.get("ZOE_STT_SNAP", "shadow") or "shadow").strip().lower()
    return raw if raw in MODES else "shadow"


def expert_may_claim(router_decision: Optional[dict]) -> bool:
    """Could the Tier-1.5 domain experts act on the ORIGINAL words (``fast_tiers`` domain half on the voice channel)?
    A conservative over-approximation of ``expert_dispatch.dispatch``'s own gates - mode, allow-listed domain, voice's
    deferred domains, score threshold - so the snap never pre-empts a fast tier that would have answered."""
    try:
        import expert_dispatch as _xd
        import fast_tiers as _ft

        if not _xd.is_enabled() or _xd.mode() != "active" or not isinstance(router_decision, dict):
            return False
        domain = str(router_decision.get("domain") or "").strip().lower()
        if domain in ("", "chat") or domain in _ft.profile_for("voice").get("defer_domains", ()):
            return False
        if domain not in _xd._active_domains():
            return False
        return float(router_decision.get("score") or 0.0) >= _xd.threshold_for(domain)
    except Exception:  # noqa: BLE001 - when unsure, assume the expert may answer (no snap)
        return True


async def route_for(text: str) -> Optional[dict]:
    """The router's verdict on the SNAPPED words (Skybridge's router veto must judge what will run). None when the router
    is off or fails - Skybridge then applies no veto, as on any turn without a verdict. Off the event loop
    (``asyncio.to_thread``, A1): the sidecar decode blocks ~400 ms. Never raises."""
    try:
        import semantic_router as _sr

        return await asyncio.to_thread(_sr.route, text) if _sr.is_enabled() else None
    except Exception:  # noqa: BLE001
        return None


def confirm_question(snap: Snap) -> str:
    return f"Did you mean: {snap.text}?"


def log_line(snap: Snap, outcome: str, *, guest: bool) -> None:
    """``STT_SNAP from=<hash> to=<intent> outcome=<...>``; a member's line also carries the words, a guest's never."""
    words = "" if guest else f" heard={snap.heard[:80]!r} as={snap.text[:80]!r}"
    logger.warning("STT_SNAP from=%s to=%s outcome=%s safety=%s cost=%.2f pos=%d mode=%s%s",
                   snap.from_hash, snap.intent, outcome, snap.safety, snap.cost, snap.position, mode(), words)


def note_struggle(snap: Snap, outcome: str, *, turn_text: str) -> None:
    """Hand the snap to the struggle detector (a MISHEAR with fix ``stt_snapped`` on this turn). Never raises."""
    try:
        import struggle_detector as _sd

        _sd.note_snap(turn_text, {"outcome": outcome, "intent": snap.intent, "safety": snap.safety,
                                  "cost": snap.cost, "position": snap.position})
    except Exception:  # noqa: BLE001
        pass
