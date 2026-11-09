"""Spoken weather answers that LEAD WITH THE FACT THE USER ASKED FOR.

Pure and deterministic (no I/O, no clock, no globals): one classifier that reads a
weather question into a :class:`WeatherAsk`, and one speaker that turns an ask plus
the provider data (``routers.weather._get_current`` / ``_get_forecast``) into a
short voice reply. Both voice lanes use it so they cannot drift apart:
``skybridge_service._resolve_weather`` (the Skybridge fast path) and
``intent_router._execute_weather_direct`` (the expert/intent lane).

Why it exists: "how hot is it going to get today" used to either miss the weather
lanes entirely (no "weather"/"temperature" keyword) or land on a plain current
reading / a five-day dump, and nothing ever spoke today's high.

Data contract (nothing here invents a field): ``current`` carries ``temp`` and
``description``; ``forecast["today"]`` carries ``high``/``low``/``rain_chance``
(percent, may be None)/``description``; ``forecast["daily"]`` holds the FOLLOWING
days (today is skipped, so ``daily[0]`` is tomorrow) as ``high``/``low``/
``rain_chance``/``description``/``day`` (ISO date).

Voice constraints: whole degrees only ("27", never "27 point 3" — TTS mangles bare
decimals and ``voice_stitch`` works on integer temps), and the plain current reply
keeps the exact ``It's N degrees and <desc> in <city>`` shape ``voice_stitch``
recognises (an optional ", with a top of N today" tail is part of that shape).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

# ── Classifier ────────────────────────────────────────────────────────────────

_SCOPE = (
    r"today|tonight|tomorrow|later(?:\s+today|\s+on)?|"
    r"this\s+(?:morning|afternoon|evening|arvo)"
)
# What may follow the matched question shape. Anything else ("how hot is it in the
# oven", "what's the high score today") means the turn is NOT about the weather.
_TAIL_OK = re.compile(
    rf"^(?:\s+(?:outside|out\s+there|out|{_SCOPE}|right\s+now|now|at\s+the\s+moment"
    r"|for\s+(?:today|tonight|tomorrow)|please))*\s*$"
)
_SCOPE_RE = re.compile(rf"\b(?:{_SCOPE})\b")
_FUTURE_RE = re.compile(r"\b(?:going\s+to|gonna|will)\b")

_HOT = r"hot|warm|cold|cool|chilly"
_RE_HOW = re.compile(
    rf"\bhow\s+({_HOT})\s+"
    r"(?P<verb>is\s+it(?:\s+(?:going|gonna)(?:\s+to)?)?|will\s+it|it(?:'s|\s+is)\s+(?:going|gonna)(?:\s+to)?)"
    r"(?:\s+(?:be|get(?:ting)?))?(?P<tail>.*)$"
)
# "how cold tonight" / "how hot tomorrow": adjective + a scope word, no verb at all.
_RE_HOW_BARE = re.compile(rf"\bhow\s+({_HOT})\s+(?P<tail>(?:{_SCOPE})(?:\s+.*)?)$")
_RE_HIGH_LOW = re.compile(
    r"\bwhat(?:'s|\s+is|\s+will)\s+(?:the\s+)?(?:(?:today|tonight|tomorrow)(?:'s)?\s+)?"
    r"(?P<w>high|low|max(?:imum)?|min(?:imum)?)(?:\s+temp(?:erature)?)?"
    r"(?:\s+(?:for|going\s+to\s+be|will\s+be|be|is))*(?P<tail>.*)$"
)
_RE_TEMP_FUTURE = re.compile(
    r"\bwhat\s+(?:is\s+)?(?:the\s+)?temp(?:erature)?\s+"
    r"(?:is\s+it\s+(?:going\s+to|gonna)|will\s+it|(?:is\s+)?(?:going\s+to|gonna))\s+"
    r"(?:be|get|reach|hit)(?P<tail>.*)$"
)
_RE_WHATS_IT_LIKE = re.compile(
    r"\b(?:what(?:'s|\s+is)|how(?:'s|\s+is))\s+it\s+(?:going|gonna)\s+(?:to\s+)?be(?:\s+like)?(?P<tail>.*)$"
)
_RE_WILL_IT_BE = re.compile(
    r"\b(?:will|is)\s+it\s+(?:going\s+to\s+)?(?:be|get)\s+(?:(?:very|really|pretty|too|quite|a\s+bit)\s+)?"
    r"(?P<w>hot|warm|cold|cool|chilly|sunny|wet|rainy|nice|windy|cloudy)(?P<tail>.*)$"
)
_RE_RAIN = re.compile(r"\b(?:will\s+it|is\s+it\s+(?:going\s+to|gonna)|going\s+to|gonna|chance\s+of|any)\s+rain\b")
_KEYWORD_RE = re.compile(r"\b(?:weather\w*|forecast\w*|temperatures?|rain(?!bow)\w*|windy|winds?)\b")
_INDOORS = (
    r"(?:inside|indoors?|in\s+here|in\s+the\s+house|house|home|room|bedroom|living\s+room|lounge|kitchen|office|bathroom"
    r"|hallway|garage|nursery|study|basement|attic|fridge|freezer|oven|pool|water|body|cpu|thermostat|heater|heating"
    r"|aircon|air\s+con(?:ditioner|ditioning)?|ac|space|area)"
)
_NOT_WEATHER_RE = re.compile(
    # a set / change of a temperature is a thermostat or a light, never a forecast
    r"\b(?:set|change|turn|raise|lower|increase|decrease|make|adjust|put)\b.*\btemp(?:erature)?\b"
    r"|\bcolou?r\s+temp(?:erature)?\b|\bkelvin\b|\b\d{4}\s*k\b"
    rf"|\btemp(?:erature)?\b.*\b{_INDOORS}\b|\b{_INDOORS}\b.*\btemp(?:erature)?\b"
    # a condition ("remind me ... if it rains") or another meaning of the word
    r"|\b(?:if|when|whenever|unless|in\s+case|once)\s+it\s+(?:rains?|is\s+raining|starts\s+raining)\b"
    r"|\bwind\s+(?:down|up|back|it|this|that|the\s+(?:clock|watch|window|car))\b|\bunder\s+the\s+weather\b"
)
_WIDE_TERMS = ("forecast", "week", "next few days")

# A COOKING / food temperature is never the weather (QA 2026-11-08: "what's the safe internal temp" for chicken and the
# follow-up "and what temp" for lamb both got today's forecast). A real weather word (outside, weather, forecast) wins, so
# "what's the temp outside" stays weather; a bare day word does not ("what temp should I cook chicken tonight" is cooking).
_OUTDOOR_WORD_RE = re.compile(r"\b(?:weather|forecast|outside|outdoors?|out\s+there|this\s+week)\b")
_FOOD = (r"chicken|lamb|beef|pork|steak|fish|turkey|mince|sausages?|burgers?|eggs?|rice|salmon|ham|roast|leftovers?|meat|"
         r"casserole|bread|cake|pie|duck|prawns?|bacon|kebabs?|curry|soup|stew")
_FOOD_TEMP_RE = re.compile(
    r"\b(?:internal|core|safe|safely|cooking|roasting|baking|oven|grill|bbq|barbecue|reheat\w*|doneness|done)\s+temp(?:erature)?s?\b"
    r"|\btemp(?:erature)?s?\s+(?:for|to)\s+(?:cook\w*|bak\w*|roast\w*|grill\w*|reheat\w*|serv\w*|stor\w*|keep\w*)\b"
    rf"|\b(?:{_FOOD})\b.*\btemp(?:erature)?s?\b|\btemp(?:erature)?s?\b.*\b(?:{_FOOD})\b"
    r"|\bwhat\s+temp(?:erature)?s?\s+(?:do|should|would|can|could|to)\b"
    r"|\btemp(?:erature)?s?\b.*\b(?:need|needs|should|must)\s+to\s+(?:reach|hit|get\s+to|be\s+(?:cooked|at|heated))\b"
    # a bare follow-up ("and what temp") carries no weather scope at all: it continues the cooking turn before it
    r"|^\W*(?:(?:and|so|ok|okay|but|then)\s+)*(?:what|which)(?:\s+about)?\s+temp(?:erature)?s?\W*$"
)


def is_food_temperature(text: str) -> bool:
    """True when ``text`` asks a cooking / food-safety temperature, not the weather. Pure."""
    low = (text or "").lower().strip()
    return bool(_FOOD_TEMP_RE.search(low)) and not _OUTDOOR_WORD_RE.search(low)


# "weather in Perth" / "what's the forecast for Broome tomorrow" / "weather in bunbury today, we're driving down saturday":
# the place(s) the user NAMED (never the household default). The place is the run of words after in/for/at up to the first
# stop word or punctuation mark, so a trailing clause ("today, were driving down saturday") no longer hides it (QA 6b: the
# old pattern was anchored to the END of the message, so Bunbury / Albany fell back to the household's town).
_NOT_A_PLACE = frozenset((
    "the a an my our your this that it here there town area home house inside outside indoors outdoors case general morning "
    "afternoon evening night day today tomorrow tonight week weekend early late summer winter spring autumn oven fridge freezer "
    "room kitchen garden yard car pool sun shade living front back work school office garage dining laundry balcony patio shed "
    "hall hallway attic basement cellar bedroom bathroom lounge roast bbq dinner lunch breakfast trip holiday picnic walk run "
    "game party wedding event me us you them"
).split())
# words that END a place name: time words, days, pronouns, verbs and joiners that follow it in a sentence
_PLACE_STOP = _NOT_A_PLACE | frozenset((
    "now please at on in for and or but then also plus so if when while because since as is are was were be been am do does did "
    "we i he she they will would can could should going gonna driving flying heading headed visiting want wanted need needed "
    "like thanks thank monday tuesday wednesday thursday friday saturday sunday next last coming moment later soon what how "
    "about with tho though right ok okay too again just"
).split())
_WEATHER_ANCHOR_RE = re.compile(r"\b(?:weather|forecast|temp(?:erature)?|raining|rain|hot|cold|warm|windy|sunny|humid)\b")
_TIME_LEAD_RE = re.compile(
    r"^(?:[\s,;?.!]*(?:for\s+)?(?:the\s+)?(?:today|tomorrow|tonight|now|please|right\s+now|this\s+(?:week|weekend|morning|afternoon|evening)))*")
_PREP_PLACE_RE = re.compile(r"\b(?:in|for|at)\s+(?P<rest>[a-z][^?!.,;:]*)")
_ALSO_RE = re.compile(
    r"^[\s,;?.!]*(?:and|or|plus|then|also)\s+(?:(?:what|how)\s+about\s+)?(?:(?P<prep>in|for|at)\s+)?(?P<rest>[a-z][^?!.,;:]*)")


def _place_words(rest: str) -> tuple[str, str]:
    """(place, remainder) from the words after a preposition: up to 3 tokens, ended by a stop word. ("", rest) when the first
    token is a stop word (``in the oven``, ``in living room``) - that is a room or a time, never a town."""
    toks = rest.split()
    if not toks or toks[0].strip("'.-") in _PLACE_STOP:
        return "", rest
    out = []
    for tok in toks[:3]:
        w = tok.strip("'.-")
        if not w or w in _PLACE_STOP:
            break
        out.append(w)
    if not out:
        return "", rest
    return " ".join(out), " ".join(toks[len(out):])


def weather_places(text: str) -> list[str]:
    """The places a weather question names, in order, at most 3 (``weather in fremantle today? and mandurah`` ->
    ``["Fremantle", "Mandurah"]``); ``[]`` for the household default. Pure."""
    low = re.sub(r"\s+", " ", (text or "").lower().replace("\u2019", "'").strip())
    if not low or is_food_temperature(low):
        return []
    anchor = _WEATHER_ANCHOR_RE.search(low)
    if not anchor:
        return []
    m = _PREP_PLACE_RE.search(low, anchor.start())
    if not m:
        return []
    place, rest = _place_words(m.group("rest"))
    if not place:
        return []
    places = [place.title()]
    after = (rest + low[m.end("rest"):]) if rest else low[m.end("rest"):]
    for _ in range(2):
        after = _TIME_LEAD_RE.sub("", after)
        am = _ALSO_RE.match(after)
        if not am:
            break
        p2, rest2 = _place_words(am.group("rest"))
        after = rest2 + after[am.end():]
        # a bare "and <word>" is a place only when it ENDS the question; "and in <word>" / "what about <word>" is explicit
        explicit = bool(am.group("prep")) or bool(re.search(r"\babout\b", am.group(0)))
        if not p2 or (not explicit and _TIME_LEAD_RE.sub("", rest2).strip(" ?.!,;")):
            break
        if p2.title() not in places:
            places.append(p2.title())
    return places


def weather_place(text: str) -> str:
    """The first place a weather question names ("weather in perth" -> "Perth"), or "" for the household default. Pure."""
    p = weather_places(text)
    return p[0] if p else ""


@dataclass(frozen=True)
class WeatherAsk:
    focus: str  # "current" | "outlook" | "high" | "low" | "rain"
    day: str = ""  # "" | "today" | "tonight" | "tomorrow"
    wide: bool = False  # a forecast / week / next-few-days ask

    @property
    def action(self) -> str:
        """The Skybridge action: forecast (tomorrow+), today (today's outlook), current."""
        if self.day == "tomorrow" or self.wide:
            return "forecast"
        if self.focus != "current" or self.day:
            return "today"
        return "current"


def _day_of(text: str) -> str:
    if "tomorrow" in text:
        return "tomorrow"
    if "tonight" in text:
        return "tonight"
    if _SCOPE_RE.search(text):
        return "today"
    return ""


def _tail_ok(tail: str) -> bool:
    return _TAIL_OK.match(tail or "") is not None


def classify_weather_ask(message: str) -> Optional[WeatherAsk]:
    """Read ``message`` as a weather question, or None when it is not one.

    Scoped on purpose: "how hot is it" needs ``it`` as the subject and only
    time/place scope words after it, so "how hot is the oven" and "what's the high
    score today" are NOT weather. A name or place after the question ("in the
    fridge", "in Perth") also fails the tail check and falls through to the brain.
    """
    text = f" {re.sub(r'[.!?,;:]+$', '', (message or '').lower().strip())} "
    low = text.strip()

    m = _RE_HOW.search(low)
    if m and _tail_ok(m.group("tail")):
        day = _day_of(m.group("tail"))
        future = bool(_FUTURE_RE.search(m.group("verb"))) or bool(day)
        word = m.group(1)
        if not future:
            return WeatherAsk("current", day)
        return WeatherAsk("high" if word in ("hot", "warm") else "low", day or "today")

    m = _RE_HOW_BARE.search(low)
    if m and _tail_ok(" " + m.group("tail")):
        return WeatherAsk("high" if m.group(1) in ("hot", "warm") else "low", _day_of(m.group("tail")))

    m = _RE_HIGH_LOW.search(low)
    if m and _tail_ok(m.group("tail")):
        day = _day_of(low)
        # A bare "what's the high" (nothing after it) is a weather ask too: tail_ok
        # already rules out "high score" / "low battery" by rejecting any other word.
        if day or "temp" in low or not m.group("tail").strip():
            return WeatherAsk("high" if m.group("w") in ("high", "max", "maximum") else "low", day or "today")

    m = _RE_TEMP_FUTURE.search(low)
    if m and _tail_ok(m.group("tail")):
        return WeatherAsk("high", _day_of(low) or "today")

    m = _RE_WILL_IT_BE.search(low)
    if m and _tail_ok(m.group("tail")):
        day = _day_of(m.group("tail"))
        if day:
            word = m.group("w")
            focus = {"hot": "high", "warm": "high", "cold": "low", "cool": "low", "chilly": "low",
                     "wet": "rain", "rainy": "rain"}.get(word, "outlook")
            return WeatherAsk(focus, day)

    m = _RE_WHATS_IT_LIKE.search(low)
    if m and _tail_ok(m.group("tail")):
        day = _day_of(m.group("tail"))
        if day:
            return WeatherAsk("outlook", day)

    # The keyword gate, now carrying what was asked. Whole words only, and never a keyword that is about something
    # else: an indoor / thermostat / colour temperature, "if it rains", "wind down" (HA corpus 2026-10-14: indoor
    # temperature and "set the lamp colour temperature to 2700 K" got the outdoor weather).
    if _KEYWORD_RE.search(text) and not _NOT_WEATHER_RE.search(text) and not is_food_temperature(low):
        day = _day_of(low)
        wide = any(term in low for term in _WIDE_TERMS)
        if _RE_RAIN.search(low):
            return WeatherAsk("rain", day or "today", wide)
        if "temp" in low and _FUTURE_RE.search(low) and day:
            return WeatherAsk("high", day, wide)
        return WeatherAsk("outlook" if day else "current", day, wide)
    return None


# ── Speaker ───────────────────────────────────────────────────────────────────


def _whole(value: Any) -> Optional[int]:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _pct(value: Any) -> Optional[int]:
    n = _whole(value)
    return None if n is None else max(0, min(100, n))


def _day_label(day_iso: Any, tomorrow_iso: Optional[str], first: bool) -> str:
    s = str(day_iso or "")
    if (tomorrow_iso and s == tomorrow_iso) or (not tomorrow_iso and first):
        return "Tomorrow"
    try:
        return date.fromisoformat(s).strftime("%A")
    except ValueError:
        return s or "Later"


def _now_clause(cur_t: Optional[int], desc: str) -> str:
    if cur_t is None:
        return ""
    return f"It's {cur_t} now and {desc}." if desc else f"It's {cur_t} now."


def speak_current(city: str, current: dict, today_high: Optional[int] = None, feels: Optional[int] = None,
                  rain_chance: Optional[int] = None) -> str:
    """``It's N degrees and <desc> in <city>[, with a top of H today][, and a P percent
    chance of rain][, and it feels like F degrees].`` — the exact shape
    ``voice_stitch`` recognises when no rain clause is present."""
    cur_t = _whole(current.get("temp"))
    desc = str(current.get("description") or "").strip()
    if cur_t is None:
        if today_high is not None:
            return f"Today's top is {today_high} degrees in {city}."
        return f"Here is the weather for {city}."
    msg = f"It's {cur_t} degrees and {desc} in {city}" if desc else f"It's {cur_t} degrees in {city}"
    if today_high is not None:
        msg += f", with a top of {today_high} today"
    if rain_chance is not None and rain_chance >= 40:
        msg += f", and a {rain_chance} percent chance of rain"
    if feels is not None:
        msg += f", and it feels like {feels} degrees"
    return msg + "."


def speak_weather(ask: WeatherAsk, current: Optional[dict], forecast: Optional[dict], city: str,
                  feels: Optional[int] = None) -> str:
    """The short spoken answer for ``ask`` — the asked-for fact first."""
    current = current if isinstance(current, dict) else {}
    forecast = forecast if isinstance(forecast, dict) else {}
    today = forecast.get("today") if isinstance(forecast.get("today"), dict) else {}
    daily = [d for d in (forecast.get("daily") or []) if isinstance(d, dict)]
    hi, lo, chance = _whole(today.get("high")), _whole(today.get("low")), _pct(today.get("rain_chance"))
    cur_t = _whole(current.get("temp"))
    desc = str(current.get("description") or "").strip()
    now = _now_clause(cur_t, desc)

    if ask.action == "forecast":
        return _speak_forecast(ask, daily, today, city)

    when = "tonight" if ask.day == "tonight" else "today"
    if ask.focus == "high" and hi is not None:
        return " ".join(p for p in (f"Today's top is {hi} degrees.", now) if p)
    if ask.focus == "low":
        # "Tonight's" low is the early hours of the NEXT day, which is daily[0].
        night_lo = _whole(daily[0].get("low")) if (ask.day == "tonight" and daily) else lo
        if night_lo is not None:
            lead = f"Tonight's low is {night_lo} degrees." if ask.day == "tonight" else f"Today's low is {night_lo} degrees."
            return " ".join(p for p in (lead, now) if p)
    if ask.focus == "rain":
        if chance is not None:
            return f"There's a {chance} percent chance of rain {when}."
        t_desc = str(today.get("description") or "").strip().lower()
        if t_desc:
            wet = any(w in t_desc for w in ("rain", "shower", "drizzle", "thunder", "storm", "sleet", "snow"))
            return f"Rain is likely {when}, with {t_desc}." if wet else f"No rain is expected {when}, just {t_desc}."
    # current / outlook, and the fallback when the asked-for number is not in the data.
    return speak_current(city, current, today_high=hi if ask.day != "tonight" else None, feels=feels,
                         rain_chance=chance if ask.focus == "outlook" else None)


def _speak_forecast(ask: WeatherAsk, daily: list[dict], today: dict, city: str) -> str:
    if not daily:
        return f"I couldn't get the forecast for {city} right now."
    tomorrow_iso: Optional[str] = None
    try:
        tomorrow_iso = (date.fromisoformat(str(today.get("day"))) + timedelta(days=1)).isoformat()
    except ValueError:
        pass
    d0 = daily[0]
    hi, lo, chance = _whole(d0.get("high")), _whole(d0.get("low")), _pct(d0.get("rain_chance"))
    t_desc = str(d0.get("description") or "").strip().lower()
    if ask.day == "tomorrow" and ask.focus in ("high", "low", "rain") and tomorrow_iso and str(d0.get("day")) == tomorrow_iso:
        tail = f" and {t_desc}" if t_desc else ""
        if ask.focus == "high" and hi is not None:
            lo_bit = f", with a low of {lo}" if lo is not None else ""
            return f"Tomorrow's top is {hi} degrees{lo_bit}{tail}."
        if ask.focus == "low" and lo is not None:
            hi_bit = f", with a top of {hi}" if hi is not None else ""
            return f"Tomorrow's low is {lo} degrees{hi_bit}{tail}."
        if ask.focus == "rain" and chance is not None:
            return f"There's a {chance} percent chance of rain tomorrow."
    # Generic: tomorrow only for a tomorrow ask, else the next three days (voice, not a dump).
    days = daily[:1] if (ask.day == "tomorrow" and not ask.wide) else daily[:3]
    parts = []
    for i, d in enumerate(days):
        d_hi, d_lo = _whole(d.get("high")), _whole(d.get("low"))
        d_desc = str(d.get("description") or "").strip().lower()
        bits = []
        if d_hi is not None:
            bits.append(f"a high of {d_hi}")
        if d_lo is not None:
            bits.append(f"a low of {d_lo}")
        if not bits:
            continue
        body = " and ".join(bits) + " degrees" + (f", {d_desc}" if d_desc else "")
        parts.append(f"{_day_label(d.get('day'), tomorrow_iso, i == 0)}, {body}.")
    return " ".join(parts) if parts else f"I couldn't get the forecast for {city} right now."
