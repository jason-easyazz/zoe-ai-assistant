"""A weather question that names a place gets a weather answer, and "and in Albany" after a weather answer is the same question (QA round 7).

THE MISSES. (1) "temp in albany" was routed to the people expert by the stage-2 router (a town name reads like a person) and then reached the
brain, which wrote "I can check the temperature in Albany for you." with no data. (2) "and broome" / "and in albany" right after a weather
answer carries no weather word at all: the head said low confidence, the brain had no history of the fast-path answer and answered "I'm
not sure what you mean". Both are deterministic: the words name a place and a weather noun (or follow a weather answer), so the weather
expert answers, never a model.

* ``explicit_text``: a SHORT message with a weather noun (weather / forecast / temp / temperature) and a named place, no second non-weather
  question (that is the brain's), not a cooking temperature -> the message unchanged (the weather planner reads the place).
* ``followup_text``: a bare place follow-up ("and broome", "and in albany", "what about albany?", "how about albany") within ``WINDOW_S`` of a
  weather answer in the same conversation -> "what's the weather in <place>" carrying the previous ask's scope word (today / tonight /
  tomorrow / this weekend).
* ``note`` / ``recent``: the per-conversation memory of the last weather answer (a bounded in-process LRU; a dict write, never I/O).

Pure but for the clock. Never raises into a turn.
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from typing import Optional

WINDOW_S = 600
_MAX = 512
_MAX_WORDS = 12

_NOUN_RE = re.compile(r"\b(?:weather|forecast|temp(?:erature)?)\b")
_FOLLOW_RE = re.compile(
    r"^\W*(?:(?:and|so|ok|okay|then|what\s+about|how\s+about|and\s+what\s+about|what\s+of)\s+)+(?:(?:in|for|at)\s+)?"
    r"(?P<place>[a-z][a-z'. -]{1,30}?)\s*[?.!]*\s*$")
#: a bare "and <words>" that opens with a COMMAND verb is a command, never a place ("and add milk", "and cancel timer")
_IMPERATIVE = (r"add|put|cancel|set|start|stop|play|pause|resume|skip|turn|switch|toggle|dim|open|close|lock|unlock|call|text|send|message|email|"
               r"remind|remember|forget|delete|remove|clear|show|read|tell|ask|find|search|look|check|buy|book|make|create|schedule|move|change|"
               r"rename|save|note|write|list|order|bring|take|give|let|note|record|log|track|mute|unmute|launch|run|post|share|wake|add")
_COMMAND_WORD_RE = re.compile(r"^(?:" + _IMPERATIVE + r"|get|do|is|are|was|were|can|could|will|would|should|have|has|had|did|does|"
                              r"please|thanks|thank|yes|yeah|yep|no|nope|nah|sure|okay|ok|great|good|cool|nice|lovely|perfect|wow|oh|um|uh)\b")
#: a message that OPENS with a command ("remember that the weather in perth was awful", "add weather stripping in perth") is that command's, not a weather ask
_COMMAND_LEAD_RE = re.compile(r"^(?:(?:please|hey|ok|okay|zoe|and|also|just|can you|could you|would you|will you)\s+)*(?:" + _IMPERATIVE + r")\b")
_SCOPE_RE = re.compile(r"\b(today|tonight|tomorrow|this\s+weekend|this\s+week|later)\b")

#: (user id, session id) -> (monotonic time, scope word of the last weather ask)
_last: "OrderedDict[tuple, tuple]" = OrderedDict()


def _key(user_id: str, session_id: str) -> tuple:
    return ((user_id or "").strip(), session_id or "")


def note(user_id: str, session_id: str, text: str, *, now: Optional[float] = None) -> None:
    """Record that ``text`` was just answered as weather in this conversation."""
    k = _key(user_id, session_id)
    m = _SCOPE_RE.search((text or "").lower())
    _last.pop(k, None)
    _last[k] = (time.monotonic() if now is None else now, m.group(1) if m else "")
    while len(_last) > _MAX:
        _last.popitem(last=False)


def recent(user_id: str, session_id: str, *, now: Optional[float] = None) -> Optional[str]:
    """The scope word ("" = none) of a weather answer given within ``WINDOW_S``, else None."""
    hit = _last.get(_key(user_id, session_id))
    if not hit:
        return None
    t = time.monotonic() if now is None else now
    return hit[1] if 0 <= t - hit[0] <= WINDOW_S else None


def reset_state() -> None:
    _last.clear()


def explicit_text(text: str) -> Optional[str]:
    """``text`` itself when it is a short, single weather question naming a place ("temp in albany"), else None. Pure."""
    import weather_speech as ws

    low = re.sub(r"\s+", " ", (text or "").lower().replace("’", "'").strip())
    if not low or len(low.split()) > _MAX_WORDS or not _NOUN_RE.search(low):
        return None
    if ws.is_food_temperature(low) or ws.has_other_question(low) or _COMMAND_LEAD_RE.match(low):
        return None
    # a question about what the person SAID, a stored fact, their own plans or a memory challenge keeps its memory / brain path
    # ("what exactly did i say about the weather in perth?"): the shortcut never outranks the memory shapes
    from memory_gate import is_event_time_question, is_evidence_question, is_memory_challenge, is_own_fact_question

    if is_evidence_question(low) or is_memory_challenge(low) or is_own_fact_question(low) or is_event_time_question(low):
        return None
    return text if ws.weather_places(low) else None


def followup_text(text: str, scope: str) -> Optional[str]:
    """"and in albany" -> "what's the weather in albany <scope>"; None when the words are not a bare place follow-up. Pure."""
    import weather_speech as ws

    low = re.sub(r"\s+", " ", (text or "").lower().replace("’", "'").strip())
    m = _FOLLOW_RE.match(low)
    if not m or len(low.split()) > 7:
        return None
    place = m.group("place").strip(" .'-")
    if _COMMAND_WORD_RE.match(place):
        return None      # "and add milk" / "and cancel timer": the real handlers' turn, never a weather place
    places = ws.weather_places(f"weather in {place}")
    if len(places) != 1 or places[0].lower() != place.lower():
        return None      # a time word, a pronoun, a room or a sentence tail - not a town
    return f"what's the weather in {place}" + (f" {scope}" if scope else "")
