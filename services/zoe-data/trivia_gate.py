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


def is_world_trivia(message: str) -> bool:
    """True for a world-knowledge question with a date / number / winner shape,
    not about the user, not live data, not a command. Pure."""
    msg = (message or "").strip()
    if not msg or len(msg) > 240:
        return False
    if _COMMAND_RE.match(msg) or _LIVE_RE.search(msg):
        return False
    if _PERSONAL_RE.search(_REQUEST_ME_RE.sub(" ", msg)):
        return False
    return any(rx.search(msg) for rx in _TRIVIA_PATTERNS)
