"""The struggle detector's SIGNALS: pure functions over two consecutive turns of one conversation (stdlib only, no I/O).

``struggle_detector.py`` owns the runtime (flag, queue, store, digest); this module only answers "did this turn show that
Zoe struggled, and how?". Every decision is deterministic and explainable from the reason codes it returns, so a wrong
event can be traced to one rule and a threshold.

A ``Turn`` is one finished exchange (the user's words + the reply + how it was served). ``detect(prev, cur)`` returns the
signals the CURRENT turn reveals. Pair signals (RE_ASK, MISHEAR, REPHRASE, CORRECTION, NEGATIVE_FEEDBACK) are about the
PREVIOUS turn - the one that failed - so their domain / path / pattern are the previous turn's; single-turn signals
(CANT_DO, FALLTHROUGH, VERBOSE, ABANDON, wake-only MISHEAR) are about the current one.

Precision over recall, by design: a struggle report full of false alarms teaches nobody anything, and Phase 3 will feed
these events to the router's self-training. Every rule therefore has an explicit "this is NOT a struggle" side:

* a changed or added SLOT (time anchor, number, place after "in"/"for"/"at", a proper noun) makes a new question, not a
  re-ask ("weather today" -> "what about tomorrow", "timer for 10 minutes" -> "for 15 minutes");
* a substituted word that does not sound like the one it replaced makes a new command ("add milk" -> "add eggs");
* a follow-up opener ("and", "what about", "also" ...) is a follow-up;
* a repeatable command ("next", "volume up", "louder" ...) is meant to be said twice;
* "no worries" / "no thanks" / "no, that's all" are not corrections, and a bare "no" answering Zoe's question is an answer;
* a long reply to an open request ("tell me a story", "explain ...") is not verbose;
* a policy refusal ("I can't share that with a guest") is the right answer, not a capability gap.

English only (the lexicons below). A turn in another language matches no cue and produces no text-based signal.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional

# ── vocabulary ───────────────────────────────────────────────────────────────

RE_ASK = "RE_ASK"
REPHRASE = "REPHRASE"
CORRECTION = "CORRECTION"
CANT_DO = "CANT_DO"
FALLTHROUGH = "FALLTHROUGH"
VERBOSE = "VERBOSE"
MISHEAR = "MISHEAR"
ABANDON = "ABANDON"
NEGATIVE_FEEDBACK = "NEGATIVE_FEEDBACK"
SIGNALS = (RE_ASK, REPHRASE, CORRECTION, CANT_DO, FALLTHROUGH, VERBOSE, MISHEAR, ABANDON, NEGATIVE_FEEDBACK)

#: the suggested class of fix (what Phase 2/3 would do about a cluster)
FIX_ROUTER_TRAINING = "router_training"      # a router example would have sent it to the right fast path
FIX_ROUTER_LATENCY = "router_latency"        # the two-stage router timed out / failed
FIX_CAPABILITY_GAP = "capability_gap"        # the right path ran but could not give the answer asked for
FIX_STT = "stt"                              # the words were misheard
FIX_VERBOSITY = "verbosity"                  # the answer was too long for the question
FIX_ANSWER_QUALITY = "answer_quality"        # the brain answered, wrongly
FIX_TYPES = (FIX_ROUTER_TRAINING, FIX_ROUTER_LATENCY, FIX_CAPABILITY_GAP, FIX_STT, FIX_VERBOSITY, FIX_ANSWER_QUALITY)

#: domains a deterministic fast path serves (a turn routed here and answered by the brain fell through)
FAST_DOMAINS = frozenset({"weather", "time", "timers", "lists", "calendar", "smart_home", "music", "reminders"})
#: domains that are not "Zoe supports this" evidence on their own (the catch-all, and the tiers' own labels)
_NON_DOMAINS = frozenset({"", "chat", "feedback", "safety", "none"})

PATH_BRAIN = "brain"
PATH_FAST = "fast"

#: the window a pair signal looks back over (seconds); the detector passes its configured value
DEFAULT_WINDOW_S = 45.0
#: a corrected repeat after a mishearing comes quickly
MISHEAR_WINDOW_S = 30.0
#: a repair cue answers the reply just heard
CORRECTION_WINDOW_S = 60.0
#: router similarity a FALLTHROUGH needs when the two-stage head was asked but failed (timeout / sidecar down)
FALLTHROUGH_MIN_SCORE = 0.65
#: ... and when no two-stage head runs at all (similarity alone is a weak witness: replayed data had 3 of 5 wrong below 0.8)
FALLTHROUGH_SIMILARITY_ONLY = 0.80
#: ... and the similarity score of the domain the two-stage head chose (the second witness)
FALLTHROUGH_TWO_STAGE_SIM = 0.70
#: spoken / typed reply length (chars) above which a short factual question got a speech
VERBOSE_CHARS = {"voice": 280, "chat": 700}
VERBOSE_CHARS_FAST_DOMAIN = {"voice": 200, "chat": 500}
VERBOSE_MAX_QUESTION_TOKENS = 12

_WAKE_RE = re.compile(
    r"^\s*(?:(?:a|ah|uh|um+|hey|hi|hello|ok|okay|oi)[\s,.!]+)?(?:zoe|zoey|zoie|zowie|zoë)\b[\s,.!?:;-]*", re.IGNORECASE)
_WAKE_TAIL_RE = re.compile(r"[\s,]+(?:zoe|zoey|zoie|zoë)[\s.!?]*$", re.IGNORECASE)
_FILLER_ONLY = frozenset({"", "a", "ah", "uh", "um", "umm", "hey", "hi", "hello", "ok", "okay", "oi", "hmm", "mm"})

_CONTRACTIONS = (
    (r"\bwhat's\b", "what is"), (r"\bit's\b", "it is"), (r"\bthat's\b", "that is"), (r"\bthere's\b", "there is"),
    (r"\bhow's\b", "how is"), (r"\bwhere's\b", "where is"), (r"\bwho's\b", "who is"), (r"\bhere's\b", "here is"),
    (r"\bwhen's\b", "when is"), (r"\blet's\b", "let us"), (r"\bcan't\b", "can not"), (r"\bcannot\b", "can not"),
    (r"\bwon't\b", "will not"), (r"\bain't\b", "is not"), (r"n't\b", " not"), (r"'re\b", " are"), (r"'ll\b", " will"),
    (r"'ve\b", " have"), (r"'d\b", " would"), (r"\bi'm\b", "i am"), (r"\bgonna\b", "going to"),
    (r"\bwanna\b", "want to"), (r"\bgotta\b", "got to"), (r"\bwhats\b", "what is"), (r"\bthats\b", "that is"),
)
_CONTRACTION_RES = tuple((re.compile(p), r) for p, r in _CONTRACTIONS)

_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15",
    "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "ninety": "90", "hundred": "100",
}

_STOP = frozenset("""
a an the is are was were be been being am to of in on at for by with from it its this that these those what how do does did
i me my mine you your can could would will should shall please just like going get got there here and or so uh um oh hey ok
okay tell know let us we our some any very really thing things want wanted need is not no yes right now then be have has had
out one if as when where who why which
""".split()) - {"now", "not", "no"}
_POLITE = frozenset({"please", "sorry", "excuse", "pardon", "hey", "hi", "hello", "well", "right", "oh", "so", "now"})
_FILLERS = frozenset({"ah", "uh", "um", "umm", "er", "erm", "hmm", "mm"})
#: words whose swap makes a DIFFERENT command ("turn the light off" -> "on"): treated as slots, never as noise
_POLARITY = frozenset("""
on off up down open close start stop lock unlock more less next previous louder quieter higher lower brighter dimmer
""".split())
#: words ending in "s" that are not plurals (folding them would break a phrase match: "does" -> "doe")
_NO_FOLD = frozenset({"always", "perhaps", "across", "unless", "series", "species", "news", "minus", "towards", "afterwards",
                      "besides", "whereas", "christmas", "sometimes", "nevertheless", "itunes", "yours", "ours", "theirs"})
_PREPOSITIONAL_POLARITY = frozenset({"on", "off", "up", "down", "next", "more", "less"})

_ANCHORS = frozenset("""
today tonight tomorrow yesterday now later morning afternoon evening night weekend week month year monday tuesday wednesday
thursday friday saturday sunday currently
""".split())
_FUTURE = frozenset({"will", "later", "tomorrow", "tonight", "forecast", "high", "low", "max", "min", "expected", "expect"})
_NOW = frozenset({"now", "currently", "current", "moment"})
_WH = ("what", "how", "when", "where", "who", "why", "which", "is", "are", "can", "could", "do", "does", "did", "will",
       "would", "should", "am")
_CMD_VERBS = frozenset("""
set add play turn remind start stop show call put create cancel delete remove open close pause resume skip switch dim
make book send read find check change move lock unlock
""".split())
_SLOT_PREPS = frozenset({"in", "at", "near", "for", "from", "called", "named"})   # not "to": "going to get" is a verb

_FOLLOWUP_OPENERS = re.compile(
    r"^(?:and|also|plus|then|so|or|but|what about|how about|what else|anything else|and what about|ok so|okay so|"
    r"now|next|another|one more|same for|same thing for)\b")
_FOLLOWUP_TAIL = re.compile(r"\b(?:too|as well|also)$")

_REPEATABLE = re.compile(
    r"^(?:(?:turn|crank) (?:it|the volume|the music|that) (?:up|down)(?: a bit| more)?|volume (?:up|down)|louder|"
    r"quieter|softer|(?:a bit |a little )?(?:louder|quieter)|next(?: song| track| one)?|skip(?: it| this| song| track"
    r"| this song)?|previous(?: song| track)?|go back|pause|resume|play|stop|again|more|keep going|continue|"
    r"another one|tell me another(?: one)?|one more|yes|yeah|yep|no|nope|ok|okay|thanks|thank you|cancel|"
    r"never ?mind|brighter|dimmer|up|down|hello|hi|good morning|good night)$")

_NO_LEAD = re.compile(r"^(?:no|nope|nah|no no)\b")
_NOT_A_CORRECTION = re.compile(
    r"^(?:no|nope|nah)(?: no)*\s+(?:worries|worry|problem|problems|thanks|thank you|that is (?:all|it|fine|ok|okay|good"
    r"|great|perfect|everything|enough)|that will be all|that would be all|i am (?:good|fine|ok|okay|all good|done)|"
    r"all good|i do not need|i do not want|not now|not right now|never ?mind|cancel|stop|more|i am done|that is fine|"
    r"nothing|we are good|that is ok|it is fine|it is ok|it is okay|it is all good|not yet|later|thanks zoe)\b")
_CORRECTION_CUES = re.compile(
    r"^(?:(?:no|nope|nah)(?: no)*\s+)?(?:i said|i meant|i asked (?:for|about|you)|i was asking|i wanted|"
    r"that is not what i (?:said|asked|meant|wanted)|that was not what i|not what i (?:said|asked|meant|wanted)|"
    r"you misheard|you heard|you got (?:it|that) wrong|wrong (?:one|room|light|lights|song|list|timer|answer|city|day|"
    r"time|person|thing|place)|not that|not (?:the|this) one|actually i (?:said|meant|asked|wanted)|"
    r"i did not (?:say|ask|mean|want)|that is the wrong|the other one|not (?:that|this) (?:one|list|room|song|light|timer))\b")
_NO_PLUS_CONTENT = re.compile(r"^(?:no|nope)(?: no)*\s+(?:the|my|it is|it was|in|on|at|for|to|about|i am just|i was just|"
                              r"i am saying|i was saying|i am not|i was not|that is not|i did not)\b")
#: the same repair cues, allowed at the start of the SECOND sentence too ("I don't know where you got that. I was asking ...")
_CORRECTION_CUES_ANYWHERE = re.compile(
    r"^(?:i was asking|i asked (?:for|about|you)|that is not what i (?:said|asked|meant|wanted)|"
    r"not what i (?:said|asked|meant|wanted)|you misheard|i said (?!that is)|i did not (?:say|ask))\b")
#: complaints about the exchange itself that ``conversation_feedback`` does not file as a verdict
_NEGATIVE_EXTRA = re.compile(
    r"\byou (?:are|were) not (?:getting|listening|understanding|hearing)\b|\byou (?:do|did) not (?:understand|get it|get what|"
    r"listen|hear)\b|\byou made (?:a|another) (?:bit of a |big |small )?(?:mistake|error)\b|\byou messed (?:it|that) up\b|"
    r"\bthat (?:does|did) not make (?:any )?sense\b|\bthat (?:is|was) not (?:helpful|useful)\b|\bthat was (?:useless|unhelpful)\b|"
    r"\byou (?:keep|kept) getting (?:it|that|this) wrong\b")

_CANT_DO_RE = re.compile(
    # mirrors services/zoe-data/tests/replay_samples.py::_CANT_DO_RE (the replay gate's verdict), plus the plain refusals
    r"don'?t (?:\w+\s+){0,2}have access to|can'?t access|no access to (?:your|the)|"
    r"\bnot on file\b|don'?t have (?:it|that|your \w+) on file|"
    r"can'?t (?:tell you|find|create|add|set|schedule|do that|help with that)|"
    r"couldn'?t (?:find|create|add|set|schedule)|\bunable to\b|"
    r"you'?ll have to (?:check|do).{0,30}?yourself|"
    r"\bi (?:can ?not|can'?t) (?:do|answer|check|look|play|turn|control|reach|get|see|connect|open|read|set|find|help)\b|"
    r"\bi (?:do not|don'?t) (?:know|have) (?:how|the|that|what|where|when|which|any|a way|information|enough)\b|"
    r"\bi'?m (?:not able|having trouble|unable)|\bi am (?:not able|having trouble|unable)|"
    r"\bsorry,? (?:something went wrong|i (?:couldn'?t|could not|can'?t|can ?not|didn'?t (?:catch|get|understand)))|"
    r"\bi (?:didn'?t|did not) (?:catch|understand|get) that\b|\bthat (?:isn'?t|is not) something i can\b|"
    r"\bi'?m not sure (?:i can|how to)\b",
    re.IGNORECASE)
_CANT_DO_POLICY = re.compile(
    r"\b(?:guest|sign(?:ed)? in|verify|verification|your pin|privacy|private|permission|not allowed|only (?:the|a) "
    r"(?:owner|member|admin))\b", re.IGNORECASE)
_CANT_DO_NOT = re.compile(r"\bi can'?t (?:wait|believe|imagine|stop thinking|help but)\b|"
                          r"\bi don'?t know about you\b", re.IGNORECASE)
#: the brain lane's degraded fallback (zoe_flue_client._FALLBACK_TEXT / voice_tts._FALLBACK_PHRASE): an ERROR, always
_ERROR_RE = re.compile(r"had trouble reaching my brain|having trouble (?:thinking|connecting|reaching)|"
                       r"\bsomething went wrong\b", re.IGNORECASE)

_OPEN_ENDED = re.compile(
    r"\b(?:story|stories|explain|explanation|why|describe|joke|jokes|poem|song|lyrics|recipe|how do (?:i|you|we)|"
    r"how does|how did|what do you think|talk|chat|summar\w*|everything|details|detail|more about|plan|ideas|idea|"
    r"tell me about|tell me a|help me|teach|history|compare|difference|options|steps|walk me|brainstorm|write|"
    r"list all|all of|what are some|news|update me|catch me up|brief|briefing|morning|day look|what happened)\b")


# ── normalisation ────────────────────────────────────────────────────────────

def strip_wake(text: str) -> str:
    """The utterance without a leading / trailing wake word ("A zoey. What's ..." -> "What's ...")."""
    t = (text or "").strip()
    for _ in range(3):
        new = _WAKE_RE.sub("", t, count=1)
        if new == t:
            break
        t = new
    return _WAKE_TAIL_RE.sub("", t).strip()


def normalise(text: str) -> str:
    """Lower case, wake word stripped, contractions expanded, number words to digits, plurals folded, punctuation gone."""
    t = strip_wake(text).replace("’", "'").replace("‘", "'").lower()
    for rx, rep in _CONTRACTION_RES:
        t = rx.sub(rep, t)
    t = re.sub(r"[^\w\s']", " ", t).replace("'", "")
    out = []
    for tok in t.split():
        if tok in _FILLERS:
            continue
        tok = _NUMBER_WORDS.get(tok, tok)
        if len(tok) > 4 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")) and tok not in _NO_FOLD:
            tok = tok[:-1]
        out.append(tok)
    return " ".join(out)


def tokens(text: str) -> list[str]:
    return normalise(text).split()


def content_tokens(toks: list[str]) -> set[str]:
    return {t for t in toks if t not in _STOP}


def _proper_nouns(text: str) -> set[str]:
    """Capitalised words that are not the first word of a sentence (STT capitalises names and places)."""
    out: set[str] = set()
    words = re.findall(r"[A-Za-z][\w']*|[.!?]", strip_wake(text))
    start = True
    for w in words:
        if w in ".!?":
            start = True
            continue
        if not start and w[0].isupper() and w.lower() not in ("i", "zoe", "zoey"):
            n = normalise(w)
            if n and " " not in n and n not in _STOP and n not in _POLITE:   # a contraction / function word is no name
                out.add(n)
        start = False
    return out


def slots(text: str) -> set[str]:
    """What makes two same-shaped questions DIFFERENT questions: time anchors, numbers, places / names."""
    toks = tokens(text)
    # on / off / up / down are switch words only in a command ("turn the light off"); in "what's on my list" they are not
    command = bool(toks) and toks[0] in _CMD_VERBS
    out = {t for t in toks if (t in _ANCHORS and t not in ("now", "currently")) or t.isdigit()
           or (t in _POLARITY and (command or t not in _PREPOSITIONAL_POLARITY))}
    for i, t in enumerate(toks[:-1]):
        nxt = toks[i + 1]
        if t in _SLOT_PREPS and nxt not in _STOP and nxt not in _ANCHORS and not nxt.isdigit():
            out.add(nxt)
    return out | _proper_nouns(text)


def shape(text: str) -> str:
    """A TEXT-FREE shape of the utterance from a fixed vocabulary: ``<lead>:<time orientation>:<anchor>``.

    It is what the digest clusters on and what a guest row keeps instead of words: "what is the weather going to be like
    today" -> ``what:future:today``; "set a timer for 10 minutes" -> ``cmd_set:none:none``."""
    toks = tokens(text)
    if not toks:
        return "empty:none:none"
    first = toks[0]
    if first in _WH:
        lead = first
    elif first in _CMD_VERBS:
        lead = f"cmd_{first}"
    elif first in ("tell", "give", "say"):
        lead = "ask"
    else:
        lead = "stmt"
    joined = " " + " ".join(toks) + " "
    if " going to " in joined or any(t in _FUTURE for t in toks):
        orient = "future"
    elif any(t in _NOW for t in toks):
        orient = "now"
    elif any(t in ("yesterday", "was", "were", "did") for t in toks):
        orient = "past"
    else:
        orient = "none"
    anchor = next((t for t in toks if t in _ANCHORS and t not in ("now", "currently")), "none")
    return f"{lead}:{orient}:{anchor}"


def is_question(text: str) -> bool:
    toks = tokens(text)
    return bool(toks) and (toks[0] in _WH or "?" in (text or ""))


def similarity(a: str, b: str) -> float:
    """Character similarity of the two NORMALISED utterances (0..1)."""
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def sound_alike(a: str, b: str) -> bool:
    """Could STT have heard one word as the other? Character overlap, or the same onset and a similar length."""
    if a == b:
        return True
    r = SequenceMatcher(None, a, b).ratio()
    return r >= 0.6 or (a[:1] == b[:1] and abs(len(a) - len(b)) <= 2 and r >= 0.25)


def token_diff(a: list[str], b: list[str]) -> list[tuple[str, list[str], list[str]]]:
    """The non-equal opcodes between two token lists: ``(op, a_tokens, b_tokens)``."""
    sm = SequenceMatcher(None, a, b, autojunk=False)
    return [(op, a[i1:i2], b[j1:j2]) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]


# ── the turn and the event ───────────────────────────────────────────────────

@dataclass
class Turn:
    ts: float
    channel: str = "voice"
    user_id: str = "guest"
    is_guest: bool = True
    conv_key: str = ""
    turn_id: str = ""
    session_id: str = ""
    panel_id: str = ""
    text: str = ""
    reply: str = ""
    path: str = PATH_FAST              # PATH_BRAIN when a brain lane answered
    lane: str = ""                     # the brain lane that answered (flue / core / ...)
    lane_outcome: str = ""
    predicted_domain: str = ""         # the router's domain (two-stage when it decided, else similarity)
    predicted_score: Optional[float] = None
    two_stage_domain: str = ""
    two_stage_conf: Optional[float] = None   # the two-stage head's confidence in two_stage_domain
    two_stage_active: bool = False     # ZOE_ROUTER_HEAD=active: the two-stage head was asked
    two_stage_failed: bool = False     # ... and its decision did not come back (timeout / sidecar failure)
    latency_ms: Optional[int] = None
    stt_ms: Optional[int] = None
    interrupted: bool = False

    def domain(self) -> str:
        return self.predicted_domain if self.predicted_domain not in _NON_DOMAINS else ""


@dataclass
class Event:
    signal: str
    confidence: float
    fix_type: str
    turn: Turn                         # the turn that REVEALED the struggle
    about: Turn                        # the turn that struggled (== turn for single-turn signals)
    reasons: list[str] = field(default_factory=list)
    numbers: dict[str, Any] = field(default_factory=dict)

    @property
    def pattern(self) -> str:
        return f"{self.about.domain() or 'unrouted'}|{shape(self.about.text)}"


# ── what a reply was about (a deterministic fast path's own phrasing) ────────

_REPLY_DOMAIN = (
    ("calendar", re.compile(r"^(?:here is (?:the|your) calendar|you have (?:\d+|no|one|a) events?|added .{1,60} (?:to your "
                            r"calendar|at \d)|your calendar)", re.IGNORECASE)),
    ("time", re.compile(r"^it is \d{1,2}:\d\d|^it'?s \d{1,2}:\d\d", re.IGNORECASE)),
    ("weather", re.compile(r"\bdegrees\b|^here is the forecast|\bforecast for\b", re.IGNORECASE)),
    ("smart_home", re.compile(r"^turned .{1,40} (?:on|off)\b|^(?:the )?lights? (?:is|are) (?:now )?(?:on|off)", re.IGNORECASE)),
    ("timers", re.compile(r"\btimer\b", re.IGNORECASE)),
    ("lists", re.compile(r"\blist\b|^lists? ha(?:s|ve) \d", re.IGNORECASE)),
)


def reply_domain(reply: str) -> str:
    """The fast domain a deterministic reply belongs to ("Here is the calendar." -> calendar), or ''. Pure."""
    r = (reply or "").strip()
    for dom, rx in _REPLY_DOMAIN:
        if rx.search(r[:160]):
            return dom
    return ""


# ── pair signals ─────────────────────────────────────────────────────────────

def _repeatable(norm: str) -> bool:
    return bool(_REPEATABLE.match(norm)) or len(norm.split()) <= 1


def _pair_fix(prev: Turn) -> str:
    """Why the previous turn failed, from how it was served."""
    if prev.path == PATH_BRAIN and prev.domain() in FAST_DOMAINS:
        return FIX_ROUTER_TRAINING
    if prev.path == PATH_BRAIN:
        return FIX_ANSWER_QUALITY
    return FIX_CAPABILITY_GAP


def _new_slots(prev_text: str, cur_text: str) -> set[str]:
    """Slots of ``cur`` that ``prev`` never said (as any word): a new time / number / place / name / on-off."""
    prev_toks = set(tokens(prev_text))
    return {s_ for s_ in slots(cur_text) if s_ not in prev_toks}


def _repeat_signals(prev: Turn, cur: Turn, gap: float, window_s: float) -> list[Event]:
    """RE_ASK / MISHEAR / REPHRASE - mutually exclusive, in that order of precedence."""
    np_, nc = normalise(prev.text), normalise(cur.text)
    if not np_ or not nc or gap > window_s:
        return []
    tp, tc = np_.split(), nc.split()
    sim = SequenceMatcher(None, np_, nc).ratio()
    nums: dict[str, Any] = {"gap_s": round(gap, 1), "sim": round(sim, 3)}
    # ── RE_ASK: the same request again
    if nc == np_:
        return [] if _repeatable(nc) else [Event(RE_ASK, 0.95, _pair_fix(prev), cur, prev, ["identical"], nums)]
    if _new_slots(prev.text, cur.text) or _new_slots(cur.text, prev.text):
        return []  # a new / changed time, number, place, name or on/off: a new question
    if _FOLLOWUP_OPENERS.match(nc) or _FOLLOWUP_TAIL.search(nc) or _repeatable(nc):
        return []
    cp, cc = content_tokens(tp), content_tokens(tc)
    if len(cp) >= 2 and cp == cc and sim >= 0.8:
        return [Event(RE_ASK, 0.9, _pair_fix(prev), cur, prev, ["same_content_words"], nums)]
    diff = token_diff(tp, tc)
    # ── the same request again without a garbled lead-in ("Hazily, let's talk" -> "let's talk": a misheard wake word)
    if (len(diff) == 1 and diff[0][0] == "delete" and tp[:len(diff[0][1])] == diff[0][1] and len(diff[0][1]) <= 2
            and len(tc) >= 2):
        lead = diff[0][1]
        if cur.channel == "voice" and gap <= MISHEAR_WINDOW_S and not any(t in _STOP or t in _POLITE for t in lead):
            return [Event(MISHEAR, 0.75, FIX_STT, cur, prev, ["leading_garble"], nums)]
        return [Event(RE_ASK, 0.85, _pair_fix(prev), cur, prev, ["dropped_lead_in"], nums)]
    pure_sub =bool(diff) and all(op == "replace" and len(a) == len(b) for op, a, b in diff)
    # ── MISHEAR (a corrected repeat) / a typo fix: the same frame with one or two words swapped for sound-alikes
    if pure_sub and sum(len(a) for _, a, _ in diff) <= 2 and len(tc) >= 3:
        pairs = [(x, y) for _, a, b in diff for x, y in zip(a, b)]
        if all(sound_alike(x, y) for x, y in pairs) and gap <= MISHEAR_WINDOW_S:
            if cur.channel == "voice":
                return [Event(MISHEAR, 0.8, FIX_STT, cur, prev, ["corrected_repeat"], {**nums, "swapped": len(pairs)})]
            return [Event(RE_ASK, 0.8, _pair_fix(prev), cur, prev, ["typo_fix"], {**nums, "swapped": len(pairs)})]
        return []  # a different word in the same frame: a different request ("add milk" -> "add eggs")
    # ── MISHEAR: the same content words said again in another order, one or two of them swapped for sound-alikes
    only_p = sorted((Counter(tp) - Counter(tc)).elements())
    only_c = sorted((Counter(tc) - Counter(tp)).elements())
    if (cur.channel == "voice" and gap <= MISHEAR_WINDOW_S and len(cp & cc) >= 3 and 1 <= len(only_p) == len(only_c) <= 2
            and all(any(sound_alike(x, y) for y in only_c) for x in only_p)):
        return [Event(MISHEAR, 0.75, FIX_STT, cur, prev, ["reordered_repeat"], {**nums, "swapped": len(only_p)})]
    # ── REPHRASE: the same need in other words, no new slot
    shared = (cp & cc) - _ANCHORS
    only_inserts = bool(diff) and all(op == "insert" for op, _, _ in diff) and sum(len(b) for _, _, b in diff) <= 2
    if len(cp) >= 2 and cp == cc and sim >= 0.5:
        reasons = ["same_content_words"]               # any domain: the very same words, re-said
    elif len(cp) >= 2 and cp < cc and len(cc - cp) == 1 and sim >= 0.6:
        reasons = ["refined"]                          # any domain: the same words plus one that pins it down
    else:
        dp, dc = prev.domain(), cur.domain()
        if not dp or dp != dc or len(tc) < 2 or len(tp) < 2:
            return []
        sp, sc = shape(prev.text).split(":"), shape(cur.text).split(":")
        reasons = []
        if only_inserts and content_tokens(tc) - cp:
            reasons.append("refined")
        if shared and sim >= 0.55:
            reasons.append("shared_content")
        if sp[2] != "none" and sp[2] == sc[2] and sp[0] == sc[0] and sim >= 0.55:
            reasons.append("same_lead_and_anchor")
        if sp[2] != "none" and sp[2] == sc[2] and sp[1] != "none" and sp[1] == sc[1] and sim >= 0.6:
            reasons.append("same_time_frame")
        if not reasons:
            return []
    conf = 0.7
    if prev.path == PATH_BRAIN or prev.path != cur.path:
        conf += 0.05
    if cant_do(prev.reply):
        conf += 0.1
    return [Event(REPHRASE, round(min(conf, 0.9), 2), _pair_fix(prev), cur, prev, reasons, {**nums, "shared": len(shared)})]


def _feedback_kind(text: str) -> Optional[str]:
    """``thumbs_down`` / ``correction`` (``conversation_feedback``'s own verdict) or ``complaint`` (this module's extra cues)."""
    try:
        import conversation_feedback as _cf

        hit = _cf.classify(strip_wake(text))
        if hit and hit[0] in (_cf.THUMBS_DOWN, _cf.CORRECTION):
            return hit[0]
    except Exception:  # noqa: BLE001 - a missing / broken classifier is "no verdict"
        pass
    return "complaint" if _NEGATIVE_EXTRA.search(normalise(text)) else None


def _sentences(text: str) -> list[str]:
    return [normalise(p) for p in re.split(r"(?<=[.!?])\s+", strip_wake(text)) if normalise(p)]


def correction_cue(prev_reply: str, text: str) -> Optional[str]:
    """The repair cue that opens ``text`` (``explicit`` / ``no_plus_content`` / ``second_sentence``), or None. Pure."""
    nc = normalise(text)
    if not nc or _NOT_A_CORRECTION.match(nc):
        return None
    if _CORRECTION_CUES.match(nc) and not re.match(r"^(?:no |nope )?i meant to\b", nc):
        return "explicit"
    asked = (prev_reply or "").rstrip().endswith("?")
    if not asked and _NO_PLUS_CONTENT.match(nc) and len(nc.split()) >= 3:
        return "no_plus_content"
    sents = _sentences(text)
    if len(sents) >= 2 and _CORRECTION_CUES_ANYWHERE.match(sents[1]):
        return "second_sentence"
    return None


# ── single-turn signals ──────────────────────────────────────────────────────

def cant_do(reply: str) -> Optional[str]:
    """``error`` for the brain's degraded fallback, ``cant_do`` for a capability disclaimer, else None. Pure."""
    r = (reply or "").strip()
    if not r:
        return None
    head = r[:240]
    if _ERROR_RE.search(head):
        return "error"
    if _CANT_DO_NOT.search(head) or _CANT_DO_POLICY.search(head):
        return None
    if _CANT_DO_RE.search(head):
        return "cant_do"
    return None


def verbose(cur: Turn) -> Optional[float]:
    """The reply-length ratio over the budget when a SHORT FACTUAL question got a long answer, else None."""
    nc = normalise(cur.text)
    toks = nc.split()
    if not toks or len(toks) > VERBOSE_MAX_QUESTION_TOKENS or _OPEN_ENDED.search(nc):
        return None
    if not (is_question(cur.text) or toks[0] in ("tell", "give") or cur.domain() in FAST_DOMAINS):
        return None
    ch = "voice" if cur.channel == "voice" else "chat"
    budget = (VERBOSE_CHARS_FAST_DOMAIN if cur.domain() in FAST_DOMAINS else VERBOSE_CHARS)[ch]
    n = len((cur.reply or "").strip())
    return round(n / budget, 2) if n > budget else None


def wake_only(text: str) -> bool:
    """The transcript was a wake word and nothing else ("hey zoe", "a zoey")."""
    raw = (text or "").strip()
    if not raw or not _WAKE_RE.match(raw):
        return False
    return normalise(text) in _FILLER_ONLY


def _fallthrough(cur: Turn) -> Optional[Event]:
    """A turn the router placed in a fast domain that the brain - or ANOTHER fast domain - answered."""
    ts_dom = cur.two_stage_domain if cur.two_stage_domain not in _NON_DOMAINS else ""
    score = cur.predicted_score or 0.0
    if ts_dom:
        # two witnesses must agree: the head chose a fast domain AND the similarity router scores that domain too (a domain
        # with no similarity examples - smart_home / music - scores 0.0 there, so the head alone must then be sure)
        sure = score >= FALLTHROUGH_TWO_STAGE_SIM or (score <= 0.0 and (cur.two_stage_conf or 0.0) >= 0.9)
        if not sure:
            return None
        dom, reasons, conf = ts_dom, ["two_stage_fast_domain"], 0.85
    elif cur.two_stage_failed and cur.predicted_domain in FAST_DOMAINS and score >= FALLTHROUGH_MIN_SCORE:
        dom, reasons, conf = cur.predicted_domain, ["two_stage_failed"], 0.85
    elif not cur.two_stage_active and cur.predicted_domain in FAST_DOMAINS and score >= FALLTHROUGH_SIMILARITY_ONLY:
        dom, reasons, conf = cur.predicted_domain, ["similarity_fast_domain"], 0.75
    else:
        return None
    if dom not in FAST_DOMAINS:
        return None
    if cur.path == PATH_BRAIN:
        reasons.append("brain_served")
    else:
        served = reply_domain(cur.reply)
        if not served or served == dom:
            return None
        reasons.append(f"served_{served}")
    fix = FIX_ROUTER_LATENCY if cur.two_stage_failed else FIX_ROUTER_TRAINING
    return Event(FALLTHROUGH, conf, fix, cur, cur, reasons, {"score": cur.predicted_score, "domain": dom})


# ── the detector ─────────────────────────────────────────────────────────────

def detect(prev: Optional[Turn], cur: Turn, *, window_s: float = DEFAULT_WINDOW_S) -> list[Event]:
    """Every struggle ``cur`` reveals (about itself, or about ``prev`` when ``prev`` is the previous turn of the same
    conversation). Pure and deterministic; never raises on ordinary input.

    The previous turn counts when it is the same user's in the same conversation, or - voice only - the previous turn on the
    same PANEL by another identity (the panel's identity resolution can flip between "guest" and a member mid-conversation;
    the detector then keeps no words, see ``struggle_detector``)."""
    events: list[Event] = []
    # ── single-turn: about cur
    if wake_only(cur.text):
        events.append(Event(MISHEAR, 0.7, FIX_STT, cur, cur, ["wake_only"]))
        return events
    kind = cant_do(cur.reply)
    if kind and (kind == "error" or cur.domain()):
        events.append(Event(CANT_DO, 0.9 if kind == "error" else 0.85,
                            FIX_ANSWER_QUALITY if kind == "error" else FIX_CAPABILITY_GAP, cur, cur, [kind],
                            {"reply_chars": len(cur.reply or "")}))
    fall = _fallthrough(cur)
    if fall:
        events.append(fall)
    ratio = verbose(cur)
    if ratio is not None:
        events.append(Event(VERBOSE, 0.85 if ratio >= 2 else 0.7, FIX_VERBOSITY, cur, cur, ["long_reply"],
                            {"reply_chars": len(cur.reply or ""), "over_budget": ratio}))
    if cur.interrupted and (cur.reply or "").strip():
        events.append(Event(ABANDON, 0.6, FIX_VERBOSITY if len(cur.reply) > 200 else FIX_ANSWER_QUALITY, cur, cur,
                            ["stream_ended_early"], {"heard_chars": len(cur.reply)}))
    # ── pair: about prev
    if prev is None or (prev.conv_key or cur.conv_key) and prev.conv_key != cur.conv_key:
        return events
    if prev.user_id != cur.user_id and cur.channel != "voice":
        return events
    gap = cur.ts - prev.ts
    if gap < 0:
        return events
    fb = _feedback_kind(cur.text) if gap <= CORRECTION_WINDOW_S else None
    if fb:
        events.append(Event(NEGATIVE_FEEDBACK, 0.95 if fb != "complaint" else 0.85, _pair_fix(prev), cur, prev, [fb],
                            {"gap_s": round(gap, 1)}))
        return events
    cue = correction_cue(prev.reply, cur.text) if gap <= CORRECTION_WINDOW_S and (prev.reply or "").strip() else None
    if cue:
        events.append(Event(CORRECTION, 0.9 if cue == "explicit" else 0.75, _pair_fix(prev), cur, prev, [cue],
                            {"gap_s": round(gap, 1)}))
        return events
    events.extend(_repeat_signals(prev, cur, gap, window_s))
    return events
