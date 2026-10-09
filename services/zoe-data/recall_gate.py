"""The recall relevance gate (``ZOE_RECALL_GATE`` = off | shadow | enforce, default shadow).

Blueprint 2.3 / 2.8 / 2.9 (docs/architecture/samantha-brain-blueprint-2026-10-09.md), register item BM4. The borrowed piece is the
SEMANTICS of SillyTavern World Info (entity-triggered injection with ``sticky`` / ``cooldown`` / ``delay`` and a token budget); the
source is AGPL-3.0, so no code is taken - this file is written from the behaviour only.

The class it replaces. Which durable rows enter the brain's packet was decided by a floor (the ranked read + one similarity search +
the hop's English topic regexes), so a fact the owner stated reached the brain only when one of those happened to match: the
day-sim S9b miss (a 6am dog walk that the hop's word list did not recognise as a constraint). The gate decides, per turn, WHICH
durable rows enter and in WHAT ORDER, from several signals of which the existing similarity recall is only one:

1. **Triggers derived from the rows themselves** (``derive`` / ``stamp``, written beside the row exactly like restraint's class and
   re-derived while the stored copy's version or text hash no longer matches): names (capitalised words), distinctive content
   keys, clock times and a routine flag (a habit cue AND a time of day). Matched against the owner's words.
2. **Similarity** (the floor's own ``hits``) - ranked, one signal among the others.
3. **Per-row timing** (``Config``): ``sticky`` - a row the owner's words triggered stays eligible for N more turns of the session;
   ``cooldown`` - a row that is not freshly triggered is not re-served for M turns after it was served; ``delay`` - a row nothing
   has triggered (the floor's generic filler) is not eligible in the first K turns of a session ("do not front-load").
4. **A token budget and a stable order**: rows are admitted by score until the budget is spent, then PRESENTED in the order they
   entered the session's selection, so while nothing changes the same rows come in the same order and a new row appends.
5. **Authority**: owner-verbatim > owner-derived > inferred. An inferred row (or an unverified speaker's) enters only by similarity
   or by a name the owner said, is never held by ``sticky``, and loses ties. Guests never; an off-record turn arms nothing and the
   gate abstains on it. The restraint classes are untouched: the gate selects AMONG rows already allowed - the floor's own rows
   were filtered upstream, and a row the gate adds from the durable pool is checked against ``restraint.decide`` as if enforcing.

Language independence by construction (blueprint 2.9, L4). Nothing here reads English. Matching is on ids, normalised keys
(NFKC, casefold, a four-letter stem key; CJK by character bigram), clock times and the rows' own structure; the per-language WORDS
(stop words, plan cues such as "tomorrow", habit cues, times of day, am/pm, capitalisation-is-a-name) are data in
``lexicons_data/<lang>.json`` under ``recall_gate``. A language with no entry gets no cue words, only key overlap weighted by how
rare the key is in the owner's own rows - it fails SAFE (fewer additions), never open. A word list may only ADD a trigger; no
list authorises anything (authority is the row's stored class).

Modes. ``off``: nothing runs. ``shadow`` (default): the packet is BYTE-IDENTICAL to the floor's (proved by
``tests/test_recall_gate.py`` over a seed set), the gate runs as a background task after the packet is built (it adds nothing
before first audio), keeps its own session state as if it were enforcing, and writes one log line per turn and surface:
``RECALL_GATE user=<id> served=<n> would_add=<ids> would_drop=<ids> budget=<tokens> ...`` (8-character row ids, counts, never text).
``enforce``: the gate's selection replaces the floor's. Every entry point is fail-open to the floor.

State is in memory (a restart is a new session), keyed by (user, session), bounded, and never crosses users.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_RECALL_GATE"
VERSION = 1                      # bump it and every stamped trigger set is invalid (re-derived, never deleted)
_OFF = frozenset({"0", "false", "no", "off", "disabled"})
_ENFORCE = frozenset({"enforce", "1", "true", "yes", "on"})


def mode() -> str:
    """``off`` | ``shadow`` (default: unset, ``shadow`` or an unrecognised value) | ``enforce``. Per-call read."""
    from typed_env import env_str

    raw = env_str("ZOE_RECALL_GATE", "shadow").lower()
    if raw in _OFF:
        return "off"
    if raw in _ENFORCE:
        return "enforce"
    return "shadow"


# ── configuration ────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Config:
    threshold: float = 2.0     # a row whose signals sum to this or more is TRIGGERED
    prior: float = 1.0         # the floor's generic filler: below the threshold, eligible only after ``delay``
    sticky: int = 2            # a triggered row stays eligible for this many further turns
    cooldown: int = 3          # turns a not-freshly-triggered row waits after it was served
    delay: int = 2             # turns of a session in which filler is not eligible
    filler_max: int = 2        # at most this many filler rows in one selection (0 = pure abstain)
    budget: int = 400          # tokens (blueprint 2.3: the recall block is ~400 tokens / 1,600 chars)
    max_rows: int = 12         # the packet's bullet cap
    incumbent: float = 0.25    # a row in the previous selection wins a near-tie (a stable set at the budget edge)
    abstain: bool = True       # enforce: nothing triggered and no filler eligible -> an EMPTY packet (False: the floor's packet stands)


PACKET = Config()
HOP = replace(PACKET, max_rows=2, budget=120, filler_max=0)

#: ordering bonus by authority rank (verbatim 4+, derived 3, anything else). Ordering only: it never lifts a row over the threshold.
AUTH_BONUS = {4: 0.30, 3: 0.15}
_W_NAME, _W_WEAK, _W_RARE, _W_MID, _W_COMMON = 2.5, 2.0, 1.5, 0.8, 0.3
_W_CLOCK, _W_PLAN, _W_SIM, _W_SIM_MID, _W_TOPIC, _LEX_CAP = 2.0, 1.2, 2.0, 1.0, 2.5, 4.0
#: the similarity floor, on the embedding DISTANCE the floor's search leaves in ``MemoryRef.score`` (lower = nearer; Chroma's default
#: MiniLM, squared L2). Measured on the replay fixtures (scripts/perf/recall_gate_replay.py): the row that answers the ask sits at
#: 0.46-0.69, an irrelevant row at 0.70-1.05. A hit is NEAR under SIM_NEAR (or the nearest hit of the ask under SIM_BEST), MID under
#: SIM_MID (a supporting signal only), and otherwise just a neighbour: the search returns its k nearest whatever they are. These
#: constants belong to the embedder; re-measure them if it changes.
SIM_NEAR, SIM_BEST, SIM_MID = 0.69, 0.80, 0.90
_SESSION_IDLE_S = 1800.0       # no session id from the caller: a gap this long is a new session
_TURN_FRESH_S = 120.0          # a noted turn is "this turn" for this long (the recall tool call within the same turn)
_POOL_TIMEOUT_S = 0.8
_MAX_SESSIONS = 128
_SESSION_TTL_S = 6 * 3600.0


# ── language data (lexicons_data/<lang>.json -> "recall_gate") ──────────────────────────────

def _fold(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold().replace("’", "'")


def _cue_rx(words: list, cjk: bool) -> Optional["re.Pattern[str]"]:
    if not words:
        return None
    body = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(body if cjk else rf"(?<!\w)(?:{body})(?!\w)")


class _Lex:
    __slots__ = ("lang", "caps_entity", "stop", "stop_chars", "am", "pm", "suffix", "months", "weekdays", "plan_rx",
                 "routine_rx", "daypart_rx")

    def __init__(self, lang: str, sec: dict, cjk: bool):
        self.lang = lang
        self.caps_entity = bool(sec.get("caps_entity"))
        self.stop = frozenset(_fold(w) for w in sec.get("stop") or [])
        self.stop_chars = frozenset(sec.get("stop_chars") or [])
        self.am = [_fold(w) for w in sec.get("am_words") or []]
        self.pm = [_fold(w) for w in sec.get("pm_words") or []]
        self.suffix = [_fold(w) for w in sec.get("clock_suffix") or []]
        self.months = frozenset(_fold(w) for w in sec.get("months") or [])
        self.weekdays = frozenset(_fold(w) for w in sec.get("weekdays") or [])
        self.plan_rx = _cue_rx([_fold(w) for w in sec.get("plan_cues") or []], cjk)
        self.routine_rx = _cue_rx([_fold(w) for w in sec.get("routine_cues") or []], cjk)
        self.daypart_rx = _cue_rx([_fold(w) for w in sec.get("dayparts") or []], cjk)


_LEX: dict = {}
_EMPTY_LEX = _Lex("", {}, False)


def lex(lang: str) -> _Lex:
    """The language's recall-gate data, or an EMPTY one (no cue words, no stop list): the gate then runs on key overlap
    weighted by rarity alone and fails safe."""
    code = (lang or "").strip().lower().split("-")[0]
    got = _LEX.get(code)
    if got is not None:
        return got
    got = _EMPTY_LEX
    try:
        import lexicons

        raw = lexicons.load(code)
        sec = raw.get("recall_gate") if raw else None
        if isinstance(sec, dict):
            got = _Lex(code, sec, bool(raw.get("cjk")) or code in ("zh", "ja"))
    except Exception as exc:  # noqa: BLE001 - data problems must never break a turn
        logger.debug("recall_gate: lexicon %s unavailable (%s)", code, type(exc).__name__)
    _LEX[code] = got
    return got


_LATIN = ("en", "es", "fr", "de")


def _detect(text: str) -> tuple:
    """``(language, evidence)``; evidence 0 means "no word of any Latin language lexicon was seen" (a very short text)."""
    try:
        import lexicons

        return lexicons.detect_scored(text)
    except Exception:  # noqa: BLE001
        return "en", 0


def _lexes(code: str, evidence: int) -> list:
    """The language data a text is read with: its detected language - or, when detection has no evidence (a three-word message), every
    Latin-script language's (stop words and cue words are unioned: a cue may only ADD a trigger, so a wrong guess costs nothing)."""
    if evidence > 0 or code not in _LATIN:
        return [lex(code)]
    return [lex(c) for c in _LATIN]


def _any(rxs: list, s: str) -> bool:
    return any(rx is not None and rx.search(s) for rx in rxs)


# ── tokens, keys, clock times (pure) ────────────────────────────────────────────────────────

_CJK_RUN = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯ｦ-ﾟ]+")
#: one script at a time inside a CJK run (Han | katakana | hiragana | hangul): a bigram never straddles a script boundary
_SCRIPT = re.compile("[㐀-䶿一-鿿]+|[゠-ヿｦ-ﾟ]+|[぀-ゟ]+|[가-힯]+")
_WORD = re.compile(r"[^\W\d_]+|\d+")
_NT = re.compile(r"n['’]t\b", re.IGNORECASE)
_SENT_END = ".!?\n。！？"


def _key(tok: str) -> str:
    """A stem key that survives inflection without a language model: four letters of a long word, a plural-less short one.
    ``walk / walks / walked / walking`` -> ``walk``; ``dog / dogs`` -> ``dog``; ``kelpie / kelpies`` -> ``kelp``."""
    if len(tok) >= 5:
        return tok[:4]
    if len(tok) == 4 and tok.endswith("s"):
        return tok[:3]
    return tok


@dataclass(frozen=True)
class Feat:
    """What a text carries, for matching. ``keys`` are content stem keys; ``names`` / ``weak`` the subset that looks like a proper
    name (capitalised mid-sentence / at a sentence start); ``clocks`` normalised ``@HH:MM`` times; ``routine`` a habit cue AND
    a time of day; ``plan`` a plan-ahead cue ("tomorrow"); ``lang`` the detected language."""

    keys: tuple = ()
    names: tuple = ()
    weak: tuple = ()
    clocks: tuple = ()
    routine: bool = False
    plan: bool = False
    lang: str = "en"


def _clock_times(s: str, lxs: list) -> list:
    out: list = []

    def add(h: int, m: int) -> None:
        if 0 <= h <= 23 and 0 <= m <= 59:
            tag = f"@{h:02d}:{m:02d}"
            if tag not in out:
                out.append(tag)

    for m in re.finditer(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)\s*(am|pm|a\.m\.|p\.m\.)?", s):
        h, mm, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "")
        if ap.startswith("p") and h < 12:
            h += 12
        if ap.startswith("a") and h == 12:
            h = 0
        add(h, mm)
    for lx in lxs:
        for words, pm in ((lx.am, False), (lx.pm, True)):
            for w in words:
                for m in re.finditer(rf"(?<!\d)(\d{{1,2}})\s?{re.escape(w)}(?!\w)", s):
                    h = int(m.group(1))
                    if 1 <= h <= 12:
                        add(h % 12 + (12 if pm else 0), 0)
        for w in lx.suffix:
            for m in re.finditer(rf"(?<!\d)(\d{{1,2}})\s?{re.escape(w)}(?:\s?(\d{{1,2}})|半)?", s):
                add(int(m.group(1)), int(m.group(2)) if m.group(2) else (30 if m.group(0).endswith("半") else 0))
    return out


def derive(text: Any, lang: Optional[str] = None) -> Feat:
    """Pure: the match features of ``text``. Never raises (an unreadable text is an empty ``Feat``)."""
    try:
        raw = unicodedata.normalize("NFKC", str(text or "")).replace("’", "'")
        raw = _NT.sub("", raw)
        code, ev = (lang, 1) if lang else _detect(raw)
        lxs = _lexes(code, ev)
        stop = frozenset().union(*(lx.stop for lx in lxs))
        months = frozenset().union(*(lx.months for lx in lxs))
        weekdays = frozenset().union(*(lx.weekdays for lx in lxs))
        stop_chars = frozenset().union(*(lx.stop_chars for lx in lxs))
        caps = all(lx.caps_entity for lx in lxs)      # German capitalises every noun: one language that does vetoes "capitalised = a name"
        s = raw.casefold()
        keys: list = []
        names: list = []
        weak: list = []

        def put(k: str, bucket: Optional[list] = None) -> None:
            if k not in keys:
                keys.append(k)
            if bucket is not None and k not in bucket:
                bucket.append(k)

        latin = _CJK_RUN.sub(lambda m: "·" * len(m.group(0)), raw)   # same length: "after a CJK char" is not a sentence start
        for m in _WORD.finditer(latin):
            tok = m.group(0)
            if tok.isdigit() or len(tok) < 2:
                continue
            low = tok.casefold()
            if low in stop or low in months or low in weekdays:
                continue
            bucket = None
            if caps and tok[0].isupper() and not tok.isupper():
                before = latin[: m.start()].rstrip()
                bucket = weak if (not before or before[-1] in _SENT_END) else names
            put(_key(low), bucket)
        for run in _CJK_RUN.findall(raw):          # CJK: split at the lexicon's function characters, bigrams of what is left
            parts = [run]
            if stop_chars:
                parts = [p for p in re.split("[" + re.escape("".join(sorted(stop_chars))) + "]", run) if p]
            for part in parts:
                for p in _SCRIPT.findall(part):
                    if len(p) == 1:
                        put(p)
                    else:
                        for i in range(len(p) - 1):
                            put(p[i:i + 2])
        clocks = _clock_times(s, lxs)
        has_day = bool(clocks) or _any([lx.daypart_rx for lx in lxs], s)
        routine = _any([lx.routine_rx for lx in lxs], s) and has_day
        plan = _any([lx.plan_rx for lx in lxs], s)
        return Feat(tuple(keys[:40]), tuple(names[:8]), tuple(weak[:4]), tuple(clocks[:3]), routine, plan, code)
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_gate: derive failed (%s)", type(exc).__name__)
        return Feat()


def _text_hash(text: Any) -> str:
    return hashlib.sha1(unicodedata.normalize("NFKC", str(text or "")).encode("utf-8")).hexdigest()[:12]


def stamp(md: dict, text: Any) -> None:
    """Write the row's triggers onto a NEW row's metadata (``MemoryService._build_metadata``, beside restraint's class). A no-op
    with the flag off. Never raises: a row is never lost to its own label. Stored as one short JSON string (Chroma metadata is
    scalar); a reader trusts it only while ``gate_v`` and ``gate_h`` still match (invalidate, never delete)."""
    try:
        if mode() == "off":
            return
        f = derive(text)
        md["gate_t"] = json.dumps({"k": f.keys[:24], "n": f.names, "w": f.weak, "c": f.clocks, "r": int(f.routine), "l": f.lang},
                                  ensure_ascii=False, separators=(",", ":"))
        md["gate_v"] = VERSION
        md["gate_h"] = _text_hash(text)
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_gate: stamp skipped: %r", exc)


def row_feat(meta: Optional[dict], text: Any) -> Feat:
    """The triggers of a stored row: the STAMPED set while its version and text hash match, else derived now. Pure."""
    meta = meta or {}
    try:
        if meta.get("gate_v") == VERSION and meta.get("gate_h") == _text_hash(text) and meta.get("gate_t"):
            d = json.loads(meta["gate_t"])
            return Feat(tuple(d.get("k") or ()), tuple(d.get("n") or ()), tuple(d.get("w") or ()), tuple(d.get("c") or ()),
                        bool(d.get("r")), False, str(d.get("l") or "en"))
    except Exception:  # noqa: BLE001 - a bad stamp is no stamp
        pass
    return derive(text)


# ── candidates, signals, decision ───────────────────────────────────────────────────────────

@dataclass
class Cand:
    """One row the gate may select. ``source``: ``hit`` (the floor's similarity search, ``rank`` 1-based), ``fact`` (the floor's
    ranked filler, ``rank`` its position), ``pool`` (a durable owner row the floor did not present), ``topic`` (the hop's own
    topic match, hop surface only)."""

    id: str
    text: str
    meta: dict
    source: str
    rank: int = 0
    auth: int = 0
    ref: Any = None
    topic: str = ""
    dist: float = 0.0            # a ``hit``'s embedding distance (0.0 for a row the floor pinned by rule)
    best: bool = False           # the NEAREST of the ask's hits (the floor's order blends in hotness, so rank 1 is not always nearest)


@dataclass(frozen=True)
class Pick:
    id: str
    text: str
    score: float
    auth: int
    source: str
    reasons: tuple
    tokens: int
    held: bool = False
    ref: Any = None
    topic: str = ""


@dataclass
class Decision:
    surface: str = "packet"
    turn: int = 0
    selected: tuple = ()
    added: tuple = ()
    dropped: tuple = ()
    budget_used: int = 0
    budget: int = 0
    held: int = 0
    cooled: int = 0
    delayed: int = 0
    skipped: str = ""            # "" | off_record | guest | empty | error - the gate abstained: the floor stands

    @property
    def ids(self) -> list:
        return [p.id for p in self.selected]


def est_tokens(text: str) -> int:
    """A language-neutral token estimate of one packet bullet (the text as the builder shows it, plus its cite and bullet)."""
    t = (text or "")[:200]
    cjk = sum(len(r) for r in _CJK_RUN.findall(t)) / max(1, len(t)) >= 0.3
    return int(math.ceil(len(t) * (0.8 if cjk else 0.25))) + 6


@dataclass
class _Row:
    served: int = -99
    sticky_until: int = -99


@dataclass
class Session:
    turn: int = 0
    last_ts: float = 0.0
    rows: dict = field(default_factory=dict)          # id -> _Row
    admit: dict = field(default_factory=dict)         # id -> admission sequence (the PRESENTATION order)
    sel_by_turn: dict = field(default_factory=dict)   # turn -> {surface: tuple[id]}
    seq: int = 0
    cache: dict = field(default_factory=dict)         # (surface, turn, extra hash) -> Decision


_SESSIONS: "OrderedDict[tuple, Session]" = OrderedDict()
_CUR: dict = {}          # (uid, session id) -> (session key, message hash, owner words, noted at, noted by the seam?) - ONE per
#                          conversation: a user's web turn and voice turn never share an entry
_SESSION_CTX: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("recall_gate_session", default=None)
_COUNTS: dict = {}
_PENDING: set = set()


def _reset_state() -> None:
    _SESSIONS.clear()
    _CUR.clear()
    _SESSION_CTX.set(None)
    _COUNTS.clear()
    _PENDING.clear()
    _LEX.clear()


def counts() -> dict:
    """``{(surface, kind): n}`` since the process started: ``turns``, ``added``, ``dropped`` (tests and probes)."""
    return dict(_COUNTS)


def _session(key: tuple, now: float) -> Session:
    s = _SESSIONS.get(key)
    if s is None:
        s = _SESSIONS[key] = Session()
    _SESSIONS.move_to_end(key)
    s.last_ts = now
    while len(_SESSIONS) > _MAX_SESSIONS:
        _SESSIONS.popitem(last=False)
    for k in [k for k, v in _SESSIONS.items() if now - v.last_ts > _SESSION_TTL_S]:
        _SESSIONS.pop(k, None)
    return s


def _advance(uid: str, sid: str, message: str, now: float, *, noted: bool) -> Session:
    key = (uid, sid)
    if not sid:
        old = _SESSIONS.get(key)
        if old is not None and now - old.last_ts > _SESSION_IDLE_S:
            _SESSIONS.pop(key, None)
    sess = _session(key, now)
    h = _text_hash(message)
    cur = _CUR.get(key)
    same = cur is not None and cur[1] == h and now - cur[3] <= _TURN_FRESH_S
    if not same:
        sess.turn += 1
    _CUR[key] = (key, h, message, now, noted)
    if len(_CUR) > 4 * _MAX_SESSIONS:
        for k in [k for k, v in _CUR.items() if now - v[3] > _SESSION_TTL_S]:
            _CUR.pop(k, None)
    return sess


def note_turn(user_id: str, session_id: Optional[str], message: Any, *, now: Optional[float] = None) -> None:
    """The owner's turn begins (the Flue seam, once per turn, before any block is built). Advances the session's turn counter - once
    per distinct message - and remembers the owner's own words, so a later ``recall_memory`` tool call in the same turn (which carries
    only the model's query) is still judged against what the owner said. No-op when off; never raises."""
    try:
        uid = (user_id or "").strip()
        if not uid or mode() == "off":
            return
        sid = (session_id or "").strip()
        _advance(uid, sid, str(message or "")[:512], time.monotonic() if now is None else now, noted=True)
        _SESSION_CTX.set(sid)      # task-local: the in-process recall / hop calls of THIS turn find their own session
    except Exception:  # noqa: BLE001
        return


def current_session() -> Optional[str]:
    """The session id the turn running in THIS task noted (``""`` = a turn noted with no session id), or None when this task noted
    none. Task-local (a ``ContextVar``), never one value per user: two conversations of one user do not see each other."""
    return _SESSION_CTX.get()


def _fresh_noted(key: tuple, now: float) -> bool:
    cur = _CUR.get(key)
    return cur is not None and bool(cur[4]) and now - cur[3] <= _TURN_FRESH_S and cur[0] in _SESSIONS


def _turn_context(uid: str, sid: Optional[str], message: str, now: float) -> Optional[tuple]:
    """``(session, turn, owner_words, extra)`` for the conversation ``(uid, sid)`` - or None when the conversation cannot be told.

    * ``sid`` given (the in-process caller knows its session: ``current_session()`` or an explicit argument): THAT conversation's
      fresh noted turn wins; with none, ``message`` IS the turn (a caller that never notes - each new message is a new turn).
    * ``sid`` None (the sidecar's ``recall_memory`` HTTP call carries only the user): the user's one fresh noted turn is the turn; with
      none, ``message`` is; with SEVERAL concurrent conversations it is ambiguous and the answer is None - the caller skips and the
      floor stands, rather than steering one conversation by another's words and counters.

    ``extra`` is the caller's own text when it differs from the owner's words (a tool query): an additional trigger source, same turn."""
    if sid is None:
        live = [k for k in _CUR if k[0] == uid and _fresh_noted(k, now)]
        if len(live) > 1:
            return None
        sid = live[0][1] if live else ""
    key = (uid, sid)
    if _fresh_noted(key, now):
        cur = _CUR[key]
        sess = _session(cur[0], now)
        return sess, sess.turn, cur[2], (message if _text_hash(message) != cur[1] else "")
    sess = _advance(uid, sid, message[:512], now, noted=False)
    return sess, sess.turn, message, ""


def _auth_rank(meta: dict, text: str) -> int:
    try:
        import memory_authority

        return int(memory_authority.row_rank(meta, text))
    except Exception:  # noqa: BLE001 - an unclassifiable row is not provably the owner's
        return 0


def _score(c: Cand, feat: Feat, msg: Feat, msg_keys: frozenset, df: dict, cfg: Config) -> tuple:
    """``(score, reasons)``: the sum of the signals the owner's words (and the floor's similarity search) raise for one row."""
    score, why, strong = 0.0, [], False
    if c.source == "hit":
        if c.dist <= SIM_NEAR or (c.best and c.dist <= SIM_BEST):
            score += _W_SIM + 1.0 / max(1, c.rank)
            why.append("sim")
            strong = True
        elif c.dist <= SIM_MID:
            score += _W_SIM_MID
            why.append("sim~")
    if c.source == "topic":
        score += _W_TOPIC
        why.append("topic")
    lexical = 0.0
    for k in feat.keys:
        if k not in msg_keys:
            continue
        d = df.get(k, 1)
        if k in feat.names:
            lexical += _W_NAME
            strong = True
            why.append("name")
        elif k in feat.weak and d <= 2:
            lexical += _W_WEAK
            why.append("name?")
        elif d <= 2:
            lexical += _W_RARE
            why.append("key")
        elif d <= 6:
            lexical += _W_MID
        else:
            lexical += _W_COMMON
    score += min(lexical, _LEX_CAP)
    if feat.clocks and set(feat.clocks) & set(msg.clocks):
        score += _W_CLOCK
        why.append("time")
    if feat.routine and msg.plan:
        score += _W_PLAN
        why.append("plan")
    if c.auth < 3 and not strong and score >= cfg.threshold:
        # an inferred (or unverified-speaker) row enters on similarity or a name the owner said, never on key overlap alone
        score, why = cfg.threshold - 0.01, why + ["auth"]
    return score, tuple(dict.fromkeys(why))


def _added(meta: dict) -> float:
    try:
        return float((meta or {}).get("added_ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def decide(sess: Session, turn: int, cands: list, owner_words: str, *, extra: str = "", cfg: Optional[Config] = None,
           surface: str = "packet", presented: Iterable = (), commit: bool = True) -> Decision:
    """The one rule. Pure but for ``sess`` (committed at the end unless ``commit`` is False): which candidate rows enter this turn's
    packet, in what order. See the module docstring for the semantics of sticky / cooldown / delay / budget / order / authority."""
    cfg = cfg or (HOP if surface == "hop" else PACKET)       # read at call time: a probe may replace the module's Config
    msg = derive(owner_words + (" " + extra if extra else ""))
    msg_keys = frozenset(msg.keys)
    df: dict = {}
    feats: dict = {}
    for c in cands:
        f = row_feat(c.meta, c.text)
        feats[c.id] = f
        for k in set(f.keys):
            df[k] = df.get(k, 0) + 1
    prev = {i for ids in sess.sel_by_turn.get(turn - 1, {}).values() for i in ids}     # last turn's selection, any surface
    live: list = []
    held_n = cooled_n = delayed_n = 0
    for c in cands:
        st = sess.rows.get(c.id) or _Row()
        score, why = _score(c, feats[c.id], msg, msg_keys, df, cfg)
        held = False
        if score < cfg.threshold and c.auth >= 3 and st.sticky_until >= turn:
            held, score, why = True, cfg.threshold - 0.005, ("sticky",)
            held_n += 1
        elif score < cfg.threshold:
            if c.source != "fact":
                continue
            if turn <= cfg.delay:
                delayed_n += 1
                continue
            if turn - st.served <= cfg.cooldown:
                cooled_n += 1
                continue
            score, why = cfg.prior - 0.0001 * c.rank, ("filler",)
        order = score + AUTH_BONUS.get(min(c.auth, 4), 0.0) + (cfg.incumbent if c.id in prev else 0.0)
        live.append((order, c, Pick(c.id, c.text, round(score, 4), c.auth, c.source, why, est_tokens(c.text), held, c.ref, c.topic)))
    live.sort(key=lambda t: (-t[0], -t[1].auth, -_added(t[1].meta), t[1].id))
    chosen: list = []
    used = filler = 0
    for _o, _c, pk in live:
        if len(chosen) >= cfg.max_rows:
            break
        if "filler" in pk.reasons and filler >= cfg.filler_max:
            continue
        if used + pk.tokens > cfg.budget and chosen:
            continue
        chosen.append(pk)
        used += pk.tokens
        filler += "filler" in pk.reasons
    # presentation order = admission order: a row keeps its place while it stays selected; new rows append (best first)
    admit = {i: s for i, s in sess.admit.items() if i in prev}
    seq = sess.seq
    for pk in chosen:
        if pk.id not in admit:
            seq += 1
            admit[pk.id] = seq
    ordered = tuple(sorted(chosen, key=lambda p: admit[p.id]))
    shown = list(presented)
    sel_ids = [p.id for p in ordered]
    dec = Decision(surface, turn, ordered, tuple(i for i in sel_ids if i not in set(shown)),
                   tuple(i for i in shown if i not in set(sel_ids)), used, cfg.budget, held_n, cooled_n, delayed_n)
    if commit:
        for _o, c, pk in live:
            r = sess.rows.setdefault(c.id, _Row())
            if pk.score >= cfg.threshold and not pk.held and c.auth >= 3:
                r.sticky_until = turn + cfg.sticky           # a fresh trigger (re)opens the sticky window
        for pk in ordered:
            sess.rows.setdefault(pk.id, _Row()).served = turn
        sess.admit, sess.seq = admit, seq
        sess.sel_by_turn.setdefault(turn, {})[surface] = tuple(sel_ids)
        for old in [t for t in sess.sel_by_turn if t < turn - 2]:
            sess.sel_by_turn.pop(old, None)
        for rid in [r for r, s in sess.rows.items() if turn - max(s.served, s.sticky_until) > 50]:
            sess.rows.pop(rid, None)
    return dec


# ── the log line, the counters ──────────────────────────────────────────────────────────────

def short_id(rid: Any) -> str:
    """The id as logged: the first 8 characters of its last ``_`` segment (``zoe_<user>_<hash>`` ids share their first characters, so a
    plain prefix would not tell two rows apart); an id with no ``_`` is cut at 8 as is."""
    s = str(rid)
    return (s.rsplit("_", 1)[-1] if "_" in s else s)[:8]


def _ids(ids: Iterable) -> str:
    return ",".join(short_id(i) for i in ids) or "-"


def _log(user_id: str, d: Decision, m: str) -> None:
    _COUNTS[(d.surface, "turns")] = _COUNTS.get((d.surface, "turns"), 0) + 1
    _COUNTS[(d.surface, "added")] = _COUNTS.get((d.surface, "added"), 0) + len(d.added)
    _COUNTS[(d.surface, "dropped")] = _COUNTS.get((d.surface, "dropped"), 0) + len(d.dropped)
    logger.info("RECALL_GATE user=%s served=%d would_add=%s would_drop=%s budget=%d surface=%s turn=%d mode=%s cap=%d "
                "held=%d cooled=%d delayed=%d skipped=%s", user_id, len(d.selected), _ids(d.added), _ids(d.dropped),
                d.budget_used, d.surface, d.turn, m, d.budget, d.held, d.cooled, d.delayed, d.skipped or "-")


# ── the surfaces ────────────────────────────────────────────────────────────────────────────

def _skip(surface: str, why: str, turn: int = 0) -> Decision:
    return Decision(surface=surface, turn=turn, skipped=why)


async def _restraint_allowed(user_id: str, owner_words: str, refs: list, mood: bool) -> set:
    """Ids of ``refs`` that ``restraint`` would let into a packet if it were enforcing (the gate adds from the durable pool only
    rows that pass). Fails CLOSED: an unreadable restraint state allows no pool row."""
    if not refs:
        return set()
    try:
        import restraint

        turn = restraint.turn_for(user_id, owner_words, mood=mood)
        mutes = await restraint.list_mutes(user_id)
        out = set()
        for r in refs:
            meta, text = (getattr(r, "metadata", None) or {}), str(getattr(r, "text", "") or "")
            rid = str(getattr(r, "id", ""))
            if restraint.decide(text, restraint.row_classes(meta, text), turn, mutes, surface="packet",
                                source_ref=f"memory:{rid}").allow:
                out.add(rid)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_gate: restraint check failed, pool rows withheld (%s)", type(exc).__name__)
        return set()


def _is_guest(user_id: str) -> bool:
    try:
        from memory_service import is_guest_memory_user

        return is_guest_memory_user(user_id)
    except Exception:  # noqa: BLE001
        return not (user_id or "").strip()


def _off_record(user_id: str, text: str) -> bool:
    try:
        import memory_provenance

        return bool(memory_provenance.is_off_record(user_id, text))
    except Exception:  # noqa: BLE001
        return False


def _cand(ref: Any, source: str, rank: int = 0) -> Cand:
    meta = getattr(ref, "metadata", None) or {}
    text = str(getattr(ref, "text", "") or "")
    try:
        dist = float(getattr(ref, "score", 0.0) or 0.0)
    except (TypeError, ValueError):
        dist = 0.0
    return Cand(str(ref.id), text, meta, source, rank, _auth_rank(meta, text), ref, dist=dist)


def _forget_old(sess: Session, turn: int) -> None:
    for old in [k for k in sess.cache if k[1] < turn]:
        sess.cache.pop(old, None)


async def evaluate_packet(svc: Any, user_id: str, message: str, *, facts: list, hits: list, recent: Optional[list],
                          presented: list, mood: bool, now: Optional[float] = None, cfg: Optional[Config] = None,
                          session_id: Optional[str] = None) -> Decision:
    """The gate's decision for the recall packet of the conversation ``(user_id, session_id)`` (None: see ``_turn_context``). Never
    raises (an error is a ``skipped`` decision: the floor stands)."""
    try:
        t = time.monotonic() if now is None else now
        if _is_guest(user_id):
            return _skip("packet", "guest")
        if not (message or "").strip():
            return _skip("packet", "empty")
        ctx = _turn_context(user_id, session_id, message, t)
        if ctx is None:
            return _skip("packet", "ambiguous_session")
        sess, turn, owner, extra = ctx
        if _off_record(user_id, owner):
            return _skip("packet", "off_record", turn)
        key = ("packet", turn, _text_hash(extra))
        if key in sess.cache:
            return sess.cache[key]
        cands: dict = {}
        for rank, ref in enumerate(hits, 1):
            cands.setdefault(str(ref.id), _cand(ref, "hit", rank))
        nearest = min((c for c in cands.values() if c.source == "hit"), key=lambda c: c.dist, default=None)
        if nearest is not None:
            nearest.best = True
        for rank, ref in enumerate(facts, 1):
            cands.setdefault(str(ref.id), _cand(ref, "fact", rank))
        for ref in recent or ():
            cands.setdefault(str(ref.id), _cand(ref, "fact", 0))
        try:
            pool = await asyncio.wait_for(svc.load_durable_for_hop(user_id), timeout=_POOL_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - no pool is the floor's own rows
            logger.debug("recall_gate: durable pool unavailable (%s)", type(exc).__name__)
            pool = []
        extra_refs = [r for r in pool if str(r.id) not in cands]
        ok = await _restraint_allowed(user_id, owner, extra_refs, mood)
        for ref in extra_refs:
            if str(ref.id) in ok:
                cands[str(ref.id)] = _cand(ref, "pool")
        dec = decide(sess, turn, list(cands.values()), owner, extra=extra, cfg=cfg, surface="packet", presented=presented)
        sess.cache[key] = dec
        _forget_old(sess, turn)
        return dec
    except Exception as exc:  # noqa: BLE001
        logger.warning("recall_gate: packet evaluation failed (floor stands): %r", exc)
        return _skip("packet", "error")


def _spawn(coro: Any) -> None:
    try:
        task = asyncio.ensure_future(coro)
    except RuntimeError:       # no running loop: nothing to schedule
        coro.close()
        return
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


async def drain() -> None:
    """Wait for the background shadow evaluations (tests and probes)."""
    while _PENDING:
        await asyncio.gather(*list(_PENDING), return_exceptions=True)


async def _shadow_packet(svc: Any, user_id: str, message: str, kw: dict) -> None:   # kw carries session_id
    try:
        _log(user_id, await evaluate_packet(svc, user_id, message, **kw), "shadow")
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_gate: shadow packet failed (%s)", type(exc).__name__)


async def packet_surface(svc: Any, user_id: str, message: str, result: dict, *, facts: list, hits: list, recent: Optional[list],
                         mood: bool, rebuild: Callable, session_id: Optional[str] = None) -> dict:
    """``routers.memories.memory_for_prompt`` calls this right after the floor built ``result``. ``off``: ``result`` untouched. ``shadow``:
    ``result`` is returned untouched at once and the decision is evaluated and logged in the background. ``enforce``: the decision is
    awaited and ``rebuild(rows)`` (the packet builder over the gate's rows, in the gate's order) replaces ``result``. ``session_id`` binds the
    decision to ONE conversation of the user (default: the session this task's turn noted). Never raises."""
    try:
        m = mode()
        if m == "off":
            return result
        presented = [str(e.get("id")) for e in (result.get("refs") or []) if e.get("id")]
        sid = session_id if isinstance(session_id, str) else current_session()      # resolved HERE: a spawned shadow task keeps it
        kw = dict(facts=list(facts), hits=list(hits), recent=list(recent) if recent else None, presented=presented, mood=mood,
                  session_id=sid)
        if m == "shadow":
            _spawn(_shadow_packet(svc, user_id, message, kw))
            return result
        dec = await evaluate_packet(svc, user_id, message, **kw)
        _log(user_id, dec, "enforce")
        if dec.skipped or (not dec.selected and not PACKET.abstain):
            return result
        rebuilt = rebuild([p.ref for p in dec.selected])
        return rebuilt if isinstance(rebuilt, dict) and "packet" in rebuilt else result
    except Exception as exc:  # noqa: BLE001
        logger.warning("recall_gate: packet surface failed (floor stands): %r", exc)
        return result


async def evaluate_hop(user_id: str, message: str, rows: list, hop_ids: list, hop_topics: dict, *,
                       now: Optional[float] = None, cfg: Optional[Config] = None, session_id: Optional[str] = None) -> Decision:
    """The gate's decision for the personalisation hop: ``rows`` are the owner's durable rows (``load_durable_for_hop``), ``hop_ids`` the
    ids the hop's own English topic match selected (a signal here - the legacy floor - not the only one)."""
    try:
        t = time.monotonic() if now is None else now
        if _is_guest(user_id):
            return _skip("hop", "guest")
        if not (message or "").strip():
            return _skip("hop", "empty")
        ctx = _turn_context(user_id, session_id, message, t)
        if ctx is None:
            return _skip("hop", "ambiguous_session")
        sess, turn, owner, extra = ctx
        if _off_record(user_id, owner):
            return _skip("hop", "off_record", turn)
        key = ("hop", turn, _text_hash(extra))
        if key in sess.cache:
            return sess.cache[key]
        topic_ids = set(hop_ids)
        ok = await _restraint_allowed(user_id, owner, [r for r in rows if str(r.id) not in topic_ids], False)
        cands: list = []
        for ref in rows:
            rid = str(ref.id)
            if rid not in topic_ids and rid not in ok:
                continue
            c = _cand(ref, "topic" if rid in topic_ids else "pool")
            c.topic = hop_topics.get(rid, "")
            cands.append(c)
        dec = decide(sess, turn, cands, owner, extra=extra, cfg=cfg, surface="hop", presented=hop_ids)
        sess.cache[key] = dec
        _forget_old(sess, turn)
        return dec
    except Exception as exc:  # noqa: BLE001
        logger.warning("recall_gate: hop evaluation failed (floor stands): %r", exc)
        return _skip("hop", "error")


async def _shadow_hop(user_id: str, message: str, rows: list, hop_ids: list, topics: dict, session_id: Optional[str]) -> None:
    try:
        _log(user_id, await evaluate_hop(user_id, message, rows, hop_ids, topics, session_id=session_id), "shadow")
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_gate: shadow hop failed (%s)", type(exc).__name__)


async def hop_surface(user_id: str, message: str, rows: list, hop: Any, session_id: Optional[str] = None) -> Any:
    """``personalisation_hop.build`` calls this with the hop it selected. ``off``/``shadow``: that same ``hop``. ``enforce``: a hop
    built from the gate's selection. Never raises."""
    try:
        m = mode()
        if m == "off":
            return hop
        hop_ids = [f.id for f in hop.facts]
        topics = {f.id: f.topic for f in hop.facts}
        sid = session_id if isinstance(session_id, str) else current_session()
        if m == "shadow":
            _spawn(_shadow_hop(user_id, message, list(rows), hop_ids, topics, sid))
            return hop
        dec = await evaluate_hop(user_id, message, rows, hop_ids, topics, session_id=sid)
        _log(user_id, dec, "enforce")
        if dec.skipped or (not dec.selected and not HOP.abstain):
            return hop
        import personalisation_hop as ph

        facts = []
        for p in dec.selected:
            shown = p.text if len(p.text) <= ph.FACT_CHARS else p.text[: ph.FACT_CHARS - 1].rstrip() + "…"
            facts.append(ph.HopFact(p.id, shown, p.topic or (hop.topics[0] if hop.topics else "gate"), int(round(p.score))))
        return ph.Hop(hop.topics, tuple(facts))
    except Exception as exc:  # noqa: BLE001
        logger.warning("recall_gate: hop surface failed (floor stands): %r", exc)
        return hop
