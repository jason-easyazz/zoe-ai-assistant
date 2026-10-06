"""Single source of truth for the memory-recall keyword gate.

The expensive MemPalace semantic search (ONNX embed + Chroma query) should only
run when a message looks like a recall / personal-fact query, not on every turn.
Both the legacy `zoe_agent` brain and the `/api/memories/for-prompt` endpoint
(which the Pi `memory.ts` extension calls each turn) gate on this — so the words
live HERE, imported by both, to avoid the two copies silently diverging.

Dependency-free on purpose (pure str → bool) so any module can import it cheaply.
"""
from __future__ import annotations

import os
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


# ── Own-fact recall questions (live 2026-10-04, ZOE_OWN_FACT_PRECEDENCE) ─────
# "When is my birthday?" was claimed by the head as time (the word "when") and
# answered "It's 7:50 AM." The same family — "what's my address", "how old am
# I", "where do I live", "when is mum's birthday" — asks for a FACT the user
# told Zoe, so no clock, calendar, weather or list tool can answer it. Each
# row pins a possessive / first-person anchor AND a fact noun (or a
# first-person verb for a stored fact), so "what time is it", "when is Easter",
# "what's my schedule" and "how old is the universe" never match. First match
# wins. Pure str -> kind.
_OF_NOUN = (
    r"(?:birthday|b-?day|birth\s*date|date\s+of\s+birth|dob|anniversary|age|"
    r"(?:home\s+|street\s+|postal\s+|email\s+|mailing\s+|work\s+)?address|post\s*code|"
    r"zip\s*code|(?:phone|mobile|cell)\s+(?:number|no\.?)|"
    r"e-?mail(?:\s+address)?|(?:sur|last|middle|full|first|nick|maiden)\s*name|name|"
    r"star\s+sign|zodiac(?:\s+sign)?|blood\s+type|(?:licen[cs]e|number)\s+plate|rego|"
    r"hometown|birthplace|place\s+of\s+birth|occupation|job(?:\s+title)?|"
    r"favou?rite\s+(?:[\w'’-]+)|shoe\s+size|height|weight)"
)
# First-person possessives (my / our / mine) and the user's own relations
# ("mum's"). A generic third-party possessive ("Obama's age") is deliberately NOT
# here: it is a world question, and claiming it as own-fact would re-point it to
# memory and switch the challenge verification off.
_OF_POSS = (
    r"(?:my|our|(?:mum|mom|mother|dad|father|nan|nana|gran|grandma|grandpa|sister|"
    r"brother|wife|husband|partner|son|daughter|boyfriend|girlfriend|"
    r"fianc[eé]e?|niece|nephew|cousin|aunt|auntie|uncle)['’]s)"
)
# The fact noun must END the question (filler words allowed): "what's my job today",
# "when is my rego due", "what's my phone bill" are not requests for a stored fact.
_OF_END = r"(?:\s+(?:again|please|then|now|exactly|anyway|zoe))*\W*$"
OWN_FACT_QUESTION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (kind, re.compile(rx, re.IGNORECASE))
    for kind, rx in (
        # "when is my birthday", "what's my address", "when's mum's birthday",
        # "what is our wifi..." is NOT here (no fact noun) — the noun is the guard.
        ("fact_noun", r"\b(?:when|what|which|whats)(?:['’]s|\s+(?:is|are|was|were))?\s+"
                      r"" + _OF_POSS + r"\s+(?:[\w'’-]+\s+)?" + _OF_NOUN + _OF_END),
        ("how_old", r"\bhow\s+old\s+(?:am\s+i|are\s+we|is\s+(?:my|our)\s+[\w'’-]+|"
                    r"is\s+(?:he|she)\b|is\s+(?:mum|mom|dad|nan|nana|gran)\b)"),
        ("born", r"\b(?:when|what\s+(?:year|day|date))\s+(?:was|were)\s+(?:i|we)\s+born\b"),
        ("born", r"\bwhere\s+(?:was|were)\s+(?:i|we)\s+born\b"),
        ("live", r"\bwhere\s+(?:do|did)\s+(?:i|we)\s+(?:live|work|study|grow\s+up|go\s+to\s+school)\b"),
        ("live", r"\bwhere\s+(?:am|are)\s+(?:i|we)\s+from\b"),
        ("live", r"\b(?:which|what)\s+(?:city|town|suburb|street|state|country|area)\s+"
                 r"(?:do|did)\s+(?:i|we)\s+(?:live|work|grow\s+up)\b"),
        ("have", r"\bwhat\s+(?:kind\s+of\s+|type\s+of\s+)?(?:car|dog|cat|pet|phone|job|team|"
                 r"school|uni|university)\s+do\s+(?:i|we)\s+(?:drive|have|own|support|go\s+to)\b"),
        ("have", r"\bwhat\s+do\s+i\s+do\s+for\s+(?:work|a\s+living)\b"),
        # "Who am I?" (live 2026-10-04: answered after a narrated lookup), "tell me
        # about myself", "what do you remember about me" — the whole stored profile.
        # Anchored to the whole utterance: "who am I meeting tomorrow" is a calendar question.
        ("self", r"^\W*(?:(?:so|and|hey|ok|okay|um|uh|zoe)[\s,]+)*who\s+am\s+i\W*$"),
        ("self", r"^\W*(?:(?:so|and|hey|ok|okay|um|uh|zoe)[\s,]+)*tell\s+me\s+(?:about\s+myself|who\s+i\s+am|"
                 r"what\s+you\s+know\s+about\s+me)\W*$"),
        ("self", r"\bwhat\s+(?:do\s+you|can\s+you)\s+(?:know|remember|tell\s+me)\s+(?:about|of)\s+me\b"),
    )
)


def own_fact_question_kind(message: str) -> str:
    """Kind ("fact_noun" / "how_old" / "born" / "live" / "have") for a question
    about a stored fact of the user's own life (birthday, address, age…), else
    "". Pure."""
    return _first_match(OWN_FACT_QUESTION_PATTERNS, message)


def is_own_fact_question(message: str) -> bool:
    return bool(own_fact_question_kind(message))


# ── Named-person questions (S21, ZOE_PERSON_RECALL_FLOOR) ────────────────────
#
# "How many children does Dana Whitfield have?" has no my/I, no event verb and no
# own-fact noun, so no recall-floor shape claimed it and the brain answered "I don't
# have any information about Dana Whitfield's children" with the corrected record in
# the store (Samantha bar S21: recall_memory was called in 0 of 3 recorded runs). A
# question that NAMES a person the user has told Zoe about is a question about the
# stored record, whatever its grammar. This half is the PURE matching; resolving the
# known names (a user's `people` rows, their person-fact entities) is
# `person_recall_floor`, because it needs the database.
#
# The match is exact and whole-name, never a substring (the `LIKE %name%` resolver this
# replaces would link "Sam" to "Samantha"):
#   * a multi-word name matches as a contiguous, whole-word phrase, in any case;
#   * a one-word reference (the first name of a multi-word contact, or a contact stored
#     with one name) must be written capitalised ("Dana"), because a lower-case "dana"
#     in typed text is as likely a word as a name; a message with NO capitals past its
#     first letter (a speech-to-text transcript) is treated as caseless, so the voice
#     lane's "how many kids does dana have" still reaches the store;
#   * a first name that two contacts share, or that is an ordinary function word
#     ("will", "may", "who"), names nobody.
PERSON_FLOOR_ENV = "ZOE_PERSON_RECALL_FLOOR"
# A question names at most this many people (bounds the focus read + the packet).
PERSON_FLOOR_MAX_NAMED = 3

_NAME_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")
_POSSESSIVE_TAIL_RE = re.compile(r"['’]s$", re.IGNORECASE)
_NOT_A_NAME = frozenset({
    "a", "an", "and", "are", "am", "any", "as", "at", "be", "but", "by", "can", "could",
    "did", "do", "does", "for", "from", "had", "has", "have", "hey", "how", "i", "if",
    "in", "is", "it", "its", "may", "me", "my", "no", "not", "of", "ok", "okay", "on",
    "or", "our", "please", "shall", "she", "should", "so", "the", "their", "them",
    "then", "there", "they", "this", "that", "to", "us", "was", "we", "were", "what",
    "whats", "when", "where", "which", "who", "whom", "whose", "why", "will", "with",
    "would", "yes", "you", "your", "zoe",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
})


def person_floor_mode() -> str:
    """ZOE_PERSON_RECALL_FLOOR: ``enforce`` (default: unset, ``1``, ``true``...) |
    ``shadow`` (resolve and log ``RECALL_FLOOR ... mode=shadow``, force nothing) |
    ``off`` (``0`` / ``false`` / ``no`` / ``off`` / empty: no lookup at all).
    Per-call env read, the idiom of ``memory_authority.mode``."""
    raw = os.environ.get("ZOE_PERSON_RECALL_FLOOR")
    if raw is None:
        return "enforce"
    v = raw.strip().lower()
    if v in ("0", "false", "no", "off", ""):
        return "off"
    if v == "shadow":
        return "shadow"
    return "enforce"


def _name_tokens(text: str) -> list[tuple[str, bool]]:
    """(casefolded token, written-capitalised) per word; a trailing 's is dropped."""
    out: list[tuple[str, bool]] = []
    for m in _NAME_TOKEN_RE.finditer(text or ""):
        raw = m.group(0)
        out.append((_POSSESSIVE_TAIL_RE.sub("", raw).casefold(), raw[:1].isupper()))
    return out


def _is_caseless(message: str) -> bool:
    """No capital after the first letter: a transcript, or a lazily typed line."""
    letters = [c for c in (message or "") if c.isalpha()]
    return not any(c.isupper() for c in letters[1:])


def is_person_question_sentence(sentence: str) -> bool:
    """A question-shaped sentence that is not a how-to ("how do I add ...")."""
    s = sentence or ""
    if _PROCEDURAL_HOW_RE.match(s):
        return False
    return bool(_QUESTION_SHAPE_RE.match(s)) or s.rstrip().endswith("?")


def person_question_sentences(message: str) -> list[str]:
    """The question-shaped sentences of ``message`` (``event_sentences`` split)."""
    return [s for s in event_sentences(message) if is_person_question_sentence(s)]


def person_names_in_question(message: str, known_names) -> list[str]:
    """The ``known_names`` (one user's own contact / person-entity names) that
    ``message`` names, in order of first mention, at most ``PERSON_FLOOR_MAX_NAMED``.
    Exact whole-name matching, see the block comment above. Pure; ``message`` is the
    sentence (or message) to read; the caller decides which sentences are questions."""
    msg_tokens = _name_tokens(message)
    if not msg_tokens or not known_names:
        return []
    caseless = _is_caseless(message)
    names = [n for n in dict.fromkeys(str(k).strip() for k in known_names) if n]
    folded = {n: [t for t, _ in _name_tokens(n)] for n in names}
    # first-name references: only where exactly one contact starts with that token
    first_owner: dict[str, set[str]] = {}
    for n, toks in folded.items():
        if toks:
            first_owner.setdefault(toks[0], set()).add(n)
    hits: list[tuple[int, str]] = []
    for n, toks in folded.items():
        if not toks:
            continue
        pos = -1
        if len(toks) >= 2:
            for i in range(len(msg_tokens) - len(toks) + 1):
                if [t for t, _ in msg_tokens[i:i + len(toks)]] == toks:
                    pos = i
                    break
        if pos < 0:
            # a one-word contact is that word; a multi-word contact answers to its
            # first name when no other contact shares it
            head = toks[0]
            if (head in _NOT_A_NAME or len(head) < 2
                    or (len(toks) >= 2 and len(first_owner.get(head, ())) != 1)):
                continue
            for i, (t, cap) in enumerate(msg_tokens):
                if t == head and (cap or caseless):
                    pos = i
                    break
        if pos >= 0:
            hits.append((pos, n))
    hits.sort()
    return [n for _, n in hits][:PERSON_FLOOR_MAX_NAMED]


def person_candidate_names(message: str) -> list[str]:
    """Capitalised word runs (up to three words) that could be a person's name: the
    lookup keys for person-fact entities (``slug:<name>``), which have no table to scan.
    Function words and weekdays break a run; a possessive 's ends it. Pure."""
    out: list[str] = []
    run: list[str] = []

    def _flush() -> None:
        if run:
            name = " ".join(run)
            if name not in out:
                out.append(name)
        run.clear()

    for m in _NAME_TOKEN_RE.finditer(message or ""):
        raw = m.group(0)
        word = _POSSESSIVE_TAIL_RE.sub("", raw)
        if raw[:1].isupper() and word.casefold() not in _NOT_A_NAME and len(word) >= 2:
            run.append(word)
            if len(run) == 3 or _POSSESSIVE_TAIL_RE.search(raw):
                _flush()
        else:
            _flush()
    _flush()
    return out[:PERSON_FLOOR_MAX_NAMED + 2]


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
