"""Single source of truth for the memory-recall keyword gate.

The expensive MemPalace semantic search (ONNX embed + Chroma query) should only
run when a message looks like a recall / personal-fact query, not on every turn.
Both the legacy `zoe_agent` brain and the `/api/memories/for-prompt` endpoint
(which the Pi `memory.ts` extension calls each turn) gate on this — so the words
live HERE, imported by both, to avoid the two copies silently diverging.

Dependency-free on purpose (pure str → bool) so any module can import it cheaply.
"""
from __future__ import annotations

import re

MEMORY_TRIGGER_WORDS = frozenset({
    "remember", "recall", "did i", "have i", "last time", "before",
    "you said", "we talked", "my name", "my preference", "i told you",
    "favourite", "favorite", "prefer", "like", "usually", "always", "never", "often",
    "who is", "what is my", "what do i", "what's my", "family", "remind me",
    "do i have", "my favourite", "my favorite", "my usual", "i usually", "i like",
    "i prefer", "i love", "i hate", "i enjoy", "do you know my",
    # Personal-fact retrieval phrases.
    "born", "age", "years old", "my age", "how old", "my birthday", "birthday",
    "my full name", "called", "known as", "allerg", "condition", "medical",
})


# Emotional-state cues — recall of stored `emotional_moment` rows (Samantha
# criterion #2 continuity). Kept SEPARATE from MEMORY_TRIGGER_WORDS: this set is
# consulted only by `message_needs_emotional_recall`, which the for-prompt
# endpoint calls behind ZOE_EMOTIONAL_RECALL_ENABLED — so the default recall gate
# is unchanged until that flag is on. Substrings, lowercased. Covers the user
# *asking* about their state ("how have I been", "how am I doing") and *sharing*
# one ("I've been so stressed", "feeling overwhelmed") — both are cues to pull
# past emotional continuity. Both valences on purpose (the 4B brain under-captures
# joy, so we must not also under-recall it).
EMOTIONAL_TRIGGER_WORDS = frozenset({
    # "how have I been" style state-of-being recall. NOTE: bare "feeling" and bare
    # "how am i" are deliberately NOT here — they over-fire ("feeling like pizza",
    # "how am I supposed to configure this"); anchor to the emotional phrasings.
    "how i feel", "how i'm feeling", "how i've been feeling", "how i've been",
    "how have i been", "how i been", "been feeling", "how are you feeling",
    "feeling down", "feeling low", "feeling anxious", "feeling overwhelmed",
    "feeling stressed", "feeling sad", "feeling lonely", "feeling numb",
    "how am i doing", "how am i feeling", "how am i holding up",
    "how are things", "how's things",
    "stressed", "stressing", "stress about", "anxious", "anxiety",
    "worried", "worrying", "overwhelmed", "depressed", "burnt out", "burned out",
    "lonely", "grieving", "grief", "heartbroken", "upset", "struggling",
    "coping", "mental health", "my mood", "been down",
    # "going through" anchored so it doesn't fire on "going through my email".
    "going through a lot", "going through a hard", "going through a rough",
    "going through a tough",
    # positive-valence emotional recall (symmetry with joy capture)
    "excited about", "so happy", "happy about", "made me happy", "makes me happy",
    "made me smile", "proud of", "thrilled", "over the moon",
})


# A possessive self-reference ("my dad", "our house") — a strong recall signal.
# NOTE: deliberately excludes the object pronoun "me" (it appears in request
# phrases "tell me / give me / remind me" that are not recall).
_POSSESSIVE_RE = re.compile(r"\b(?:my|mine|our|ours)\b", re.IGNORECASE)
# A first-person SUBJECT ("I", "we") — the user asking about their own state.
# Just \b(?:i|we)\b: the apostrophe in contractions ("I'm", "we're") is a word
# boundary, so this still matches the subject of "I've", "we're", etc., while NOT
# matching "ill" or "were" (a letter follows, so no boundary) — avoiding the
# false positives an optional-apostrophe pattern (i'?ll → "ill") would cause.
_FIRST_PERSON_SUBJ_RE = re.compile(r"\b(?:i|we)\b", re.IGNORECASE)
# A question / recall shape — a leading interrogative or request-to-tell.
_QUESTION_SHAPE_RE = re.compile(
    r"^\s*(?:hey\s+|ok\s+|so\s+|um\s+|uh\s+|zoe[,\s]+)*"
    r"(?:who|what|what'?s|when|when'?s|where|where'?s|why|how|which|whose|"
    r"do|does|did|are|is|was|were|have|has|can|could|would|will|should|am|"
    r"tell\s+me|remind\s+me)\b",
    re.IGNORECASE,
)
# Procedural "how do/can I …" — a how-to, NOT a recall of stored facts.
_PROCEDURAL_HOW_RE = re.compile(r"^\s*how\s+(?:do|can|would|should|could)\s+(?:i|we|you)\b", re.IGNORECASE)


# ── Event-shaped recall questions (Samantha bar S1 round 3) ──────────────────
#
# "Who is flying in on Thursday, and where from?" asks about something the user
# TOLD Zoe ("my sister Marisol is flying in from Lisbon on Thursday"), yet it
# carries no "my"/"I" — so neither the personal-question recall floor nor the
# structural half of `message_needs_memory` saw it. These shapes are anchored
# to the user's own life by a people-movement verb (someone arriving, visiting,
# leaving) PLUS one of: a time cue (who + move + when), a relation word ("my
# sister", "our parents"), or a he/she/they subject ("where is she flying
# from"). General knowledge ("who is the prime minister", "who is playing on
# Sunday", "when does the train leave", "what is the weather today") has none
# of those pairings and never matches.
_EVT_MOVE = (
    r"(?:fly(?:ing|s)?|flies|flown|com(?:e|es|ing)|arriv(?:e|es|ing)|land(?:s|ing)?|"
    r"visit(?:s|ing)?|get(?:s|ting)?\s+in|stay(?:s|ing)?|leav(?:e|es|ing)|"
    r"driv(?:e|es|ing)\s+(?:up|down|over|in|back)|"
    r"head(?:s|ing)?\s+(?:over|home|back|down|up|in)|"
    r"drop(?:s|ping)?\s+(?:by|in|over)|pop(?:s|ping)?\s+(?:by|in|over|round)|"
    r"mov(?:e|es|ing)\s+in|turn(?:s|ing)?\s+up|show(?:s|ing)?\s+up|back)"
)
_EVT_DAY = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_EVT_TIME = (
    r"(?:today|tonight|tomorrow|this\s+(?:morning|afternoon|evening|week(?:end)?)|"
    r"next\s+(?:week(?:end)?|month|" + _EVT_DAY + r")|(?:on\s+)?" + _EVT_DAY + r"|"
    r"(?:at|over)\s+the\s+weekend|for\s+(?:christmas|easter|the\s+holidays))"
)
_EVT_REL = (
    r"(?:mum|mom|mother|dad|father|parents?|sister|brother|siblings?|sons?|"
    r"daughters?|kids?|children|wife|husband|partner|boyfriend|girlfriend|"
    r"fianc[eé]e?|friends?|mates?|cousins?|aunt|auntie|uncle|nan|nana|gran|"
    r"grandma|grandmother|grandpa|grandfather|grandparents|in-laws|niece|nephew|"
    r"family|flatmate|roommate|housemate|neighbou?rs?|boss|colleagues?)"
)
_EVT_WH_AUX = (
    r"(?:when|where|what\s+time|what\s+day|how\s+long)"
    r"(?:['’]s|\s+(?:is|are|was|were|does|do|did|will))\s+"
)
# who + movement + time cue: "who is flying in on Thursday", "who's coming over
# tonight", "who is staying with us this weekend" — and the bare present tense
# with no auxiliary: "Who arrives on Thursday?", "Who flies in on Thursday?"
# (Greptile #1770). The bare form must OPEN the question (message or clause
# start, optionally after "so/and/hey/ok/remind me/tell me") so a relative
# clause in a statement ("my cleaner, who comes on Friday, …") is not a question.
_EVT_WHO_TIME_RE = re.compile(
    r"(?:\bwho(?:['’]s|\s+is|\s+are|\s+was|\s+will\s+be)\s+"
    r"|(?:^|[.!?;:]\s*)(?:(?:so|and|hey|ok|okay|remind\s+me|tell\s+me)[,\s]+)?who\s+)"
    + _EVT_MOVE + r"\b[^.?!]{0,60}?\b" + _EVT_TIME + r"\b",
    re.IGNORECASE,
)
# Anchored shapes — a relation word or a pronoun ties them to the user's people:
#   when/where/what time + my/our + relation: "where is my sister flying from",
#   "what time does my dad land", "when are our parents arriving";
#   when/where/what time + he/she/they + movement: "where is she flying from".
_EVT_ANCHORED_RE = re.compile(
    r"\b" + _EVT_WH_AUX + r"(?:my|our)\s+" + _EVT_REL + r"\b"
    r"|"
    r"\b" + _EVT_WH_AUX + r"(?:she|he|they)\s+" + _EVT_MOVE + r"\b",
    re.IGNORECASE,
)
# A public event or venue as the destination/object ("Who is coming to the game
# on Friday?") is a question about the world, not the user's people — the
# who+time shape does not claim it unless the message also names the user's own
# people (Greptile #1770).
_EVT_PUBLIC_RE = re.compile(
    r"\b(?:game|match|concert|gig|show|festival|finals?|race|parade|premiere|"
    r"conference|olympics|world\s+cup|derby|tournament|playoffs?|grand\s+prix|"
    r"election|debate|oscars|grammys|super\s+bowl|stadium|arena|theat(?:re|er)|"
    r"cinema|movies|ceremony|summit|rally|launch|opening)\b",
    re.IGNORECASE,
)
_EVT_PERSONAL_ANCHOR_RE = re.compile(
    r"\b(?:my|our)\s+" + _EVT_REL + r"\b|\b(?:he|she|they|me|us|we)\b",
    re.IGNORECASE,
)


# Sentence boundaries for scoping the event checks: the anchors and the
# public-event exclusion are judged INSIDE the sentence that holds the question,
# so an unrelated sentence ("… The game is Friday", "We're busy. …") neither
# vetoes nor vouches for it (Greptile #1771). Commas and dashes do not split:
# "Who is flying in on Thursday, and where from?" is one question.
_EVT_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|(?<=[.!?;])(?=[A-Z])")


def event_sentences(message: str) -> list[str]:
    """The message's sentences (split on . ! ? ; only), stripped, non-empty."""
    return [p.strip() for p in _EVT_SENTENCE_SPLIT_RE.split(message or "") if p.strip()]


def _sentence_is_event_question(sentence: str) -> bool:
    if _EVT_ANCHORED_RE.search(sentence):
        return True
    if not _EVT_WHO_TIME_RE.search(sentence):
        return False
    return not (_EVT_PUBLIC_RE.search(sentence)
                and not _EVT_PERSONAL_ANCHOR_RE.search(sentence))


def is_event_question(message: str) -> bool:
    """True for an event-shaped question about the user's own people/plans:
    in SOME sentence of the message, an anchored shape (a my/our relation or a
    he/she/they subject), or who + movement + time cue when that sentence names
    no public event/venue (or names one together with the user's own people).
    Pure str → bool."""
    return any(_sentence_is_event_question(s) for s in event_sentences(message))


# ── Evidence-shaped questions (ZOE_RECALL_EVIDENCE, context gaps #3/#6) ──────
# WHEN the user told Zoe something, WHAT they said, or whether Zoe is sure: on
# these turns the recall packet quotes the user's own words (`recall_evidence`).
# Table-driven, first match wins; every row pins "I"/"we" or a challenge aimed at
# Zoe, so "when did the war end" / "make sure the light is off" never match. The
# "sure" rows overlap zoe_agent._VERIFY_CHALLENGE_RE (the chat lane's WEB check),
# which is too heavy to import here.
EVIDENCE_QUESTION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (kind, re.compile(rx, re.IGNORECASE))
    for kind, rx in (
        ("when", r"\bwhen\s+did\s+(?:i|we)\b"),
        ("when", r"\bwhen\s+was\s+(?:it\s+)?(?:that\s+)?(?:i|we)\s+(?:told|said|mentioned)\b"),
        ("when", r"\bhow\s+long\s+ago\s+did\s+(?:i|we)\b"),
        ("when", r"\bwhat\s+(?:day|date)\s+did\s+(?:i|we)\b"),
        ("when", r"\bwhen\s+did\s+you\s+(?:learn|find\s+out|hear)\b"),
        ("said", r"\bwhat\s+(?:exactly\s+)?(?:did|have|had)\s+i\s+(?:say|said|tell|told|mention|mentioned)\b"),
        ("said", r"\bwhat\s+i\s+(?:said|told\s+you|mentioned)\b"),
        ("said", r"\b(?:did|have)\s+i\s+(?:ever\s+|really\s+|actually\s+)?(?:say|said|tell|told|mention|mentioned)\b"),
        ("said", r"\bmy\s+(?:exact\s+|own\s+)?words\b"),
        ("sure", r"\b(?:are|r)\s+(?:you|u)\s+(?:really\s+)?(?:sure|certain|positive)\b"),
        ("sure", r"^\W*(?:you\s+sure|sure\s*\?)"),
        ("sure", r"\bhow\s+do\s+you\s+know\s+(?:that|this)\b"),
        ("sure", r"\bwhere\s+did\s+you\s+(?:get|hear)\s+that\b"),
        ("sure", r"\bi\s+(?:never|didn['’]?t|did\s+not)\s+(?:say|said|tell|told|mention|mentioned)\b"),
    )
)


def evidence_question_kind(message: str) -> str:
    """"when" / "said" / "sure" for an evidence-shaped question, else "".
    First matching row of ``EVIDENCE_QUESTION_PATTERNS`` wins. Pure."""
    text = message or ""
    for kind, rx in EVIDENCE_QUESTION_PATTERNS:
        if rx.search(text):
            return kind
    return ""


def is_evidence_question(message: str) -> bool:
    """True when the recall packet should quote the user's own words."""
    return bool(evidence_question_kind(message))


def _first_match(patterns: tuple[tuple[str, re.Pattern], ...], message: str) -> str:
    text = message or ""
    return next((kind for kind, rx in patterns if rx.search(text)), "")


# ── Present-state questions about the user's OWN stored facts (day-sim 6/6n) ─
# "Am I still doing the half-marathon?", "Do I still get migraines?", "How's my
# mum?" carry no what's-my/when-did-I shape, so the Flue recall floor sent no
# packet and the brain answered "not sure I have that saved" / "see your doctor"
# (live 2026-10-03). Each row pins I/we/my/our; the lookaheads keep session,
# device and weather-advice questions out ("am I still connected?", "is my timer
# still running?", "do I still need an umbrella?"). First match wins.
_PS_SESSION = (
    r"(?:connected|online|offline|there|here|muted|live|audible|paired|recording|"
    r"on\s+(?:the\s+)?(?:call|line|hold|mute|speaker|wi-?fi|network|air)|"
    r"(?:logged|signed)\s+(?:in|on)|being\s+(?:recorded|heard)|talking\s+to\s+you)"
)
_PS_DEVICE = (
    r"(?:timers?|alarms?|wi-?fi|internet|connection|phone|battery|music|song|"
    r"playlist|volume|lights?|tv|telly|speakers?|mic(?:rophone)?|camera|screen|"
    r"panel|bluetooth|app|account|subscription|order|parcel|package|delivery)"
)
_PS_PET = (
    r"(?:pets?|dogs?|cats?|pupp(?:y|ies)|pups?|kittens?|horses?|budgies?|parrots?|"
    r"rabbits?|bunn(?:y|ies)|hamsters?|guinea\s+pigs?|chooks|chickens|fish)"
)
PRESENT_STATE_QUESTION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (kind, re.compile(rx, re.IGNORECASE))
    for kind, rx in (
        ("still", r"\b(?:(?:am|was)\s+i|(?:are|were)\s+we)\s+still\b(?!\s+" + _PS_SESSION + r"\b)"),
        ("still", r"\b(?:do|did)\s+(?:i|we)\s+still\b(?!\s+need\s+(?:an?\s+|my\s+)?"
                  r"(?:umbrella|jacket|coat|jumper|sunscreen)\b)"),
        ("still", r"\b(?:is|are|was|were)\s+(?:my|our)\s+(?!" + _PS_DEVICE + r"\b)"
                  r"(?:[\w'’-]+\s+){1,3}?still\b"),
        ("still", r"\bhave\s+(?:i|we)\s+still\s+got\b"),
        ("how", r"\bhow(?:['’]s|\s+is|\s+are|\s+was|\s+were)\s+(?:my|our)\s+"
                r"(?:" + _EVT_REL + r"|" + _PS_PET + r")\b"),
        ("which", r"\bwhich\s+(?:[\w'’-]+\s+){1,3}?(?:am|are|was|were)\s+(?:i|we)\b"),
        ("which", r"\bwhich\s+(?:[\w'’-]+\s+){1,3}?do\s+(?:i|we)\s+(?:have|own|use|support|"
                  r"barrack\s+for|go\s+to|work|drive|prefer|like)\b"),
    )
)


def present_state_question_kind(message: str) -> str:
    """"still" / "how" / "which" for a present-state question about the user's
    own stored facts, else "". Pure."""
    return _first_match(PRESENT_STATE_QUESTION_PATTERNS, message)


# ── Event-time questions about the user's OWN plans (day-sim 9) ──────────────
# "What time is my dentist appointment on Friday?" was claimed by the head as
# time @ 0.997 and answered "It's 10:41 PM." (live 2026-10-03). A when/what-time
# question about the user's own appointment is a calendar/memory question, never
# the clock. Rows pin my/our or I/we AND an event noun (or a travel/shift verb),
# so "what time is it", "what time does the game start" and "what time is my
# alarm set for" never match. First match wins.
_ET_NOUN = (
    r"(?:appointments?|appt|meetings?|flights?|dentist|doctor(?:['’]s)?|gp|physio|"
    r"chiro|vet|optometrist|surgery|operation|scan|x-?ray|check-?up|interview|exams?|"
    r"tests?|class(?:es)?|lessons?|lectures?|shifts?|sessions?|booking|reservation|"
    r"dinner|lunch|breakfast|brunch|party|wedding|funeral|game|match|race|training|"
    r"practice|rehearsal|recital|concert|gig|haircut|massage|therapy|counselling|"
    r"pick-?up|drop-?off|train|bus|ferry|call|presentation|deadline|event|date|"
    r"visit|trip|holiday)"
)
_ET_WH = r"(?:when|what\s+time|what\s+day|which\s+day|what\s+date)"
EVENT_TIME_QUESTION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (kind, re.compile(rx, re.IGNORECASE))
    for kind, rx in (
        ("my_event", r"\b" + _ET_WH + r"(?:['’]s|\s+(?:is|are|was|were|does|do|did|will))\s+"
                     r"(?:my|our)\s+(?:[\w'’-]+\s+){0,3}?" + _ET_NOUN + r"\b"),
        ("have_event", r"\b" + _ET_WH + r"\s+(?:do|am|are|have)\s+(?:i|we)\s+(?:got\s+)?"
                       r"(?:[\w'’-]+\s+){0,2}?" + _ET_NOUN + r"\b"),
        ("my_move", r"\b(?:when|what\s+time)\s+(?:do|am|are)\s+(?:i|we)\s+(?:seeing|meeting|"
                    r"fly(?:ing)?|land(?:ing)?|leav(?:e|ing)|depart(?:ing)?|due|booked|"
                    r"start(?:ing)?|finish(?:ing)?|on)\b"),
    )
)


def event_time_question_kind(message: str) -> str:
    """"my_event" / "have_event" / "my_move" for a question about WHEN the
    user's own event is, else "". Pure."""
    return _first_match(EVENT_TIME_QUESTION_PATTERNS, message)


def is_event_time_question(message: str) -> bool:
    return bool(event_time_question_kind(message))


def message_needs_memory(message: str) -> bool:
    """True when the message likely benefits from MemPalace semantic search.

    Fires on either (a) an explicit trigger word/phrase, or (b) a *structural*
    signal: a self-reference in a question/recall shape, or an event-shaped
    question about the user's people/plans (``is_event_question``). The
    structural rule catches natural recall phrasings the keyword list misses
    ("where do I live", "tell me about my mum", "what team do we support",
    "where is she flying from") while staying off non-personal questions
    ("what's the weather") and procedural how-tos ("how do I make pasta") that
    would waste the embed.
    """
    text = message or ""
    low = text.lower()
    if any(kw in low for kw in MEMORY_TRIGGER_WORDS):
        return True
    if is_event_question(text):
        return True
    is_question = bool(_QUESTION_SHAPE_RE.match(text)) or low.rstrip().endswith("?")
    if not is_question:
        return False
    # A possessive ("my/our") in a question is recall regardless of phrasing.
    if _POSSESSIVE_RE.search(text):
        return True
    # A first-person subject question ("where do I live") is recall too — unless
    # it's a procedural "how do I <action>".
    if _FIRST_PERSON_SUBJ_RE.search(text) and not _PROCEDURAL_HOW_RE.match(text):
        return True
    return False


def message_needs_emotional_recall(message: str) -> bool:
    """True when the message carries an emotional-state cue worth pulling stored
    `emotional_moment` rows for. Kept SEPARATE from `message_needs_memory` so the
    default recall gate is unchanged: the for-prompt endpoint ORs this in only
    when ZOE_EMOTIONAL_RECALL_ENABLED is set. Pure str → bool, no env read here
    (the flag decision stays at the endpoint), so both callers stay cheap."""
    low = (message or "").lower()
    return any(cue in low for cue in EMOTIONAL_TRIGGER_WORDS)


# ── Affect at capture time (Samantha bar S4) ─────────────────────────────────
#
# The per-turn digest rewrites "Honestly I'm pretty anxious about my job
# interview at the aquarium on Friday" as the neutral fact "The user has a job
# interview at the aquarium on Friday" — the feeling is exactly what emotional
# continuity needs, and it is gone before anything is stored. The RAW turn still
# has it at capture time, so `extract_affect` reads it deterministically from the
# user's own words and the digest stores it beside the fact (`affect` metadata).
#
# First-person only: "my sister is anxious" is not the user's feeling. Each
# label maps the surface forms that express it; the label is what is rendered
# ("felt anxious").
_AFFECT_LABELS: tuple[tuple[str, str], ...] = (
    ("anxious", r"anxious|anxiety|nervous|on\s+edge|uneasy"),
    ("worried", r"worried|worrying|dreading"),
    ("stressed", r"stressed(?:\s+out)?|stressing|under\s+(?:a\s+lot\s+of\s+)?pressure"),
    ("scared", r"scared|afraid|terrified|frightened"),
    ("overwhelmed", r"overwhelmed|swamped"),
    ("sad", r"sad|down(?!\s+(?:for|to|with|here|there|at|in|on|by)\b)|low(?!\s+on\b)|"
            r"miserable|heartbroken|gutted"),
    ("upset", r"upset"),
    ("lonely", r"lonely"),
    ("frustrated", r"frustrated|annoyed|fed\s+up"),
    ("exhausted", r"exhausted|drained|burnt\s+out|burned\s+out|worn\s+out"),
    ("excited", r"excited|thrilled|pumped"),
    ("happy", r"happy|delighted|over\s+the\s+moon"),
    ("proud", r"proud"),
    ("relieved", r"relieved"),
)
_AFFECT_SOFTENERS = (
    r"(?:(?:so|really|pretty|quite|super|very|just|still|totally|kind\s+of|kinda|"
    r"sort\s+of|a\s+bit|a\s+little|a\s+little\s+bit|bit|incredibly|extremely|"
    r"honestly|feeling|more|getting)\s+){0,3}"
)
_AFFECT_ANCHOR = (
    r"(?:\bi(?:['’]?m|\s+am|['’]?ve\s+been|\s+have\s+been|\s+was|\s+feel|\s+felt|"
    r"\s+keep\s+feeling|\s+still\s+feel|\s+get|\s+got)|(?:^|[.!?,]\s*)\s*feeling)\s+"
)
_AFFECT_RES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (label, re.compile(_AFFECT_ANCHOR + _AFFECT_SOFTENERS + r"(?:" + forms + r")\b",
                       re.IGNORECASE))
    for label, forms in _AFFECT_LABELS
) + (("excited", re.compile(r"\bi\s+can['’]?t\s+wait\b", re.IGNORECASE)),)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# Someone ELSE's words are never the user's feeling (Greptile #1762):
#   * quoted spans — straight/curly double quotes, curly single quotes, and a
#     straight single-quoted span that opens after whitespace and closes before a
#     boundary (so the apostrophe in "I'm" is not taken for a quote);
#   * reported speech — "<someone other than I> said/says/told me/… (that) …" to
#     the end of its clause ("my sister said she is so stressed").
_QUOTED_SPAN_RE = re.compile(
    r"\"[^\"]*\"|“[^”]*”|‘[^’]*’|(?:(?<=\s)|^)'.*?'(?=\s|[,.!?;:]|$)"
)
# The reported span ends at its CLAUSE, not the sentence: "My sister said she
# is fine, but I'm stressed about my interview" keeps the user's own clause.
# A clause boundary is ", but/and/so/though/yet", ";", " but/however/although/
# though/whereas ", or ", " / " and " followed by a first-person subject.
# A first-person subject straight after the verb ("my sister said I'm
# stressed") stays inside the reported span — that is the sister speaking.
_CLAUSE_BOUNDARY = (
    r"(?:,\s*(?:but|and|so|though|yet)\b|;|\s(?:but|however|although|though|whereas)\b|"
    r"(?:,\s*|\s+and\s+)i(?:['’]m|\s+am|\s+feel|\s+felt|['’]ve|\s+have|\s+was)\b)"
)
_REPORTED_SPEECH_RE = re.compile(
    r"\b(?!i\b)[a-z']+\s+(?:said|says|say|told\s+(?:me|us)|tells\s+(?:me|us)|"
    r"asked|texted|wrote|messaged|mentioned|reckons|thinks)\b"
    r"(?:(?!" + _CLAUSE_BOUNDARY + r")[^.!?])*",
    re.IGNORECASE,
)


def _own_words(sentence: str) -> str:
    """``sentence`` with quoted spans and reported speech removed."""
    return _REPORTED_SPEECH_RE.sub(" ", _QUOTED_SPAN_RE.sub(" ", sentence))


def extract_affect(message: str) -> tuple[str, str]:
    """(label, sentence) for the first first-person feeling in ``message``, or
    ("", "") — e.g. "Honestly I'm pretty anxious about my job interview…" →
    ("anxious", "Honestly I'm pretty anxious about my job interview…"). The
    sentence is returned so a caller can attach the feeling only to facts drawn
    from it. Quoted and reported words are someone else's (``_own_words``).
    Pure str → tuple, no env read."""
    for sentence in _SENTENCE_SPLIT_RE.split(message or ""):
        own = _own_words(sentence)
        best: tuple[int, str] | None = None
        for label, rx in _AFFECT_RES:
            m = rx.search(own)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), label)
        if best is not None:
            return best[1], sentence.strip()
    return "", ""
