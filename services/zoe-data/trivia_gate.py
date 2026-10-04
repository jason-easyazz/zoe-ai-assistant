"""World-knowledge trivia detector — stdlib only, pure ``str -> bool``.

Two flag-dark callers share it (``zoe_flue_client`` seam):

* ``ZOE_TRIVIA_HEDGE`` — a question with a date / number / winner in the world
  (not about the user's own life, not a live-data tool question) answered from
  the model's head gets a one-line "hedge and offer to check" instruction.
* ``ZOE_VERIFY_ON_CHALLENGE`` (``verify_on_challenge``) — whether the PREVIOUS
  user question was such a world-fact question, i.e. whether "are you sure?" is
  a challenge to a checkable factual claim rather than to a tool result or to
  something Zoe knows about the user.

Deliberately conservative: every positive needs a factual cue (who won / what
year / how many / the capital of …) AND no personal anchor, no live-data cue and
no imperative opener. A miss costs only the hedge; a false positive would hedge
a question that is not trivia, so the tables lean narrow.
"""
from __future__ import annotations

import re

# Who/what/which … about the world with a checkable answer.
_TRIVIA_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(rx, re.IGNORECASE) for rx in (
    # winners, makers, firsts and office holders
    r"\bwho\s+(?:won|wins|win|scored|invented|wrote|directed|painted|discovered|founded|"
    r"created|composed|sang|coached|captained|beat|defeated|came\s+(?:first|second|third)|"
    r"holds\s+the\s+record|was\s+the\s+(?:first|last|youngest|oldest|fastest|greatest))\b",
    r"\bwho(?:['’]s|\s+is|\s+was)\s+the\s+(?:current\s+|first\s+|last\s+)?(?:president|prime\s+minister|"
    r"premier|ceo|captain|king|queen|leader|governor|mayor|coach|author|director|inventor|"
    r"founder|winner|champion|top\s+scorer)\b",
    # when / what year
    r"\b(?:what|which)\s+(?:year|decade|century)\b",
    r"\bwhen\s+(?:did|was|were)\s+(?!i\b|we\b|my\b|our\b)",
    # how many / how tall … is/are/was
    r"\bhow\s+many\s+(?:[\w'’-]+\s+){0,4}?(?:did|has|have|are|were|was|is|does|do|in|times|"
    r"people|goals|points|runs|wickets|titles|medals|games|players|countries|states|moons|"
    r"people|km|miles)\b",
    r"\bhow\s+(?:tall|high|long|far|old|big|fast|deep|heavy|wide|much)\s+(?:is|are|was|were|did)\b",
    # superlatives and reference facts
    r"\bwhat(?:['’]s|\s+is|\s+was|\s+are|\s+were)\s+the\s+(?:capital|population|height|"
    r"record|final\s+score|currency|tallest|largest|biggest|longest|highest|smallest|oldest|"
    r"fastest|deepest|national|official|average|speed|distance|boiling|melting|atomic)\b",
    # which team / country … won / has / hosted
    r"\bwhich\s+(?:team|country|player|city|club|nation|driver|band|album|film|movie|"
    r"horse|school|state|company)\b.*\b(?:won|win|wins|has|have|had|scored|hosted|"
    r"invented|made|signed|was\s+first)\b",
    # an explicit year in a who/what/which question
    r"\b(?:who|what|which)\b[^?]*\b(?:1[5-9]\d\d|20[0-4]\d)\b",
))

# The user's own life — memory/tool turf, never world trivia.
_PERSONAL_RE = re.compile(
    r"\b(?:my|mine|our|ours|myself|ourselves|i|i['’]m|i['’]ve|i['’]d|i['’]ll|we|we['’]re|we['’]ve|us|me)\b",
    re.IGNORECASE,
)
# "tell me / remind me / show me / give me" is a request frame, not a personal anchor.
_REQUEST_ME_RE = re.compile(r"\b(?:tell|remind|show|give|get|let|help)\s+(?:me|us)\b", re.IGNORECASE)
# Live-data questions belong to a tool (weather, clock, traffic, "right now").
_LIVE_RE = re.compile(
    r"\b(?:today|tonight|tomorrow|right\s+now|currently|at\s+the\s+moment|this\s+(?:week|weekend|"
    r"morning|arvo|afternoon|evening)|forecast|weather|traffic|what\s+time|the\s+time)\b",
    re.IGNORECASE,
)
# Imperative openers — a command, not a question.
_COMMAND_RE = re.compile(
    r"^\W*(?:please\s+|hey\s+|ok\s+|okay\s+|zoe[,\s]+)*(?:add|set|remind|play|turn|call|send|create|"
    r"schedule|book|cancel|delete|remove|open|show|start|stop|pause|skip|put|make|write)\b",
    re.IGNORECASE,
)


# Household shapes with NO personal pronoun: "how old is Sarah", "when was Anna born",
# "when did Tom move out", "how many kids does Sarah have", "how far is it from home".
# These must never read as world trivia — the question (with the names in it) would be
# sent to a search provider and the answer hedged as if it were public knowledge.
_HOUSEHOLD_RE = re.compile(
    r"\b(?:home|here|house|our|ours|family|household|neighbou?rs?|kids?|children|mum|mom|dad|"
    r"wife|husband|partner|sister|brother|nan|nana|gran|grandma|grandpa|boyfriend|girlfriend|"
    r"boss|colleagues?|flatmates?|roommates?)\b"
    r"|\bhow\s+old\s+(?:is|was)\s+(?!the\b|this\b|that\b|it\b|earth\b|universe\b|moon\b|sun\b|"
    r"everest\b|mount\b|mt\b)\w+\s*\W*$"
    r"|\bwhen\s+(?:was|were)\s+(?!the\b)\w+(?:\s+\w+)?\s+born\b"
    r"|\bwhen\s+did\s+(?!the\b)\w+\s+(?:move|retire|marry|get\s+(?:married|home|back)|"
    r"come\s+(?:home|back)|leave\s+home)\b"
    r"|\bhow\s+many\s+(?:kids|children|siblings|brothers|sisters|pets|cars|dogs|cats)\s+"
    r"(?:does|do|did|has)\s+\w+\s+(?:have|got)\b",
    re.IGNORECASE,
)
# A capitalised word that is not a public entity is treated as a PERSON/PLACE OF THE
# HOUSEHOLD (fail closed: a missed hedge/verify costs little; a leaked name costs more).
# The allowlist is deliberately small and public: countries, continents, big cities,
# and the words real trivia questions capitalise ("Grand Final", "World Cup").
_PUBLIC_WORDS = frozenset("""
australia australian canada canadian america american usa us uk england english britain british
scotland wales ireland france french germany german italy italian spain spanish portugal japan
japanese china chinese india indian russia brazil mexico argentina egypt greece turkey europe asia
africa antarctica oceania pacific atlantic indian arctic earth moon mars sun jupiter venus saturn
berlin paris london rome madrid tokyo beijing moscow sydney melbourne perth adelaide brisbane
canberra hobart darwin auckland wellington washington york new zealand
mount mt everest eiffel tower wall great barrier reef sahara amazon nile river lake ocean sea
grand final finals world cup olympics olympic games series league premier premiership super bowl
open championship championships tournament grand prix formula one afl nrl nfl nba mlb nhl fifa uefa
nobel prize oscar oscars academy awards emmy grammy bible titanic war century
january february march april may june july august september october november december
monday tuesday wednesday thursday friday saturday sunday
""".split())
_CAP_WORD_RE = re.compile(r"\b[A-Z][a-z]+(?:['’][a-z]+)?\b")


def _has_private_proper_name(msg: str) -> bool:
    """True when ``msg`` holds a capitalised word (other than the opener and "I")
    that is not in the small public-entity allowlist."""
    words = list(_CAP_WORD_RE.finditer(msg))
    for m in words:
        w = m.group(0).lower().replace("\u2019", "'")
        if m.start() == len(msg) - len(msg.lstrip()):
            continue  # sentence-initial capital ("Who", "How")
        if w.endswith("'s"):
            w = w[:-2]
        if w not in _PUBLIC_WORDS:
            return True
    return False


def is_world_trivia(message: str) -> bool:
    """True for a world-knowledge question with a date / number / winner shape and
    no personal signal: not about the user (pronoun anchor), not about the
    household (named people, family words, home/here), not live data, not a
    command. Pure."""
    msg = (message or "").strip()
    if not msg or len(msg) > 240:
        return False
    if _COMMAND_RE.match(msg) or _LIVE_RE.search(msg):
        return False
    if _PERSONAL_RE.search(_REQUEST_ME_RE.sub(" ", msg)):
        return False
    if _HOUSEHOLD_RE.search(msg) or _has_private_proper_name(msg):
        return False
    return any(rx.search(msg) for rx in _TRIVIA_PATTERNS)
