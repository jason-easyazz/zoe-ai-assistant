"""Restraint in code (``ZOE_RESTRAINT`` = off | shadow | enforce, default shadow).

Register item BP1; person-likeness record rules SAL3 (sensitive classes wait for a pull), SAL6 (a
spoken "don't bring that up" is a mute that survives), SAL7 (an ignored raise doubles the wait);
blueprint law L3 (withhold, do not instruct): what must not be mentioned is never put in front of
the model, so every rule here REMOVES material from the packet, the brief or the raise. There is no
"do not mention" line anywhere in this module and a test pins that.

Five parts, one module (the pure core imports nothing heavy; I/O imports are lazy):

1. **A sensitivity class** on memory rows and threads: ``health``, ``money``, ``family_conflict``,
   ``grief``, ``other_member`` (a row about another person) and ``affect`` (an emotional moment).
   Decided in code from STRUCTURED signals first (``memory_type``, ``entity_type``, the captured
   ``affect``, the candidate ``kind``) and a word list second (the union rule of the blueprint:
   the lexicon is the English-only fallback, the structured signal does not care about language).
   Stored on a new memory row's metadata (``sensitivity`` / ``sensitivity_v`` / ``sensitivity_h``) and
   for threads in ``restraint_classes`` (migration 0042). INVALIDATE, NEVER DELETE: a stored class
   is trusted only while its classifier version and text hash still match; otherwise it is ignored
   (and, for threads, marked ``invalid_at`` and superseded by a new row), never overwritten.
2. **Withhold** (``apply_to_packet`` / ``filter_brief_ctx`` / ``decide``): in enforce, a row or
   thread in a sensitive class is removed from the recall packet, the ``[Today]`` brief and the
   ``[RAISE]`` pick unless the turn PULLS it: the owner's words share a topic with it, name its
   class, are a mood statement (affect only) or are an open question ("what's up?"). A bare
   "good morning" is not a pull.
3. **A spoken mute** (``handle_turn``): "don't mention that again", "stop bringing that up",
   "leave it" and their natural variants mute the thread or topic they refer to, persistently
   (``restraint_mutes``: a row with provenance, never the owner's words; only topic stems, the
   session, a digest of the turn and the pattern id). "You can mention it again" releases it
   (``status = released``, the row stays). A deterministic acknowledgement is spoken in enforce.
4. **Back-off** (``backoff_why``): the delivery ledger's ``outcome`` column is the evidence. Each
   consecutive ``ignored`` raise of a kind doubles the wait before that kind is raised again
   (capped), and three unanswered raises in a row pause raising altogether.
5. **The guest rule**: with the speaker gate saying the voice is NOT a confirmed member
   (``speaker_verified is False``), sensitive classes are withheld whatever the turn pulls.

Modes. ``off`` = nothing runs. ``shadow`` (default) = every decision is made and logged (counts and
class names only, never text) but nothing is removed and no acknowledgement is spoken; mute
utterances are still RECORDED so the data exists when the flag flips. ``enforce`` = the decisions
bite. Every entry point is fail-open (a failure is no restraint, never a broken turn) except the
guest rule, which is pure code with no I/O to fail.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import restraint_lex as _lex

logger = logging.getLogger(__name__)

ENV = "ZOE_RESTRAINT"
VERSION = 2  # the classifier version: bump it and every stored class is invalid (read as unstored)
CLASSES = ("health", "money", "family_conflict", "grief", "other_member", "affect")
SURFACES = ("packet", "brief", "raise_greeting", "raise_cue", "card")

_OFF = frozenset({"0", "false", "no", "off", "disabled"})
_ENFORCE = frozenset({"enforce", "1", "true", "yes", "on"})


def mode() -> str:
    """``off`` | ``shadow`` (default: unset, ``shadow`` or an unrecognised value) | ``enforce``.
    Per-call env read, so a restart flips it with no code change."""
    raw = (os.environ.get(ENV) or "").strip().lower()
    if raw in _OFF:
        return "off"
    if raw in _ENFORCE:
        return "enforce"
    return "shadow"


# ── text helpers (pure) ─────────────────────────────────────────────────────
# Every WORD this module decides by (the class lists, the stop words, the pull phrases, the mute grammar, the spoken
# acknowledgements) lives in ``lexicons_data/<lang>.json`` under "restraint" and is compiled by ``restraint_lex``.
_norm = _lex.norm


def _stem(token: str) -> str:
    """Cheap singular form (the ledger's rule): appointments -> appointment, dentists -> dentist."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith(("sses", "xes", "ches", "shes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _topic_token(t: str, p: "_lex.Pack") -> str:
    """Kin words are 3-6 letters and are exactly what "how's my mum doing?" is about: the language's lexicon canonicalises
    them (mum = mom = mother)."""
    return p.kin_canon.get(t) or _stem(t)


def _tokens(text: Any, p: "_lex.Pack") -> list[str]:
    """Topic tokens of ``text``: words, or (a CJK language writes no spaces) the character bigrams of each run."""
    words = _norm(text).split()
    if not p.cjk:
        return words
    # a CJK language writes no spaces: the character bigrams of each run, minus any that holds a stop character
    # (a particle: "の", "的") - the same job the stop list does for a spaced language
    return [b for run in words for b in (run[i:i + 2] for i in range(max(len(run) - 1, 1)))
            if b not in p.stop and not any(ch in p.stop for ch in b)]


def stems(text: Any) -> frozenset[str]:
    """Content stems of ``text`` (>= 4 letters or a kin word, no stop word; a CJK bigram counts). The overlap test of a topic
    pull and of a mute."""
    p = _lex.pack_for(text)
    return frozenset(_topic_token(t, p) for t in _tokens(text, p)
                     if (len(t) >= (2 if p.cjk else 4) or t in p.kin_canon) and t not in p.stop)


def text_hash(text: Any) -> str:
    return hashlib.sha1(_norm(text).encode("utf-8")).hexdigest()[:12]


def turn_key(message: Any) -> str:
    """A digest of the turn's words: provenance without the words (the owner's sentence is never
    stored by this module)."""
    return hashlib.sha1(_norm(message).encode("utf-8")).hexdigest()[:16]


def _ts(value: Any) -> str:
    """A timestamp compared at second precision (the ledger writes microseconds, candidates seconds)."""
    return str(value or "")[:19]


# ── 1. the sensitivity class (pure) ─────────────────────────────────────────
# The word lists are data: ``lexicons_data/<lang>.json`` "restraint" (health, money, grief, conflict, kin, feelings ...),
# compiled per language by ``restraint_lex.pack``. The language is the text's own; a language with no entry adds nothing
# and the structured signals below still decide.
_AFFECT_WORD = re.compile(r"[a-z][a-z ]{0,23}")


def _name_rx(names: Iterable[str]) -> Optional["re.Pattern[str]"]:
    parts = sorted({re.escape(n.strip().lower()) for n in names if n and len(n.strip()) >= 3}, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b") if parts else None


# Tags a writer put on a row (``MemoryService`` stores them as a comma-separated string): a structured
# signal that does not depend on the language of the row's text.
_TAG_CLASSES = {
    "health": "health", "medical": "health", "medication": "health", "allergy": "health", "illness": "health",
    "symptom": "health", "dental": "health", "money": "money", "finance": "money", "financial": "money",
    "debt": "money", "bank": "money", "grief": "grief", "bereavement": "grief", "conflict": "family_conflict",
}


def classify_full(text: Any, *, memory_type: Any = "", affect: Any = "", entity_type: Any = "",
                  kind: str = "", names: Iterable[str] = (), tags: Any = "") -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(classes, signals)`` for one row or thread. ``signals`` names what decided each class
    (``entity``, ``type``, ``tag``, ``affect``, ``kind``, ``lexicon``, ``name``) so a test or an audit can
    tell a structured decision from a word-list one. Pure, no I/O, order follows ``CLASSES``."""
    raw = str(text or "")
    low = raw.lower().replace("\u2019", "'")
    pk = _lex.pack_for(raw)
    found: dict[str, str] = {}

    for tag in str(tags or "").lower().split(","):
        cls = _TAG_CLASSES.get(tag.strip())
        if cls:
            found[cls] = "tag"
    for cls, rx in (("health", pk.health), ("money", pk.money), ("grief", pk.grief),
                    ("family_conflict", pk.family_conflict)):
        if cls not in found and rx is not None and rx.search(low):
            found[cls] = "lexicon"

    # the structured signals: they do not depend on the language the row is written in
    mtype = str(memory_type or "").strip().lower()
    etype = str(entity_type or "").strip().lower()
    if mtype == "person" or etype == "person":
        found["other_member"] = "entity"
    elif (pk.other_kin is not None and pk.other_kin.search(low)) or (pk.kin_poss is not None and pk.kin_poss.search(low)):
        found["other_member"] = "lexicon"
    else:
        for m in (pk.name_poss.finditer(raw) if pk.name_poss is not None else ()):
            if m.group(1).lower() not in pk.not_a_name:
                found["other_member"] = "lexicon"
                break
        else:
            rx = _name_rx(names)
            if rx and rx.search(low):
                found["other_member"] = "name"

    felt = str(affect or "").strip().lower()
    if mtype == "emotional_moment":
        found["affect"] = "type"
    elif felt and _AFFECT_WORD.fullmatch(felt):
        found["affect"] = "affect"
    elif kind == "emotional":
        found["affect"] = "kind"
    elif pk.affect is not None and pk.affect.search(low):
        found["affect"] = "lexicon"

    classes = tuple(c for c in CLASSES if c in found)
    return classes, tuple(f"{c}:{found[c]}" for c in classes)


def classify(text: Any, **kw: Any) -> tuple[str, ...]:
    return classify_full(text, **kw)[0]


def _parse_csv(raw: Any) -> tuple[str, ...]:
    have = {x.strip() for x in str(raw or "").split(",")}
    return tuple(c for c in CLASSES if c in have)


def stamp(md: dict, text: Any) -> None:
    """Write the class onto a NEW memory row's metadata (``MemoryService._build_metadata``). A no-op
    with the flag off. Never raises: a row is never lost to its own label."""
    try:
        if mode() == "off":
            return
        classes = classify(text, memory_type=md.get("memory_type"), affect=md.get("candidate_affect"),
                           entity_type=md.get("entity_type"), tags=md.get("tags"))
        md["sensitivity"] = ",".join(classes)
        md["sensitivity_v"] = VERSION
        md["sensitivity_h"] = text_hash(text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("restraint: stamp skipped: %r", exc)


def row_classes(meta: Optional[dict], text: Any) -> tuple[str, ...]:
    """The classes of a stored memory row: the STORED label while its version and text hash still
    match (a label for other words, or from another classifier, is invalid and ignored, never
    deleted), else a fresh classification. Pure."""
    meta = meta or {}
    if (meta.get("sensitivity_v") == VERSION and meta.get("sensitivity_h") == text_hash(text)
            and "sensitivity" in meta):
        return _parse_csv(meta.get("sensitivity"))
    return classify(text, memory_type=meta.get("memory_type"), affect=meta.get("candidate_affect"),
                    entity_type=meta.get("entity_type"), tags=meta.get("tags"))


# ── the turn: who is speaking and what the owner just asked (pure) ───────────
def is_pull(message: Any) -> bool:
    """True for an open question to Zoe about what is pending: "what's up?", "how's it going?",
    "anything I need to know?", "what do you know about me?". A bare greeting is NOT a pull. Pure.
    The phrases are the lexicon's (``pull_phrases``); the text's own language is tried first, then every other language
    that has them (a short "como estas?" carries too few function words to detect)."""
    for p in _lex.packs_for(message):
        words = _norm(message).split()
        while words and words[0] in p.pull_leads:
            words.pop(0)
        while words and words[-1] in p.pull_tail:
            words.pop()
        if (" ".join(words) if not p.cjk else "".join(words)) in p.pull_phrases:
            return True
    return False


# The verdict of the speaker gate for THIS turn: True = a confirmed member, False = the gate ran and
# did NOT confirm one (a guest is, or may be, speaking), None = no verdict (gate off / shadow / typed).
_VERDICT: "contextvars.ContextVar[Optional[bool]]" = contextvars.ContextVar("restraint_verdict", default=None)


def bind_verdict(verdict: Optional[bool]) -> None:
    """Called once per voice turn where the gate's verdict is known (``routers/voice_tts``)."""
    _VERDICT.set(verdict)


def current_verdict() -> Optional[bool]:
    return _VERDICT.get()


@dataclass(frozen=True)
class Turn:
    message: str = ""
    verdict: Optional[bool] = None
    pull_all: bool = False       # an open question: everything pending may be delivered
    mood: bool = False           # a first-person mood statement: affect rows are pulled
    stems: frozenset = frozenset()
    classes: tuple = ()          # the sensitive classes the owner's own words name


def make_turn(message: Any, *, verdict: Optional[bool] = None, mood: bool = False) -> Turn:
    msg = str(message or "")
    return Turn(message=msg, verdict=verdict, pull_all=is_pull(msg), mood=bool(mood),
                stems=stems(msg), classes=classify(msg))


# Turn marks by user: the recall_memory TOOL call made during a turn carries only the model's query,
# not the owner's words (the recall_evidence / exact_words pattern), so the turn is noted at its start.
_MARK_TTL_S = 120.0
_PREV_TTL_S = 300.0   # the owner's PREVIOUS turn still counts as the topic ("are you sure?" continues it)
_MARK_MAX = 256
_marks: dict[str, tuple[str, Optional[bool], float, str]] = {}


def note_turn(user_id: str, message: Any) -> None:
    """Remember the owner's words + the gate's verdict for this turn. No-op when off; never raises."""
    try:
        uid = (user_id or "").strip()
        if not uid or mode() == "off":
            return
        now = time.monotonic()
        old = _marks.pop(uid, None)
        prev = old[0] if old and old[0] != str(message or "")[:512] and now - old[2] <= _PREV_TTL_S else ""
        _marks[uid] = (str(message or "")[:512], current_verdict(), now, prev)
        while len(_marks) > _MARK_MAX:
            _marks.pop(next(iter(_marks)))
    except Exception:  # noqa: BLE001
        return


def turn_for(user_id: str, message: Any, *, mood: bool = False) -> Turn:
    """The turn a packet is built for: the noted owner's words when a fresh mark exists (the tool path),
    else ``message`` itself (the recall floor passes the owner's own words)."""
    mark = _marks.get((user_id or "").strip())
    if mark and time.monotonic() - mark[2] <= _MARK_TTL_S:
        turn = make_turn(mark[0], verdict=mark[1], mood=mood)
        if mark[3]:
            turn = replace(turn, stems=turn.stems | stems(mark[3]))
        return turn
    return make_turn(message, verdict=current_verdict(), mood=mood)


# ── 3a. mutes: the match (pure) ──────────────────────────────────────────────
@dataclass(frozen=True)
class Mute:
    id: str
    stems: frozenset
    thread_ref: str = ""
    created_at: str = ""


def muted(text: Any, source_ref: str, mutes: Iterable[Mute]) -> bool:
    """True when ``text`` (or the thread ``source_ref``) is covered by an ACTIVE mute: the same thread,
    or a topic overlap of ``min(len(mute), 2)`` stems (one stem mutes a one-word topic, two are needed
    to match a longer one, so "dentist" mutes the dentist and "Friday" alone does not)."""
    t = stems(text)
    for m in mutes:
        if m.thread_ref and source_ref and m.thread_ref == source_ref:
            return True
        if m.stems and len(t & m.stems) >= min(len(m.stems), 2):
            return True
    return False


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str = ""             # "" | "guest" | "sensitive" | "muted"
    classes: tuple = ()


def decide(text: Any, classes: Iterable[str], turn: Turn, mutes: Iterable[Mute] = (), *,
           surface: str, source_ref: str = "") -> Decision:
    """The one rule. Pure.

    * ``guest`` (verdict False): a sensitive row is withheld whatever the turn pulls, on every surface.
    * ``muted``: unprompted surfaces (brief, either raise) never carry it; the recall packet withholds it
      unless the owner's own words share its topic (they asked).
    * sensitive: delivered only on a pull. ``raise_cue`` IS a topic pull (the owner's words matched the
      candidate's anchors); the brief and a greeting raise need an open question; the packet also takes a
      topic overlap, a named class, or (affect rows only) a mood statement.
    """
    cls = tuple(c for c in CLASSES if c in set(classes))
    if cls and turn.verdict is False:
        return Decision(False, "guest", cls)
    topic = bool(turn.stems & stems(text))
    if muted(text, source_ref, mutes):
        if surface == "packet" and topic:
            return Decision(True, "", cls)
        return Decision(False, "muted", cls)
    if not cls:
        return Decision(True)
    if surface == "raise_cue" or turn.pull_all:
        return Decision(True, "", cls)
    if surface == "packet" and (topic or (turn.mood and "affect" in cls) or (set(turn.classes) & set(cls))):
        return Decision(True, "", cls)
    return Decision(False, "sensitive", cls)


# ── counters + the one log line (counts and class names only, never text) ────
_counts: dict[tuple[str, str, str], int] = {}


def counts() -> dict[tuple[str, str, str], int]:
    """``{(surface, reason, mode): n}`` since the process started (tests and probes)."""
    return dict(_counts)


def _note(uid: str, surface: str, withheld: int, reasons: dict[str, int], classes: dict[str, int],
          enforced: bool) -> None:
    m = "enforce" if enforced else "shadow"
    for reason, n in reasons.items():
        _counts[(surface, reason, m)] = _counts.get((surface, reason, m), 0) + n
    logger.info("RESTRAINT user=%s surface=%s mode=%s withheld=%d reasons=%s classes=%s", uid, surface, m,
                withheld, ",".join(f"{k}:{v}" for k, v in sorted(reasons.items())) or "-",
                ",".join(f"{k}:{v}" for k, v in sorted(classes.items())) or "-")


def gate(user_id: str, dec: Decision, surface: str) -> bool:
    """Keep this item? ``enforce`` honours the decision; ``shadow`` logs a would-withhold and keeps it."""
    if dec.allow:
        return True
    enforced = mode() == "enforce"
    _note(user_id, surface, 1, {dec.reason: 1}, {c: 1 for c in dec.classes}, enforced)
    return not enforced


# ── 2. withhold from the recall packet ───────────────────────────────────────
def _meta_text(ref: Any) -> tuple[dict, str]:
    return (getattr(ref, "metadata", None) or {}), str(getattr(ref, "text", "") or "")


async def apply_to_packet(user_id: str, message: Any, facts: list, hits: list,
                          recent: Optional[list] = None, *, mood: bool = False) -> tuple[list, list, Optional[list]]:
    """Filter the three row lists ``routers.memories.memory_for_prompt`` hands the packet builder.
    ``off`` and ``shadow`` return them unchanged (shadow logs what enforce would remove). Never raises."""
    m = mode()
    if m == "off" or not (facts or hits or recent):
        return facts, hits, recent
    try:
        turn = turn_for(user_id, message, mood=mood)
        mutes = await list_mutes(user_id)
        drop: dict[str, Decision] = {}
        seen: set[str] = set()
        for ref in list(recent or []) + list(hits) + list(facts):
            rid = str(getattr(ref, "id", ""))
            if not rid or rid in seen:
                continue
            seen.add(rid)
            meta, text = _meta_text(ref)
            dec = decide(text, row_classes(meta, text), turn, mutes, surface="packet", source_ref=f"memory:{rid}")
            if not dec.allow:
                drop[rid] = dec
        if not drop:
            return facts, hits, recent
        reasons: dict[str, int] = {}
        classes: dict[str, int] = {}
        for dec in drop.values():
            reasons[dec.reason] = reasons.get(dec.reason, 0) + 1
            for c in dec.classes:
                classes[c] = classes.get(c, 0) + 1
        _note(user_id, "packet", len(drop), reasons, classes, m == "enforce")
        if m != "enforce":
            return facts, hits, recent

        def keep(rows: Optional[list]) -> Optional[list]:
            return None if rows is None else [r for r in rows if str(getattr(r, "id", "")) not in drop]

        return keep(facts) or [], keep(hits) or [], keep(recent)
    except Exception as exc:  # noqa: BLE001 — restraint must never break a turn
        logger.warning("restraint: packet filter failed (no restraint this turn): %r", exc)
        return facts, hits, recent


async def filter_extra(user_id: str, message: Any, texts: list, *, pull: bool = True) -> list[bool]:
    """Per text: may it ride this turn? For the blocks appended to the packet AFTER ``apply_to_packet`` (the personalisation hop's
    durable facts, the night mind's quotes), which that filter never sees. ``pull`` = the lookup that chose the text was already the
    owner pulling it (an advice request, a named story), so the only walls are the guest wall (a voice the speaker gate did not
    confirm hears no sensitive text) and the member's mutes. ``off`` and ``shadow`` keep everything (shadow logs). Never raises."""
    keep = [True] * len(texts)
    m = mode()
    if m == "off" or not texts:
        return keep
    try:
        turn = turn_for(user_id, message)
        if pull and not turn.pull_all:
            turn = replace(turn, pull_all=True)
        mutes = await list_mutes(user_id)
        for n, text in enumerate(texts):
            keep[n] = gate(user_id, decide(text, classify(text), turn, mutes, surface="packet"), "packet")
        return keep
    except Exception as exc:  # noqa: BLE001 - restraint must never break a turn
        logger.warning("restraint: extra-block filter failed (no restraint this turn): %r", exc)
        return [True] * len(texts)


# ── 2c. withhold from the always-present user-model card ──────────────────────
#: Classes a CARD line never carries in enforce: the card is in front of the model on every turn, so there is no pull that
#: could release it (money, grief and family trouble wait to be asked about). The Health line keeps only what the model must
#: know to be safe (the lexicon's ``health_safety``: allergy, asthma, diabetes, medication, pregnancy, access needs); the rest
#: of it (migraines, a knee, insomnia, a surgery) waits for the recall packet, which the owner's own question pulls.
CARD_WITHHELD = ("money", "grief", "family_conflict")


def card_withheld(category: str, text: Any) -> tuple[str, ...]:
    """The classes that keep one card item (``category``, ``text``) off the card; ``()`` = it stays. Pure."""
    held = [c for c in CARD_WITHHELD if c in classify(text)]
    if category == "health":
        safety = _lex.pack_for(text).safety
        if safety is None or not safety.search(str(text or "").lower()):
            held.append("health")
    return tuple(held)


def filter_card_items(user_id: str, items: list) -> list:
    """The card's ``[category, text, memory_id]`` items minus the ones it may not carry. ``off`` and ``shadow`` return them
    unchanged (shadow logs what enforce would remove: counts and class names, never text). Never raises."""
    m = mode()
    if m == "off" or not items:
        return items
    try:
        drop = {str(it[2]): card_withheld(str(it[0]), it[1]) for it in items}
        drop = {k: v for k, v in drop.items() if v}
        if not drop:
            return items
        classes: dict[str, int] = {}
        for cls in drop.values():
            for c in cls:
                classes[c] = classes.get(c, 0) + 1
        _note(user_id, "card", len(drop), {"sensitive": len(drop)}, classes, m == "enforce")
        if m != "enforce":
            return items
        return [it for it in items if str(it[2]) not in drop]
    except Exception as exc:  # noqa: BLE001 - restraint must never break a turn
        logger.warning("restraint: card filter failed (no restraint this turn): %r", exc)
        return items


# ── 2b. withhold from the [Today] brief ──────────────────────────────────────
async def filter_brief_ctx(ctx: dict, user_id: str, message: Any) -> dict:
    """The brief's gathered context minus the threads this turn may not carry. Returns a COPY (the
    caller caches the raw context); ``off`` and ``shadow`` return it unchanged. Calendar events are
    left alone: they are the day, not a thread. Never raises."""
    m = mode()
    if m == "off" or not ctx:
        return ctx
    try:
        turn = make_turn(message, verdict=current_verdict())
        mutes = await list_mutes(user_id)
        # the SAME class the selector saved for this thread (and the same contact-name fallback), so a
        # loop that mentions a contact is not unclassified here
        stored: dict = {}
        names: list[str] = []
        try:
            from db_compat import get_compat_db

            async with get_compat_db() as db:
                stored, names = await load_thread_classes(db, user_id), await people_names(db, user_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("restraint: brief class read failed (text classes only): %r", exc)
        out = dict(ctx)
        enforced = m == "enforce"
        reasons: dict[str, int] = {}
        classes: dict[str, int] = {}

        def allowed(text: str, ref: str, kind: str) -> bool:
            dec = decide(text, thread_classes(ref, text, kind, stored, names), turn, mutes,
                         surface="brief", source_ref=ref)
            if not dec.allow:
                reasons[dec.reason] = reasons.get(dec.reason, 0) + 1
                for c in dec.classes:
                    classes[c] = classes.get(c, 0) + 1
            return dec.allow

        loops = []
        for lp in ctx.get("open_loops") or []:
            text = str(lp.get("text") or lp.get("hint") or "")
            ref = f"open_loops:{lp['id']}" if lp.get("id") is not None else ""
            if allowed(text, ref, "open_loop") or not enforced:
                loops.append(lp)
        out["open_loops"] = loops
        if turn.verdict is False:
            # a guest may be in the room: a sensitive calendar title (a clinic, a funeral) waits too
            cal = []
            for ev in ctx.get("calendar") or []:
                if classify(str(ev.get("title") or "")):
                    reasons["guest"] = reasons.get("guest", 0) + 1
                    if not enforced:
                        cal.append(ev)
                else:
                    cal.append(ev)
            out["calendar"] = cal
        moments, ids = [], []
        raw_ids = ctx.get("emotional_moment_ids") or []
        for i, text in enumerate(ctx.get("emotional_moments") or []):
            rid = raw_ids[i] if i < len(raw_ids) else None
            if allowed(str(text), f"memory:{rid}" if rid is not None else "", "emotional") or not enforced:
                moments.append(text)
                if rid is not None:
                    ids.append(rid)
        out["emotional_moments"] = moments
        if "emotional_moment_ids" in ctx:
            out["emotional_moment_ids"] = ids
        if reasons:
            _note(user_id, "brief", sum(reasons.values()), reasons, classes, enforced)
        return out if enforced else ctx
    except Exception as exc:  # noqa: BLE001
        logger.warning("restraint: brief filter failed (no restraint this turn): %r", exc)
        return ctx


# ── 3b. the spoken mute: parse (pure) ────────────────────────────────────────
@dataclass(frozen=True)
class Utterance:
    kind: str                    # "mute" | "release"
    stems: tuple = ()            # the topic the owner named (empty = "that")
    deictic: bool = False        # "that" / "it": the referent is the last thing raised
    needs_referent: bool = False  # a bare "leave it": only a mute when something was just raised
    pattern: str = ""            # the pattern id (provenance), never the words


_MAX_UTTERANCE_CHARS = 200


def _object(rest: str, p: "_lex.Pack") -> tuple[tuple[str, ...], bool]:
    content = [t for t in _tokens(rest, p) if t not in p.obj_drop and len(t) >= (2 if p.cjk else 3)]
    st = tuple(sorted({_topic_token(t, p) for t in content if t not in p.stop}))[:6]
    return st, not st


def _sub(p: "_lex.Pack", name: str, s: str) -> str:
    rx = p.mute.get(name)
    return rx.sub("", s) if rx is not None else s


def parse_utterance(message: Any) -> Optional[Utterance]:
    """A spoken mute ("don't mention that again", "stop bringing up the dentist", "I don't want to talk
    about it anymore", "that's enough about the interview", "leave it") or release ("you can mention
    the dentist again"), or None. Pure; the words are read here and never stored. The grammar is the
    lexicon's (``mute_patterns``): the text's own language first, then every other one that has it."""
    raw = str(message or "")
    if not raw.strip() or len(raw) > _MAX_UTTERANCE_CHARS:
        return None
    for p in _lex.packs_for(raw):
        u = _parse_with(p, raw)
        if u is not None:
            return u
    return None


def _parse_with(p: "_lex.Pack", raw: str) -> Optional[Utterance]:
    if not p.mute:
        return None
    s = _norm(raw)
    # a release is read BEFORE the modal lead is stripped: "you can mention it again" starts with one
    rel = p.mute.get("release")
    m = rel.match(_sub(p, "filler_lead", s).strip()) if rel is not None else None
    if m:
        rest = ((m.groupdict().get("mid") or "") + " " + (m.groupdict().get("rest") or "")).strip()
        st, deictic = _object(rest, p)
        return Utterance("release", st, deictic, False, "release")
    for _ in range(3):  # fillers and modal leads can alternate ("ok please could you not ...")
        s2 = _sub(p, "modal_lead", _sub(p, "filler_lead", s)).strip()
        if s2 == s:
            break
        s = s2
    if not s:
        return None
    for pid in ("neg", "want", "enough", "lets"):
        rx = p.mute.get(pid)
        m = rx.match(s) if rx is not None else None
        if m:
            rest = ((m.groupdict().get("mid") or "") + " " + (m.groupdict().get("rest") or "")).strip()
            st, deictic = _object(rest, p)
            return Utterance("mute", st, deictic, False, pid)
    rx = p.mute.get("leave")
    m = rx.match(s) if rx is not None else None
    if m and not set(_tokens(m.group("rest"), p)) <= p.person_obj:  # "leave me alone" is not a topic
        st, deictic = _object(m.group("rest"), p)
        return Utterance("mute", st, deictic, deictic, "leave")
    rx = p.mute.get("leave_bare")
    if rx is not None and rx.match(s):
        return Utterance("mute", (), True, True, "leave_bare")
    return None


# ── 3c. the mute store (migration 0042) ──────────────────────────────────────
# The spoken acknowledgements are the lexicon's, in the language the owner spoke (English when that language has none).
# These are the English ones (the replay harness and the tests compare against them).
ACK_MUTE = _lex.pack("en").ack["ack_mute"]
ACK_RELEASE = _lex.pack("en").ack["ack_release"]
ACK_RELEASE_NONE = _lex.pack("en").ack["ack_release_none"]
ACK_ASK = _lex.pack("en").ack["ack_ask"]

_MUTE_CACHE_S = 20.0
_mute_cache: dict[str, tuple[float, list[Mute]]] = {}
_FOCUS_TTL_S = 600.0
_focus: dict[str, tuple[float, tuple[str, ...]]] = {}


def note_focus(user_id: str, text: Any) -> None:
    """The thing a continuity turn just checked in about, so a following "leave it" has a referent.
    Never raises."""
    try:
        uid = (user_id or "").strip()
        st = tuple(sorted(stems(text)))[:6]
        if uid and st and mode() != "off":
            _focus[uid] = (time.monotonic(), st)
            while len(_focus) > _MARK_MAX:
                _focus.pop(next(iter(_focus)))
    except Exception:  # noqa: BLE001
        return


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def list_mutes(user_id: str) -> list[Mute]:
    """The member's ACTIVE mutes (cached 20 s, invalidated by every write here). Fail-open: a read that
    fails is no mutes."""
    uid = (user_id or "").strip()
    if not uid:
        return []
    hit = _mute_cache.get(uid)
    if hit and time.monotonic() - hit[0] < _MUTE_CACHE_S:
        return hit[1]
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            async with db.execute(
                "SELECT id, topic_key, thread_ref, created_at FROM restraint_mutes "
                "WHERE user_id = ? AND status = 'active'", (uid,),
            ) as cur:
                rows = await cur.fetchall()
        out = [Mute(str(r[0]), frozenset(str(r[1] or "").split()), str(r[2] or ""), str(r[3] or "")) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.debug("restraint: mute read failed (no mutes): %r", exc)
        return []
    _mute_cache[uid] = (time.monotonic(), out)
    while len(_mute_cache) > _MARK_MAX:
        _mute_cache.pop(next(iter(_mute_cache)))
    return out


async def _referent(db, uid: str, sid: str, now: datetime) -> Optional[tuple[str, tuple[str, ...]]]:
    """What "that" / "it" means: the thread raised (or marked by the brief) in THIS conversation, else the
    member's most recent raise within 12 h, else the item a continuity turn just checked in about."""
    queries = []
    if sid:
        queries.append(("SELECT source_ref, text, cue_words FROM proactive_candidates WHERE user_id = ? "
                        "AND last_surfaced_session = ? ORDER BY last_surfaced_at DESC LIMIT 1", (uid, sid)))
    queries.append(("SELECT source_ref, text, cue_words FROM proactive_candidates WHERE user_id = ? "
                    "AND last_surfaced_at >= ? ORDER BY last_surfaced_at DESC LIMIT 1",
                    (uid, _iso(now - timedelta(hours=12)))))
    try:
        for sql, params in queries:
            async with db.execute(sql, params) as cur:
                row = await cur.fetchone()
            if row:
                pk = _lex.pack_for(row[1])
                st = sorted({_topic_token(w, pk) for w in str(row[2] or "").split()} | set(stems(row[1])))[:6]
                return str(row[0]), tuple(st)
    except Exception as exc:  # noqa: BLE001
        logger.debug("restraint: referent read failed: %r", exc)
    note = _focus.get(uid)
    if note and time.monotonic() - note[0] <= _FOCUS_TTL_S:
        return "", note[1]
    return None


async def handle_turn(message: Any, user_id: str, session_id: str = "") -> str:
    """Recognise a spoken mute / release, record it, and return the acknowledgement to SPEAK ("" = say
    nothing, let the brain reply). Records in ``shadow`` and ``enforce`` (the data exists when the flag
    flips); speaks only in ``enforce``. NEVER raises."""
    uid, sid = (user_id or "").strip(), (session_id or "").strip()
    m = mode()
    if m == "off" or not uid:
        return ""
    try:
        from user_filters import GUEST_USERS

        if uid in GUEST_USERS:
            return ""  # a guest has no memory to mute and no standing to set a preference
        if current_verdict() is False:
            # the speaker gate did NOT confirm this voice at a member-bound panel: it may be a guest, and a
            # guest cannot set (or lift) the member's saved mutes, in shadow or in enforce
            logger.info("RESTRAINT_MUTE user=%s action=refused_unverified_speaker mode=%s", uid, m)
            return ""
        u = parse_utterance(message)
        if u is None:
            return ""
        from db_compat import get_compat_db

        now = datetime.now(timezone.utc)
        key = turn_key(message)
        async with get_compat_db() as db:
            if u.kind == "release":
                n = await _release(db, uid, u, key, now)
                logger.info("RESTRAINT_MUTE user=%s action=release n=%d mode=%s", uid, n, m)
                return (_lex.ack("ack_release", message) if n else _lex.ack("ack_release_none", message)) if m == "enforce" else ""
            thread_ref, topic = "", u.stems
            if u.deictic:
                ref = await _referent(db, uid, sid, now)
                if ref is None:
                    logger.info("RESTRAINT_MUTE user=%s action=unresolved mode=%s", uid, m)
                    return _lex.ack("ack_ask", message) if (m == "enforce" and not u.needs_referent) else ""
                thread_ref, topic = ref
            if not topic and not thread_ref:
                return ""
            created = await _add(db, uid, topic, thread_ref, sid, key, u.pattern, now)
        _mute_cache.pop(uid, None)
        logger.info("RESTRAINT_MUTE user=%s action=mute scope=%s new=%d mode=%s", uid,
                    "thread" if thread_ref else "topic", int(created), m)
        return _lex.ack("ack_mute", message) if m == "enforce" else ""
    except Exception as exc:  # noqa: BLE001 — a mute that cannot be recorded must not break the turn
        logger.warning("restraint: mute handling failed (turn continues): %r", exc)
        return ""


async def _add(db, uid: str, topic: tuple[str, ...], thread_ref: str, sid: str, key: str, pattern: str,
               now: datetime) -> bool:
    topic_key = " ".join(sorted(set(topic)))
    async with db.execute(
        "SELECT 1 FROM restraint_mutes WHERE user_id = ? AND status = 'active' AND topic_key = ? "
        "AND COALESCE(thread_ref, '') = ? LIMIT 1", (uid, topic_key, thread_ref),
    ) as cur:
        if await cur.fetchone():
            return False
    await db.execute(
        """INSERT INTO restraint_mutes (id, user_id, topic_key, thread_ref, scope, source, session_id,
               turn_key, phrase, status, created_at)
           VALUES (?, ?, ?, ?, ?, 'spoken', ?, ?, ?, 'active', ?)""",
        (uuid.uuid4().hex, uid, topic_key, thread_ref or None, "thread" if thread_ref else "topic",
         sid or None, key, pattern, _iso(now)))
    return True


async def _release(db, uid: str, u: Utterance, key: str, now: datetime) -> int:
    """Mark matching active mutes released (the rows stay: invalidate, never delete)."""
    async with db.execute(
        "SELECT id, topic_key, created_at FROM restraint_mutes WHERE user_id = ? AND status = 'active' "
        "ORDER BY created_at DESC", (uid,),
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return 0
    if u.deictic:  # "you can mention it again": the most recently muted thing
        ids = [str(rows[0][0])]
    else:
        want = set(u.stems)
        ids = [str(r[0]) for r in rows if want & set(str(r[1] or "").split())]
    for rid in ids:
        await db.execute(
            "UPDATE restraint_mutes SET status = 'released', released_at = ?, released_turn_key = ? "
            "WHERE id = ? AND user_id = ? AND status = 'active'", (_iso(now), key, rid, uid))
    _mute_cache.pop(uid, None)
    return len(ids)


async def erase_entity(user_id: str, name: str) -> int:
    """A forget of ``name`` also deletes the mutes whose topic names it (the forgotten-means-forever rule:
    a topic stem is still the name). Best-effort; returns the rows removed."""
    uid = (user_id or "").strip()
    st = stems(name)
    if not uid or not st:
        return 0
    try:
        from db_compat import get_compat_db

        n = 0
        async with get_compat_db() as db:
            async with db.execute("SELECT id, topic_key FROM restraint_mutes WHERE user_id = ?", (uid,)) as cur:
                rows = await cur.fetchall()
            for r in rows:
                if st & set(str(r[1] or "").split()):
                    await db.execute("DELETE FROM restraint_mutes WHERE id = ? AND user_id = ?", (r[0], uid))
                    n += 1
        _mute_cache.pop(uid, None)
        return n
    except Exception as exc:  # noqa: BLE001
        logger.debug("restraint: entity erase skipped: %r", exc)
        return 0


async def erase_user(user_id: str) -> int:
    """Right-to-be-forgotten: every restraint row of the member goes (mutes and thread classes)."""
    uid = (user_id or "").strip()
    if not uid:
        return 0
    from db_compat import get_compat_db

    n = 0
    async with get_compat_db() as db:
        for table in ("restraint_mutes", "restraint_classes"):
            cur = await db.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
            n += int(getattr(cur, "rowcount", 0) or 0)
    _mute_cache.pop(uid, None)
    _focus.pop(uid, None)
    _marks.pop(uid, None)
    return n


# ── 1b. thread classes: stored, invalidate-never-delete (migration 0042) ─────
def thread_classes(source_ref: str, text: Any, kind: str, stored: dict, names: Iterable[str] = ()) -> tuple[str, ...]:
    """Classes of a candidate / loop / moment: the stored row while its version and text hash match, else
    a fresh classification. Pure; ``stored`` is ``load_thread_classes``'s map."""
    hit = stored.get(source_ref)
    if hit and hit[0] == VERSION and hit[1] == text_hash(text):
        return _parse_csv(hit[2])
    return classify(text, kind=kind, names=names)


async def load_thread_classes(db, user_id: str) -> dict:
    """``{subject_ref: (version, text_hash, classes_csv)}`` of the member's VALID rows. Fail-open ({})."""
    try:
        async with db.execute(
            "SELECT subject_ref, version, text_hash, classes FROM restraint_classes "
            "WHERE user_id = ? AND invalid_at IS NULL", (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        return {str(r[0]): (int(r[1]), str(r[2]), str(r[3] or "")) for r in rows}
    except Exception as exc:  # noqa: BLE001
        logger.debug("restraint: thread classes read failed: %r", exc)
        return {}


def _txn(db):
    """The connection's transaction block (asyncpg via the pool wrapper); a no-op context on a
    connection without one (the SQLite test double commits per statement)."""
    t = getattr(db, "transaction", None)
    return t() if callable(t) else contextlib.nullcontext()


async def people_names(db, user_id: str) -> list[str]:
    """The member's contacts' names (the ``other_member`` class names a person the row talks about);
    [] when the table is unreadable."""
    try:
        async with db.execute(
            "SELECT name FROM people WHERE user_id = ? AND (deleted = 0 OR deleted IS NULL)", (user_id,),
        ) as cur:
            return [str(r[0]) for r in await cur.fetchall() if r[0]]
    except Exception:  # noqa: BLE001
        return []


async def store_thread_classes(db, user_id: str, items: Iterable[tuple[str, str, str]],
                               names: Iterable[str] = (), now: Optional[datetime] = None) -> int:
    """Store the class of each ``(source_ref, text, kind)``. A changed text or classifier version
    INVALIDATES the earlier row (``invalid_at``) and inserts a new one; nothing is deleted or rewritten.
    Never raises; returns the rows written."""
    if mode() == "off":
        return 0
    stamp_ = _iso(now or datetime.now(timezone.utc))
    written = 0
    try:
        valid = await load_thread_classes(db, user_id)
        names = list(names)
        for ref, text, kind in items:
            h = text_hash(text)
            cur_ = valid.get(ref)
            # classified BEFORE the skip: the contact names are an input too, so a thread stored before
            # a contact was added is re-derived (same text, new class) rather than kept stale
            classes, signals = classify_full(text, kind=kind, names=names)
            if cur_ and cur_[0] == VERSION and cur_[1] == h and _parse_csv(cur_[2]) == classes:
                continue
            # invalidate + insert are ONE transaction: a failed insert must not leave the thread with
            # no valid saved class (db-safety: multi-step writes are transactional)
            async with _txn(db):
                await db.execute(
                    "UPDATE restraint_classes SET invalid_at = ? WHERE user_id = ? AND subject_ref = ? "
                    "AND invalid_at IS NULL", (stamp_, user_id, ref))
                await db.execute(
                    """INSERT INTO restraint_classes (id, user_id, subject_ref, classes, signals, version,
                           text_hash, derived_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (user_id, subject_ref, version, text_hash)
                       DO UPDATE SET invalid_at = NULL, classes = excluded.classes,
                           signals = excluded.signals, derived_at = excluded.derived_at""",
                    (uuid.uuid4().hex, user_id, ref, ",".join(classes), ",".join(signals), VERSION, h, stamp_))
            written += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("restraint: thread class store failed user=%s: %r", user_id, exc)
    return written


# ── 4. back-off from the delivery ledger ─────────────────────────────────────
BACKOFF_CAP_LEVEL = 5          # x32 on the base gap
BACKOFF_MAX_S = 7 * 86400
PAUSE_AFTER = 3                # unanswered raises in a row
PAUSE_S = 72 * 3600


def backoff_level(outcomes: list[tuple[str, str]], kind: str) -> int:
    """Consecutive ``ignored`` raises of ``kind``, newest first, from ledger ``(kind, outcome)`` pairs.
    ``unknown`` and ``undelivered`` say nothing about the person (it may never have been heard) and are
    skipped; an ``accepted`` ends the run. Pure."""
    n = 0
    for k, outcome in outcomes:
        if k != kind or outcome in ("unknown", "undelivered"):
            continue
        if outcome == "ignored":
            n += 1
        else:
            break
    return min(n, BACKOFF_CAP_LEVEL)


def unanswered_run(outcomes: list[tuple[str, str]]) -> int:
    """Consecutive ``ignored`` raises of ANY kind, newest first. Pure."""
    n = 0
    for _k, outcome in outcomes:
        if outcome in ("unknown", "undelivered"):
            continue
        if outcome == "ignored":
            n += 1
        else:
            break
    return n


def required_gap_s(base_s: int, level: int) -> int:
    return min(max(int(base_s), 1) * (2 ** max(0, level)), BACKOFF_MAX_S)


def backoff_decision(led: list[tuple[str, str, str]], kind: str, rows: list[tuple], now: datetime,
                     base_gap_s: int = 7200) -> tuple[str, int]:
    """``(why, level)`` from the ledger rows ``(kind, outcome, surfaced_at)`` newest first and the
    member's candidate rows (``kind`` at 1, ``last_surfaced_at`` at 11). Pure."""
    pairs = [(k, o) for k, o, _ in led]
    if unanswered_run(pairs) >= PAUSE_AFTER:
        last_ignored = next((s for _k, o, s in led if o == "ignored"), "")
        if last_ignored and _ts(last_ignored) > _ts(_iso(now - timedelta(seconds=PAUSE_S))):
            return "paused", backoff_level(pairs, kind)
    level = backoff_level(pairs, kind)
    if level:
        last_raise = max((_ts(r[11]) for r in rows if str(r[1]) == kind and len(r) > 11 and r[11]), default="")
        if last_raise and last_raise > _ts(_iso(now - timedelta(seconds=required_gap_s(base_gap_s, level)))):
            return "backoff", level
    return "", level


async def backoff_why(user_id: str, kind: str, rows: list[tuple], now: datetime, *, base_gap_s: int = 7200) -> str:
    """Why the next raise of ``kind`` must wait on the ledger's evidence: ``"backoff"`` (the doubled wait
    since the last raise of that kind has not elapsed), ``"paused"`` (three unanswered raises in a row),
    or ``""``. ``shadow`` logs the would-defer and returns ``""``. Needs ``ZOE_PROACTIVE_LEDGER`` rows;
    with the ledger dark there is no evidence and no back-off. Fail-open."""
    m = mode()
    if m == "off":
        return ""
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            async with db.execute(
                "SELECT kind, outcome, surfaced_at FROM proactive_deliveries WHERE user_id = ? "
                "AND outcome IS NOT NULL ORDER BY surfaced_at DESC LIMIT 12", (user_id,),
            ) as cur:
                led = [(str(r[0]), str(r[1]), str(r[2] or "")) for r in await cur.fetchall()]
        why, level = backoff_decision(led, kind, rows, now, base_gap_s)
        if why:
            _note(user_id, "raise_spacing", 1, {why: 1}, {}, m == "enforce")
            logger.info("RESTRAINT_BACKOFF user=%s kind=%s level=%d why=%s mode=%s", user_id, kind, level, why, m)
        return why if m == "enforce" else ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("restraint: back-off read failed (no back-off): %r", exc)
        return ""


def _reset_state() -> None:
    """Clear in-process state (tests; simulates a restart)."""
    _marks.clear()
    _mute_cache.clear()
    _focus.clear()
    _counts.clear()
    _VERDICT.set(None)
