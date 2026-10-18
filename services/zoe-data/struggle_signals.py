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
FIX_STT_SNAPPED = "stt_snapped"              # a misheard command word was snapped back (stt_snap) - or would have been
FIX_TYPES = (FIX_ROUTER_TRAINING, FIX_ROUTER_LATENCY, FIX_CAPABILITY_GAP, FIX_STT, FIX_VERBOSITY, FIX_ANSWER_QUALITY,
             FIX_STT_SNAPPED)

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
_WAKE_TAIL_RE = re.compile(r"[\s,]+(?:zoe|zoey|zoie|zoë)$", re.IGNORECASE)   # applied to a rstripped, 12-char tail only
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
    body = t.rstrip(" \t\n.!?")
    head, tail = body[:-12], body[-12:]          # a bounded tail: no regex ever scans a long run of whitespace
    return (head + _WAKE_TAIL_RE.sub("", tail)).strip() if _WAKE_TAIL_RE.search(tail) else t.strip()


def display(text: str) -> str:
    """The words as a member's struggle row keeps them: wake word stripped, lower case, whitespace collapsed - NOT folded
    (no plural / contraction rewriting), so a forget's name pattern still finds "james" or "o'brien" in it."""
    return re.sub(r"\s+", " ", strip_wake(text).replace("’", "'").lower()).strip()


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
    started: Optional[float] = None    # when the turn began (the router's note), if known; ``ts`` is when it finished
    sim_scores: dict = field(default_factory=dict)   # the similarity router's per-domain scores (0..1)
    head_top: str = ""                 # the two-stage head's top domain (also when it gated to chat)
    sky_domain: str = ""               # the domain Skybridge classified the words as ('' = it did not claim them)
    sky_vetoed: bool = False           # ... and the router vetoed that claim
    stt_snap: Optional[dict] = None    # stt_snap's text-free note when a misheard command word was snapped (or would be)

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


#: a similarity score this high is evidence of the domain on its own (the live routers score a domain they own at 0.8+)
SIM_DOMAIN_EVIDENCE = 0.75
#: the similarity router's own routing threshold (``semantic_router.threshold``, ZOE_ROUTER_THRESHOLD default)
ROUTER_THRESHOLD = 0.62
#: a fast path's COUNT-ONLY answer ("You have 1 event today.", "Lists has 2 items.") - the follow-up "what is it?" asks for
#: what the count left out
_COUNT_ONLY_RE = re.compile(
    r"^(?:you have|there (?:is|are)|i found|lists? ha(?:s|ve)|your \w+ ha(?:s|ve))\s+(?:\d+|no|one|a|an|two|three|four|five)\s+"
    r"(?:\w+\s+){0,2}?(?:events?|items?|things?|reminders?|timers?|lists?|contacts?|people|appointments?|messages?)\b"
    r"(?:\s+(?!and\b)[\w']+){0,4}\s*[.!]?$", re.IGNORECASE)
#: a short follow-up that only points back at the answer ("what is it?", "which one?", "what are they?")
_ANAPHORIC_ASK_RE = re.compile(
    r"^(?:so |and |ok |okay |well )?(?:what|which|who|when|where)(?: 1)?(?: is| are| was| were)? "
    r"(?:it|they|them|that|those|this|these|1|ones)(?: then| exactly| again)?$|^(?:which 1|what are they|what is it)$")
#: (matched against ``normalise``d text, where "one" is "1": "which one?" -> "which 1")


def domains(t: Turn) -> set:
    """Every domain the turn carries evidence for: the head's decision, a strong similarity score, a fast reply's own
    phrasing, an un-vetoed Skybridge claim. Used to say two turns asked about the same thing."""
    out = set()
    if t.two_stage_domain not in _NON_DOMAINS:
        out.add(t.two_stage_domain)
    score = t.predicted_score or 0.0
    if t.predicted_domain not in _NON_DOMAINS and (
            score >= SIM_DOMAIN_EVIDENCE or (t.two_stage_domain in _NON_DOMAINS and score >= ROUTER_THRESHOLD)):
        out.add(t.predicted_domain)     # similarity alone routed it (the head did not decide): its own threshold applies
    for d, v in (t.sim_scores or {}).items():
        if d not in _NON_DOMAINS and isinstance(v, (int, float)) and v >= SIM_DOMAIN_EVIDENCE:
            out.add(d)
    if t.path == PATH_FAST:
        rd = reply_domain(t.reply)
        if rd:
            out.add(rd)
        if t.sky_domain and not t.sky_vetoed:
            out.add(t.sky_domain)
    return out


def resolved_route(t: Turn) -> str:
    """The fast domain that SUCCESSFULLY served ``t`` ('' = the brain answered, a can't-do / error reply, a cut-off reply, or no fast domain
    is evidenced). This is the label a failed turn takes from the follow-up that worked (Phase 2: ``struggle_proposals``). Pure."""
    if t.path != PATH_FAST or t.interrupted or cant_do(t.reply):
        return ""
    served = reply_domain(t.reply)
    if served in FAST_DOMAINS:
        return served
    dom = t.two_stage_domain if t.two_stage_domain not in _NON_DOMAINS else t.domain()
    return dom if dom in FAST_DOMAINS and dom in domains(t) else ""


def _pair_fix(prev: Turn) -> str:
    """Why the previous turn failed, from how it was served."""
    if prev.path == PATH_BRAIN and prev.domain() in FAST_DOMAINS:
        return FIX_ROUTER_TRAINING
    if prev.path == PATH_BRAIN:
        return FIX_ANSWER_QUALITY
    return FIX_CAPABILITY_GAP


def _new_slots(prev_text: str, cur_text: str, prev_reply: str = "") -> set[str]:
    """Slots of ``cur`` that neither ``prev`` nor the reply to it said (as any word): a new time / number / place / name /
    on-off. A slot Zoe's own answer established ("You have 1 event today") is not a new question when the user repeats it."""
    prev_toks = set(tokens(prev_text)) | set(tokens(prev_reply))
    return {s_ for s_ in slots(cur_text) if s_ not in prev_toks}


#: number words that sound like ordinary words ("two on the light" = "turn on the light", "for" / "four", "ate" / "eight")
_NUMBER_HOMOPHONES = frozenset({"two", "to", "too", "four", "for", "eight", "ate", "won", "ten", "then"})


def _raw_tokens(text: str) -> list[str]:
    """Like ``tokens`` but number words stay words (a mishearing swaps a WORD for a sound-alike number word)."""
    t = strip_wake(text).replace("’", "'").lower()
    for rx, rep_ in _CONTRACTION_RES:
        t = rx.sub(rep_, t)
    return [w for w in re.sub(r"[^\w\s']", " ", t).replace("'", "").split() if w not in _FILLERS]


def _homophone_mishear(prev: Turn, cur: Turn, gap: float) -> Optional[list]:
    """A voice repeat whose only difference is one or two sound-alike words where ONE side is a number homophone
    ("two on the light" -> "turn on the light"). Two numbers, two places or two on/off words are a changed request."""
    if cur.channel != "voice" or gap > MISHEAR_WINDOW_S:
        return None
    rp, rc = _raw_tokens(prev.text), _raw_tokens(cur.text)
    if len(rc) < 3 or len(rp) != len(rc):
        return None
    diff = token_diff(rp, rc)
    if not diff or not all(op == "replace" and len(a) == len(b) for op, a, b in diff) or sum(len(a) for _, a, _ in diff) > 2:
        return None
    pairs = [(x, y) for _, a, b in diff for x, y in zip(a, b)]
    if not all(sound_alike(x, y) for x, y in pairs):
        return None
    if not all((x in _NUMBER_HOMOPHONES) != (y in _NUMBER_HOMOPHONES) for x, y in pairs):
        return None
    return pairs


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
    # ── MISHEAR: a number homophone heard where the user said a word ("two on the light"), then said clearly
    pairs = _homophone_mishear(prev, cur, gap)
    if pairs:
        return [Event(MISHEAR, 0.8, FIX_STT, cur, prev, ["number_homophone"], {**nums, "swapped": len(pairs)})]
    if _new_slots(prev.text, cur.text, prev.reply) or _new_slots(cur.text, prev.text):
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
        if not (domains(prev) & domains(cur)) or len(tc) < 2 or len(tp) < 2:
            return []
        sp, sc = shape(prev.text).split(":"), shape(cur.text).split(":")
        reasons = []
        if only_inserts and content_tokens(tc) - cp:
            reasons.append("refined")
        if (shared and sim >= 0.55) or (len(shared) >= 2 and sim >= 0.45):   # two shared content words need less wording
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


_CONTRAST_RE = re.compile(
    r"(?:^|\b)(?:actually|it is|it was|its|it's|that is|that was|that's|i said|i meant|i wanted|i asked for|i want)\b"
    r"(?P<x>[^.?!]{1,60}?),?\s+not\s+(?:a |an |the |my )?(?P<y>[a-z0-9' ]{2,40}?)\s*(?:[.!?,]|$)")


def contrast_correction(prev: "Turn", text: str) -> Optional[str]:
    """"It's actually a garden shop, not a garbage shop": the user puts the right X against a wrong Y that the previous
    exchange (their own misheard words or Zoe's reply) contained. Returns ``contrast`` or None. Pure."""
    said = set(content_tokens(tokens(prev.text))) | set(content_tokens(tokens(prev.reply)))
    for sent in [display(p) for p in re.split(r"(?<=[.!?])\s+", strip_wake(text))][:2]:
        m = _CONTRAST_RE.search(sent)
        if not m:
            continue
        x, y = content_tokens(tokens(m.group("x"))), content_tokens(tokens(m.group("y")))
        if x and y and y <= said:         # a real X ("I'm not hungry" has none) against a Y the exchange contained
            return "contrast"
    return None


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


#: words a request for each fast domain plausibly carries. A FALLTHROUGH on the head's word ALONE (a domain with no similarity
#: examples scores 0.0, so there is no second witness) also needs one of these in the turn: the head put a calculator follow-up
#: ("that one divided by 3.5") in ``music`` at 0.9+ and the report blamed the router for a domain the words never touched.
_DOMAIN_CUES = {
    "music": re.compile(
        r"\b(?:play(?:ing)?|music|songs?|tracks?|tunes?|albums?|artists?|playlists?|radio|spotify|volume|louder|quieter|softer"
        r"|skip|next|previous|pause|resume|shuffle|repeat|listen(?:ing)?|band|sing|dj|jazz|rock|pop|put on)\b|turn (?:it )?(?:up|down)"),
    "smart_home": re.compile(
        r"\b(?:lights?|lamps?|bulbs?|switch(?:es)?|plugs?|fans?|heater|heating|aircon|ac|air conditioning|thermostat|temperature"
        r"|warmer|cooler|colder|hotter|doors?|lock|unlock|garage|gate|blinds?|curtains?|shutters?|tv|television|brightness|dim"
        r"|brighter|darker|scenes?|kettle|vacuum|turn|toggle|open|close|set|room|bedroom|kitchen|bathroom|lounge|hallway"
        r"|porch|garden|office)\b"),
    "weather": re.compile(
        r"\b(?:weather|forecast|rain(?:ing)?|sunny|temperature|hot|cold|warm|windy|wind|umbrella|jacket|coat|snow|storm|degrees"
        r"|humid|outside|today|tomorrow|tonight|week(?:end)?|high|low|cloud(?:s|y)?|sun)\b"),
    "time": re.compile(r"\b(?:time|clock|date|day|today|o'?clock|hour|minute|morning|afternoon|evening|tonight)\b"),
    "timers": re.compile(r"\b(?:timers?|alarms?|countdown|minutes?|seconds?|hours?|stopwatch|remind)\b"),
    "lists": re.compile(r"\b(?:lists?|add|shopping|groceries|grocery|to-?do|tasks?|buy|items?|put|remove|tick|cross)\b"),
    "calendar": re.compile(
        r"\b(?:calendar|meetings?|appointments?|events?|schedule|agenda|booked|diary|busy|free|today|tomorrow|tonight|week"
        r"|weekend|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next)\b"),
    "reminders": re.compile(r"\b(?:remind(?:er|ers|s)?|remember|forget|don'?t let me|alert|nudge)\b"),
}


def domain_plausible(dom: str, text: str) -> bool:
    """True when ``text`` carries a word a request for fast domain ``dom`` plausibly has (or ``dom`` has no lexicon). Pure."""
    rx = _DOMAIN_CUES.get(dom)
    return rx is None or bool(rx.search((text or "").lower()))


def _is_arithmetic(text: str) -> bool:
    """A sum ("two plus two", "that one divided by 3.5"): never a request for a fast DOMAIN, whatever a head said about it."""
    try:
        import calc_speech

        return calc_speech.parse(strip_wake(text), 1.0) is not None
    except Exception:  # noqa: BLE001 - a guard that cannot run is no guard: the other rules decide
        return False


def _fallthrough(cur: Turn) -> Optional[Event]:
    """A turn the router placed in a fast domain that the brain - or ANOTHER fast domain - answered."""
    if _is_arithmetic(cur.text):
        return None
    ts_dom = cur.two_stage_domain if cur.two_stage_domain not in _NON_DOMAINS else ""
    score = cur.predicted_score or 0.0
    if ts_dom:
        # two witnesses must agree: the head chose a fast domain AND the similarity router scores that domain too (a domain
        # with no similarity examples - smart_home / music - scores 0.0 there, so the head alone must then be sure AND the
        # words must carry a cue for that domain)
        sure = score >= FALLTHROUGH_TWO_STAGE_SIM or (
            score <= 0.0 and (cur.two_stage_conf or 0.0) >= 0.9 and domain_plausible(ts_dom, cur.text))
        if not sure:
            return None
        dom, reasons, conf = ts_dom, ["two_stage_fast_domain"], 0.85
    elif cur.two_stage_failed and cur.predicted_domain in FAST_DOMAINS and score >= FALLTHROUGH_MIN_SCORE:
        dom, reasons, conf = cur.predicted_domain, ["two_stage_failed"], 0.85
    elif not cur.two_stage_active and cur.predicted_domain in FAST_DOMAINS and score >= FALLTHROUGH_SIMILARITY_ONLY:
        dom, reasons, conf = cur.predicted_domain, ["similarity_fast_domain"], 0.75
    elif (cur.sky_vetoed and cur.sky_domain in FAST_DOMAINS and cur.path == PATH_BRAIN
          and ((cur.sim_scores or {}).get(cur.sky_domain, 0.0) >= SIM_DOMAIN_EVIDENCE or cur.head_top == cur.sky_domain)):
        # Skybridge recognised the request, the router vetoed it, the brain served it slowly; a second witness (the
        # similarity score for that domain, or the head's own top pick under its gate) keeps a declarative that merely
        # mentions rain or a meeting out of it
        dom, reasons, conf = cur.sky_domain, ["skybridge_vetoed"], 0.8
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
    if cur.stt_snap:
        snap = cur.stt_snap
        events.append(Event(MISHEAR, 0.8, FIX_STT_SNAPPED, cur, cur, ["stt_snapped", str(snap.get("outcome") or "")],
                            {k: snap.get(k) for k in ("intent", "safety", "cost", "position")}))
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
    # the pause the user took: from the END of the previous turn (its reply done) to the START of this one when known
    if cur.ts < prev.ts:
        return events
    gap = max(0.0, (cur.started if cur.started is not None else cur.ts) - prev.ts)
    fb = _feedback_kind(cur.text) if gap <= CORRECTION_WINDOW_S else None
    if fb:
        events.append(Event(NEGATIVE_FEEDBACK, 0.95 if fb != "complaint" else 0.85, _pair_fix(prev), cur, prev, [fb],
                            {"gap_s": round(gap, 1)}))
        return events
    cue = correction_cue(prev.reply, cur.text) if gap <= CORRECTION_WINDOW_S and (prev.reply or "").strip() else None
    if not cue and gap <= CORRECTION_WINDOW_S:
        cue = contrast_correction(prev, cur.text)
    if cue:
        events.append(Event(CORRECTION, 0.9 if cue == "explicit" else 0.75, _pair_fix(prev), cur, prev, [cue],
                            {"gap_s": round(gap, 1)}))
        return events
    rep_ev = _repeat_signals(prev, cur, gap, window_s)
    if not rep_ev:
        rep_ev = _detail_after_count(prev, cur, gap, window_s)
    events.extend(rep_ev)
    return events


def _detail_after_count(prev: Turn, cur: Turn, gap: float, window_s: float) -> list[Event]:
    """"What's on my calendar?" -> "You have 1 event today." -> "what is it?": the fast path answered a count and the user
    had to ask again for the thing itself. Only right after a COUNT-ONLY fast reply; "what is it?" anywhere else is not."""
    if gap > window_s or prev.path != PATH_FAST or not _COUNT_ONLY_RE.match((prev.reply or "").strip()):
        return []
    if not _ANAPHORIC_ASK_RE.match(normalise(cur.text)):
        return []
    return [Event(REPHRASE, 0.8, FIX_CAPABILITY_GAP, cur, prev, ["detail_after_count"], {"gap_s": round(gap, 1)})]


#: how many earlier turns of the conversation a repeat may reach back over (the previous one is the pair rule's)
LOOKBACK_TURNS = 3
_PAIR_FAMILY = frozenset({RE_ASK, MISHEAR, REPHRASE, CORRECTION, NEGATIVE_FEEDBACK})


def detect_window(history: list, cur: Turn, *, window_s: float = DEFAULT_WINDOW_S) -> list[Event]:
    """``detect`` against the previous turn, plus a look BACK over up to ``LOOKBACK_TURNS`` earlier turns of the same
    conversation by the same user for a repeat the pair rule cannot see: "what is it?" asked twice with another question in
    between, or the fourth way of asking for the same calendar entry. Only RE_ASK / REPHRASE reach back (a mishearing is
    corrected at once), only inside the window measured from that earlier turn's end, and an earlier hit is attributed to
    the earlier turn. A RE_ASK may still be found when the pair rule only found a REPHRASE."""
    prev = history[-1] if history else None
    events = detect(prev, cur, window_s=window_s)
    found = {e.signal for e in events if e.about is not cur}
    if found & {RE_ASK, MISHEAR, CORRECTION, NEGATIVE_FEEDBACK} or wake_only(cur.text):
        return events
    want = {RE_ASK} if REPHRASE in found else {RE_ASK, REPHRASE}
    start = cur.started if cur.started is not None else cur.ts
    for earlier in reversed(history[-1 - LOOKBACK_TURNS:-1]):
        if earlier.user_id != cur.user_id or earlier.conv_key != cur.conv_key:
            continue
        gap = start - earlier.ts
        if gap < 0 or gap > window_s:
            break
        # reaching back, a REPHRASE needs domain evidence on both turns: a conversational "tell me more about that" asked
        # again around another question is not a struggle the words alone can prove
        hit = [e for e in _repeat_signals(earlier, cur, gap, window_s) if e.signal in want
               and (e.signal == RE_ASK or domains(earlier) & domains(cur))]
        if hit:
            for e in hit:
                e.reasons.append("earlier_turn")
            events.extend(hit)
            break
    return events
