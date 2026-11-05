"""alarm_voice - the spoken grammar for alarms and how Skybridge resolves it (flag-dark ``ZOE_ALARMS``; design:
``docs/architecture/alarms.md`` section "Voice").

``parse(text, now)`` turns an utterance into an ``AlarmCommand`` or None. It is deliberately NARROW, like the timer grammar: a
question or statement ABOUT an alarm ("should I set an alarm?") is never a write, and a phrase that merely contains the word
"alarm" ("the smoke alarm is beeping") is left to the brain. ``resolve(cmd, user_id)`` runs a command against the alarm store and
returns the Skybridge result dict.

Grammar (all case-insensitive, any trailing punctuation):
  set     "set an alarm for 7" / "set my alarm for 6:30 am tomorrow" / "wake me up at seven thirty with music" /
          "alarm for half past six every weekday called gym" / "wake me at 7 with the bell tone"
  list    "what alarms do I have" / "when is my next alarm" / "show my alarms"
  cancel  "cancel my 7 am alarm" / "delete the gym alarm" / "cancel all my alarms"          (deletes)
  off/on  "turn off the alarm" (stops one that is ringing; otherwise switches the matching alarm off) / "turn my 7 am alarm back on"
  snooze  "snooze" / "snooze the alarm" / "snooze for ten minutes" / "five more minutes"        (only meaningful while ringing)
A time with no am/pm means the NEXT such time on the clock ("wake me at 7" is 7 am however late it is at night: a wake request
always prefers the morning).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

import alarms
import command_shapes
import write_receipts

_HOURS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
          "twelve": 12}
_MINS = {"oh five": 5, "oh one": 1, "oh two": 2, "oh three": 3, "oh four": 4, "oh six": 6, "oh seven": 7, "oh eight": 8, "oh nine": 9,
         "ten": 10, "fifteen": 15, "twenty": 20, "twenty five": 25, "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50}
_H = "|".join(_HOURS)
_M = "|".join(sorted(_MINS, key=len, reverse=True))
_WEEKDAY_NAMES = {"monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6, "sunday": 7}

_DAY_ALT = "|".join(_WEEKDAY_NAMES)
_ALARM_WORD = re.compile(r"\balarms?\b|\bwake\s+(?:me|us)\b|\bsnooze\b|\bmore\s+minutes?\b")
_LEAD = r"^\s*(?:(?:please|zoe|hey zoe|ok zoe|okay zoe|can you|could you|would you|will you|and)\s+)*"
_WAKE = re.compile(r"\bwake\s+(?:me|us)(?:\s+up)?\b")
_SNOOZE = re.compile(r"\bsnooze\b|^\s*(?:just\s+)?(\d{1,2}|" + "|".join(_HOURS) + r"|fifteen|twenty|thirty)\s+more\s+minutes?\s*$")
_STOP = re.compile(r"\b(?:(?:turn|switch|shut)\s+off|stop|silence|dismiss|kill)(?:\s+it)?\s+(?:the|my|this|that|our)?\s*alarm\b|"
                   r"\balarm\s+(?:off|stop)\b|\bstop\s+(?:the\s+)?alarm\b")
_ON = re.compile(r"\b(?:turn|switch)\s+(?:on|back\s+on)\s+(?:the|my|our)?\s*(?:[\w:\s]*?)alarm\b|\b(?:turn|switch)\s+(?:the|my|our)\s+"
                 r"[\w:\s]*?alarm\s+(?:back\s+)?on\b|\bre-?enable\b.*\balarm\b")
_OFF_WORD = re.compile(r"\b(?:turn|switch)\s+off\b[\w:\s]*?\balarm\b|\b(?:turn|switch)\s+(?:the|my|our)\b[\w:\s]*?\balarm\s+off\b|\bdisable\b"
                       r"[\w:\s]*?\balarm\b")
_NOT_A_LABEL = frozenset({"my", "the", "all", "every", "other", "this", "that", "our", "next", "ringing", "current", "first", "last", "another",
                          "alarm", "snoozed"})
_SCOPE_QUALIFIER = re.compile(r"\b(?:other|others|except|but|besides|apart|rest|excluding|without|else)\b")
_DATE_WORD = re.compile(r"\b(?:" + "|".join(_WEEKDAY_NAMES) + r"|january|february|march|april|june|july|august|september|october|november|december|"
                        r"next\s+(?:week|month)|the\s+day\s+after|\d{1,2}(?:st|nd|rd|th))\b")
_CANCEL = re.compile(r"\b(?:cancel|delete|remove|clear|get\s+rid\s+of)\b")
_LIST = re.compile(r"\b(?:what|which|list|show|read|tell|when|do\s+i\s+have|how\s+many|any)\b")
_SET = re.compile(r"\b(?:set|make|create|add|start|put|need|want|schedule|wake)\b")
_WITH = re.compile(r"\b(?:with|to|using|playing)\s+(?:some\s+|the\s+|my\s+)?([a-z0-9' &-]{2,60}?)(?:\s+(?:tomorrow|every|each|on|at|for|called|named|labell?ed)\b|$)")
_LABEL = re.compile(r"\b(?:called|named|labell?ed|titled)\s+([a-z0-9' &-]{1,40}?)(?:\s+(?:with|to|tomorrow|every|each|on|at)\b|$)")


@dataclass(frozen=True)
class AlarmCommand:
    action: str  # set | list | cancel | off | on | snooze
    time_hhmm: str = ""
    once_date: Optional[date] = None
    repeat: str = "once"
    label: str = ""
    sound_kind: str = "tone"
    sound_id: str = ""
    sound_name: str = ""
    snooze_minutes: int = 0
    target_hhmm: str = ""  # which alarm a cancel / off / on means, by its time
    target_label: str = ""
    all_alarms: bool = False
    fuzzy_time: bool = False  # the words gave no am/pm
    needs_day: bool = False  # the words named a day the grammar will not guess ("next week"): the resolver asks which day
    day_spec: Optional[tuple] = None  # shift / unshift: the day, as ``date_spec`` read it (resolved against the target alarm's own time)
    shift_minutes: int = 0  # shift: + later / - earlier, relative to that day's time
    reason: str = ""  # shift: "flight" in "I have a flight next Friday" - offered for the calendar, never written without a yes


def _spoken_times(text: str) -> str:
    """Rewrite spoken clock phrases as H:MM so one digit regex finds them ("half past six" -> "6:30")."""
    t = text
    t = re.sub(rf"\bhalf\s+past\s+({_H})\b", lambda m: f"{_HOURS[m.group(1)]}:30", t)
    t = re.sub(rf"\bquarter\s+past\s+({_H})\b", lambda m: f"{_HOURS[m.group(1)]}:15", t)
    t = re.sub(rf"\bquarter\s+(?:to|till)\s+({_H})\b", lambda m: f"{(_HOURS[m.group(1)] - 2) % 12 + 1}:45", t)
    t = re.sub(rf"\b({_H})\s+o'?\s?clock\b", lambda m: f"{_HOURS[m.group(1)]}:00", t)
    t = re.sub(rf"\b({_H})\s+({_M})\b(?!\s+(?:{_H}|\d))", lambda m: f"{_HOURS[m.group(1)]}:{_MINS[m.group(2)]:02d}", t)
    t = re.sub(rf"\b({_H})\b(?=\s*(?:am|pm|a\.m\.|p\.m\.|in the|tomorrow|every|on\b|with\b|using\b|playing\b|called\b|named\b|labell?ed\b|$))", lambda m: str(_HOURS[m.group(1)]), t)
    t = re.sub(r"\ba\.\s?m\.?", "am", t)
    return re.sub(r"\bp\.\s?m\.?", "pm", t)


def _meridiem(text: str, given: str) -> str:
    if given:
        return given[0] + "m"
    if re.search(r"\bnoon\b", text):
        return "noon"
    if re.search(r"\bmidnight\b", text):
        return "midnight"
    if re.search(r"\b(?:in the (?:evening|afternoon)|tonight|at night|this evening|this afternoon)\b", text):
        return "pm"
    if re.search(r"\b(?:in the morning|this morning)\b", text):
        return "am"
    return ""


def _find_time(text: str) -> Optional[tuple[int, int, str]]:
    """(hour, minute, meridiem) from "for 7", "at 6:30 pm", "for 730", "at noon"; None when there is no time."""
    if re.search(r"\bnoon\b", text):
        return 12, 0, "pm"
    if re.search(r"\bmidnight\b", text):
        return 12, 0, "am"
    m = re.search(r"(?:\b(?:at|for|to|my|the)\s+|\balarm\s+|\bme\s+(?:up\s+)?|\bset\s+(?:it\s+)?)(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?!\d|\s*(?:minutes?|mins?|hours?|seconds?)\b)", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
    else:
        m = re.search(r"\b(?:at|for|to)\s+(\d{3,4})\s*(am|pm)?\b", text)  # "wake me at 730"
        if not m:
            return None
        n = int(m.group(1))
        h, mi = n // 100, n % 100
        m_mer = m.group(2) or ""
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            return None
        return h, mi, _meridiem(text, m_mer)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h, mi, _meridiem(text, m.group(3) or "")


def resolve_clock(h: int, mi: int, mer: str, *, prefer_morning: bool, now: float) -> tuple[str, bool]:
    """(HH:MM 24 h, fuzzy). With no am/pm: a wake request takes the morning; otherwise the next occurrence on the clock."""
    if h >= 13 or h == 0:
        return f"{h:02d}:{mi:02d}", False
    if mer == "am":
        return f"{0 if h == 12 else h:02d}:{mi:02d}", False
    if mer == "pm":
        return f"{h if h == 12 else h + 12:02d}:{mi:02d}", False
    am, pm = f"{0 if h == 12 else h:02d}:{mi:02d}", f"{h if h == 12 else h + 12:02d}:{mi:02d}"
    if prefer_morning and h != 12:
        return am, True
    today = datetime.fromtimestamp(now, alarms.zoe_timezone()).date()
    for cand in (am, pm):  # earliest of the two that is still ahead today; both gone = the morning one tomorrow
        if alarms._at(today, cand) > now:
            return cand, True
    return am, True


def _repeat_of(text: str) -> str:
    if re.search(r"\b(?:every\s+week\s?days?|week\s?days?|monday\s+(?:to|through)\s+friday|work\s?days?)\b", text):
        return "weekdays"
    if re.search(r"\b(?:every\s+)?week\s?ends?\b", text):
        return "weekends"
    days = sorted({n for w, n in _WEEKDAY_NAMES.items() if re.search(rf"\b(?:every|each|on)\s+(?:[a-z]+\s+(?:and\s+)?)*?{w}s?\b", text)
                   or re.search(rf"\bevery\s+{w}\b", text)})
    for m in re.finditer(rf"\b({_DAY_ALT})s?\s*(?:through|thru|to|until|till|-)\s*({_DAY_ALT})s?\b", text):  # "tuesday through thursday": inclusive, wraps past sunday
        a, b = _WEEKDAY_NAMES[m.group(1)], _WEEKDAY_NAMES[m.group(2)]
        days = sorted(set(days) | {(a - 1 + i) % 7 + 1 for i in range((b - a) % 7 + 1)})
    if days and (re.search(r"\b(?:every|each)\b", text) or re.search(rf"\b(?:{_DAY_ALT})s\b", text)):  # "on fridays" repeats too
        return "days:" + ",".join(str(d) for d in days)
    if re.search(r"\b(?:every\s*day|daily|each\s+day|every\s+morning|every\s+night)\b", text):
        return "daily"
    return "once"


# ── the DAY of a one-off ("on Friday", "next Monday", "on the 15th", "November 20") ───────────────────────────────────────────────
_MONTH_NUM = {m: i + 1 for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september",
                                                "october", "november", "december"))}
_MONTH_NUM.update({m[:3]: n for m, n in list(_MONTH_NUM.items())})
_MONTH_NUM["sept"] = 9
_MON_ALT = "|".join(sorted(_MONTH_NUM, key=len, reverse=True))
_ORD_UNITS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9}
_ORD_WORDS = dict(_ORD_UNITS, tenth=10, eleventh=11, twelfth=12, thirteenth=13, fourteenth=14, fifteenth=15, sixteenth=16,
                  seventeenth=17, eighteenth=18, nineteenth=19, twentieth=20, thirtieth=30)
_ORD_WORDS.update({f"twenty {w}": 20 + n for w, n in _ORD_UNITS.items()})
_ORD_WORDS.update({f"twenty-{w}": 20 + n for w, n in _ORD_UNITS.items()})
_ORD_WORDS.update({"thirty first": 31, "thirty-first": 31})
_ORD_ALT = r"\d{1,2}(?:st|nd|rd|th)|" + "|".join(sorted(_ORD_WORDS, key=len, reverse=True))
# "november 20" takes a bare number too - never a time ("november at 7" has no day; "20:30" / "20 am" are clock times)
_DAYNUM_ALT = _ORD_ALT + r"|\d{1,2}(?!\s*(?::|am\b|pm\b|o'?clock\b)|\d)"
_DATE_SPECS = (  # (kind, pattern) - tried in order; the first hit names the day
    ("month_day", re.compile(rf"\b(?:on\s+)?(?:the\s+)?({_MON_ALT})\s+(?:the\s+)?({_DAYNUM_ALT})\b")),
    ("day_month", re.compile(rf"\b(?:on\s+)?(?:the\s+)?({_ORD_ALT}|\d{{1,2}})\s+(?:of\s+)?({_MON_ALT})\b")),
    ("nth", re.compile(rf"\b(?:on\s+)?the\s+({_ORD_ALT})\b")),
    ("after_tomorrow", re.compile(r"\b(?:on\s+)?the\s+day\s+after\s+tomorrow\b")),
    ("next_weekday", re.compile(rf"\b(?:on\s+)?next\s+({_DAY_ALT})\b")),
    ("weekday", re.compile(rf"\b(?:on\s+|this\s+(?:coming\s+)?)?({_DAY_ALT})\b(?!s)")),
    ("today", re.compile(r"\b(?:today|tonight|this\s+(?:morning|afternoon|evening))\b")),
)
_VAGUE_DATE = re.compile(r"\bnext\s+(?:week|month|year)\b|\bthe\s+day\s+after\b(?!\s+tomorrow)|\bin\s+(?:a|two|three|\d+)\s+(?:days?|weeks?)\b")


def _ord_value(tok: str) -> int:
    tok = tok.strip()
    m = re.match(r"(\d{1,2})", tok)
    return int(m.group(1)) if m else _ORD_WORDS.get(tok, 0)


def date_spec(text: str) -> tuple[Optional[tuple], str]:
    """(spec, text with the day phrase blanked). ``spec`` is ``(kind, *values)``, ``("vague",)`` for a day this grammar will not
    guess ("next week"), or None when the words name no day. The phrase is blanked so "the 15th" is never read as a 15:00 alarm."""
    if _VAGUE_DATE.search(text):
        return ("vague",), text
    for kind, rx in _DATE_SPECS:
        m = rx.search(text)
        if not m:
            continue
        if kind == "weekday" and re.search(rf"\b(?:every|each)\s+(?:[a-z]+\s+(?:and\s+)?)*?{m.group(1)}\b", text):
            return None, text  # "every friday" is a REPEAT, never a one-off day
        blanked = text[:m.start()] + " " + text[m.end():]
        if kind == "month_day":
            return (kind, _MONTH_NUM[m.group(1)], _ord_value(m.group(2))), blanked
        if kind == "day_month":
            return (kind, _MONTH_NUM[m.group(2)], _ord_value(m.group(1))), blanked
        if kind == "nth":
            return (kind, _ord_value(m.group(1))), blanked
        if kind in ("next_weekday", "weekday"):
            return (kind, _WEEKDAY_NAMES[m.group(1)]), blanked
        return (kind,), blanked
    return None, text


def resolve_date(spec: tuple, hhmm: str, now: float) -> Optional[date]:
    """The household-local day a spec names for an alarm at ``hhmm``, never one whose ``hhmm`` has already gone. None = no such
    day ("February 30"), a vague one, or "today" at a time already past: the caller asks rather than guessing."""
    today = datetime.fromtimestamp(now, alarms.zoe_timezone()).date()

    def ahead(d: date) -> bool:
        return alarms._at(d, hhmm) > now

    kind = spec[0]
    if kind == "today":
        return today if ahead(today) else None
    if kind == "after_today":  # "tomorrow" (the shift grammar)
        return today + timedelta(days=1)
    if kind == "after_tomorrow":
        return today + timedelta(days=2)
    if kind == "weekday":  # "on Friday": this week's (today while the time is still ahead), otherwise next week's
        d = today + timedelta(days=(spec[1] - today.isoweekday()) % 7)
        return d if ahead(d) else d + timedelta(days=7)
    if kind == "next_weekday":  # "next Friday": the coming one, never today - the confirmation names the date, so a slip is heard
        return today + timedelta(days=(spec[1] - today.isoweekday()) % 7 or 7)
    if kind == "nth":
        for k in range(13):
            y, m = today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1
            try:
                d = date(y, m, spec[1])
            except ValueError:
                continue
            if ahead(d):
                return d
        return None
    if kind in ("month_day", "day_month"):
        for y in (today.year, today.year + 1):
            try:
                d = date(y, spec[1], spec[2])
            except ValueError:
                return None
            if ahead(d):
                return d
    return None


def day_phrase(d: date, now: float) -> str:
    """"today" / "tomorrow" / "on Friday the 13th" / "on Friday the 20th of November" (the month once it is not obvious)."""
    today = datetime.fromtimestamp(now, alarms.zoe_timezone()).date()
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    n = d.day
    suf = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    out = f"on {d.strftime('%A')} the {n}{suf}"
    return out if (d.year, d.month) == (today.year, today.month) and (d - today).days < 7 else out + f" of {d.strftime('%B')}"


def _sound_of(text: str) -> tuple[str, str, str]:
    for tid, name in alarms.TONES.items():
        if re.search(rf"\b{tid}\b(?:\s+(?:tone|sound))?", text) and re.search(rf"\b(?:with|to|using)\s+(?:the\s+|a\s+)?{tid}\b", text):
            return "tone", tid, name
    if re.search(r"\b(?:with|to|using)\s+(?:some\s+|the\s+|my\s+)?(?:music|radio|a\s+song|songs|tunes)\b", text):
        return "music", "last", "Music"
    m = _WITH.search(text)
    if m and not re.match(r"^(?:\d|me\b|up\b|sleep|bed|work|school)", m.group(1)) and re.search(r"\b(?:play|playing|with|to)\b", text):
        thing = m.group(1).strip()
        if thing and not re.match(r"^(?:\d|a\s+timer)", thing) and thing.split()[0] not in ("wake", "get", "go", "be", "make"):
            return "music", f"search:{thing}", thing.title()
    return "tone", "", ""


def _target_cmd(action: str, t: str, now: float) -> AlarmCommand:
    """off / on / cancel with the alarm it names: by its time ("my 7 am alarm") or by its label ("the gym alarm", "called gym")."""
    tm = _find_time(_spoken_times(t))
    lab = _LABEL.search(t) or re.search(r"\b(?:the|my)\s+([a-z0-9' ]{2,30}?)\s+alarm\b", t)
    label = lab.group(1).strip() if lab and not tm and lab.group(1).strip() not in _NOT_A_LABEL else ""
    return AlarmCommand(action, target_hhmm=resolve_clock(tm[0], tm[1], tm[2], prefer_morning=True, now=now)[0] if tm else "", target_label=label)


# ── one day's change to an alarm ("get me up an hour earlier next Friday", "never mind about Friday") ─────────────────────────────
_AMOUNT = (r"(half\s+an|an?\s+and\s+a\s+half|an?|one|two|three|four|\d{1,3}|ten|fifteen|twenty|thirty|forty[\s-]five|forty|forty|"
           r"forty-five|fifty)")
_AMOUNT_N = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
             "forty": 40, "forty five": 45, "forty-five": 45, "fifty": 50}
_UNIT = r"(hours?|hrs?|minutes?|mins?)"
_EARLIER = r"(earlier|early|later|late|back|forward)"
_SHIFT_RELATIVE = (
    # "get me up an hour earlier", "wake me 30 minutes early"
    re.compile(rf"\b(?:get|wake)\s+(?:me|us)\s+(?:up\s+)?{_AMOUNT}\s+{_UNIT}\s+{_EARLIER}\b"),
    # "push my alarm back half an hour", "move the alarm earlier by 20 minutes"
    re.compile(rf"\b(?:push|move|bring|shift|put|set)\s+(?:my|the|our)\s+(?:\w+\s+)?alarm\s+{_EARLIER}\s+(?:by\s+)?{_AMOUNT}\s+{_UNIT}"),
    re.compile(rf"\b(?:push|move|bring|shift|put|set)\s+(?:my|the|our)\s+(?:\w+\s+)?alarm\s+(?:by\s+)?{_AMOUNT}\s+{_UNIT}\s+{_EARLIER}\b"),
)
# "I need to be up at 5 on Friday", "get me up at 5 tomorrow" - an absolute time for that day
_SHIFT_ABSOLUTE = re.compile(r"^\s*(?:(?:i|we)\s+)?(?:need|have|got|'ve\s+got)\s+to\s+(?:be\s+up|get\s+up|wake\s+up)\b|\bget\s+(?:me|us)\s+up\s+(?:at|for|by)\b")
_UNSHIFT = re.compile(r"\b(?:never\s*mind|forget)\s+(?:about\s+)?(?:the\s+(?:change|early\s+start|alarm)\s+(?:for|on)\s+)?|"
                      r"\bback\s+to\s+(?:normal|its\s+usual|the\s+usual)\b|"
                      r"\b(?:undo|cancel|remove|drop)\s+(?:the\s+)?(?:change|early\s+start|earlier\s+alarm|override)\b")
_REASON = re.compile(r"\b(?:flight|plane|train|trip|appointment|interview|exam|meeting)\b")


def _amount_minutes(tok: str, unit: str) -> int:
    tok = re.sub(r"\s+", " ", tok.strip())
    if tok == "half an":
        return 30
    if re.match(r"an? and a half", tok):
        return 90
    n = int(tok) if tok.isdigit() else _AMOUNT_N.get(tok, 0)
    return n * 60 if unit.startswith(("hour", "hr")) else n


def _shift_parse(t: str, now: float) -> Optional[AlarmCommand]:
    """A change to ONE day's alarm, or None. Needs a day; "every Friday an hour earlier" is a change to the alarm itself (asked, never
    written as a one-day change). Questions ("should I get up earlier on Friday?") are never a write."""
    rel = next((m for rx in _SHIFT_RELATIVE for m in [rx.search(t)] if m), None)
    unshift = _UNSHIFT.search(t)
    absolute = _SHIFT_ABSOLUTE.search(t)
    if not (rel or unshift or absolute):
        return None
    # Judge the CLAUSE that carries the command: "I have a flight next Friday, can you get me up an hour earlier" is a request whose
    # first clause is context. "never mind" is itself the undo word, so it is not read as a negation of the change.
    hit = rel or absolute or unshift
    clause = re.split(r"[,;]|\s(?:so|and|but)\s", t[:hit.start()])[-1] + t[hit.start():]
    clause = re.split(r"[,;]|\s(?:so|but)\s", clause)[0]
    if absolute and hit is absolute:  # "I have to be up at 5" is the request itself, not "I have ..." talk about the past
        clause = re.sub(r"^\s*(?:i|we)\s+(?:have|need|got|'ve\s+got)\s+to\s+", " ", clause)
    # "never mind" IS the undo word; any other negation ("don't put Friday back to normal") blocks every kind of change
    if command_shapes.asks_or_mentions(clause.strip()) or command_shapes._NEGATED_RE.search(re.sub(r"\bnever\s*mind\b", " ", clause)):
        return None
    if rel and _repeat_of(t) != "once":
        return AlarmCommand("shift_repeat")
    tomorrow = bool(re.search(r"\btomorrow\b", t))
    spec, rest = date_spec(t)
    if spec is None and tomorrow:
        spec, rest = ("after_today",), re.sub(r"\btomorrow\b", " ", t)
    if unshift and not rel and not absolute:
        if spec is None or spec[0] == "vague":
            # "never mind" with no day is not about an alarm at all unless it says so
            return AlarmCommand("unshift", needs_day=True) if _ALARM_WORD.search(t) or "back to normal" in t else None
        return AlarmCommand("unshift", day_spec=spec)
    reason = (_REASON.search(t).group(0) if _REASON.search(t) else "")
    if rel:
        groups = [g for g in rel.groups()]
        direction = next(g for g in groups if g in ("earlier", "early", "later", "late", "back", "forward"))
        amount_tok = next(g for g in groups if g not in (direction,) and not re.match(r"(hours?|hrs?|minutes?|mins?)$", g))
        unit = next(g for g in groups if re.match(r"(hours?|hrs?|minutes?|mins?)$", g))
        mins = _amount_minutes(amount_tok, unit)
        if not 0 < mins <= 12 * 60:
            return None
        sign = -1 if direction in ("earlier", "early", "forward") else 1
        if spec is None or spec[0] == "vague":
            return AlarmCommand("shift", shift_minutes=sign * mins, needs_day=True, reason=reason)
        return AlarmCommand("shift", shift_minutes=sign * mins, day_spec=spec, reason=reason)
    found = _find_time(_spoken_times(rest))
    if not found:
        return None
    if spec is None or spec[0] == "vague":
        return AlarmCommand("shift", time_hhmm="", needs_day=True, reason=reason)
    hhmm, fuzzy = resolve_clock(found[0], found[1], found[2], prefer_morning=True, now=now)
    return AlarmCommand("shift", time_hhmm=hhmm, day_spec=spec, reason=reason, fuzzy_time=fuzzy)



def parse(text: str, now: Optional[float] = None) -> Optional[AlarmCommand]:
    now = time.time() if now is None else now
    raw = re.sub(r"[.!?,;:]+$", "", (text or "").lower().strip())
    t = " " + raw + " "
    if not re.search(r"\btimers?\b|\b(?:smoke|fire|car|burglar|security|door)\s+alarm\b", t):
        shift = _shift_parse(t, now)
        if shift is not None:
            return shift
    if not _ALARM_WORD.search(t) or re.search(r"\b(?:timers?|smoke|fire|car|burglar|security|door)\s+alarm\b|"
                                              r"\balarm\s+(?:code|clock|sound|noise|tone|volume|system|panel|settings?|app)\b", t):
        return None
    if re.search(r"\btimers?\b", t):
        return None
    snooze = _SNOOZE.search(t)
    if snooze and not _SET.search(t.replace("snooze", "")):
        mins = 0
        mm = re.search(rf"\b(\d{{1,2}}|{_H}|fifteen|twenty|thirty)\s+(?:more\s+)?(?:minutes?|mins?)\b", t)
        if mm:
            tok = mm.group(1)
            mins = int(tok) if tok.isdigit() else {**_HOURS, "fifteen": 15, "twenty": 20, "thirty": 30}[tok]
        if command_shapes.asks_or_mentions(t.strip(), destructive=True):
            return None
        if _SCOPE_QUALIFIER.search(t) or re.search(r"\bother\s+than\b|\bbut\s+not\b", t):
            return None  # "snooze all alarms except gym": a scope this grammar cannot honour is never widened or inverted
        lab = _LABEL.search(t) or re.search(r"\b(?:the|my)\s+([a-z0-9' ]{2,30}?)\s+alarm\b", t)
        label = lab.group(1).strip() if lab and lab.group(1).strip() not in _NOT_A_LABEL else ""
        return AlarmCommand("snooze", snooze_minutes=mins, target_label=label)
    # A question or a negation about a CHANGE ("don't turn off the alarm", "can I turn off the alarm", "what if I cancel it") is never a
    # write, and a list word ("what") does not turn it into one.
    writes = bool(_STOP.search(t) or _ON.search(t) or _OFF_WORD.search(t) or _CANCEL.search(t))
    if writes and command_shapes.asks_or_mentions(t.strip(), destructive=True):
        return None
    if writes and _SCOPE_QUALIFIER.search(t):
        return None  # "turn off all alarms except gym" / "every other alarm": a scope this grammar cannot honour is never widened or inverted
    if command_shapes.asks_or_mentions(t.strip()) and not _LIST.search(t):
        return None
    if _STOP.search(t) and not _ON.search(t):
        return _target_cmd("off", t, now)
    if _ON.search(t):
        return _target_cmd("on", t, now)
    if _OFF_WORD.search(t):
        return _target_cmd("off", t, now)
    if _CANCEL.search(t):
        cmd = _target_cmd("cancel", t, now)
        return AlarmCommand("cancel", all_alarms=bool(re.search(r"\b(?:all|every|each)\b", t)) and not cmd.target_hhmm and not cmd.target_label,
                            target_hhmm=cmd.target_hhmm, target_label=cmd.target_label)
    spec, t_wo_date = date_spec(t) if _repeat_of(t) == "once" else (None, t)
    sp = _spoken_times(t_wo_date)
    found = _find_time(sp)
    # A WRITE needs a command shape, not just a time and a cue word: "set (me) an alarm ...", "wake me ...", "I want/need an alarm at ...",
    # or a bare "alarm for 7". "I want to know if my alarm is set for 7" and "is my alarm set for 7" are not on the list.
    imperative = re.match(_LEAD + r"(?:set|make|create|add|schedule|start)\s+(?:me\s+)?(?:(?:an?|my|the|another)\s+)?(?:[a-z]+\s+){0,2}?alarm\b", t)
    wants = re.match(_LEAD + r"(?:i\s+)?(?:want|need|would\s+like|'d\s+like)\s+(?:an?|my)\s+alarm\s+(?:for|at)\b", t)
    bare = re.match(_LEAD + r"(?:an?\s+)?alarm\s+(?:for|at)\s", t)
    wakes = _WAKE.search(t) and re.match(_LEAD + r"(?:i\s+)?(?:want\s+(?:you\s+)?to\s+)?wake\b|\s*(?:i\s+)?(?:need|want)\s+(?:you\s+)?to\s+wake\b", t)
    if found and (imperative or wants or bare or wakes):
        h, mi, mer = found
        wake = bool(_WAKE.search(t))
        once = _repeat_of(t) == "once"
        tomorrow = bool(re.search(r"\btomorrow\b", t)) and not (spec and spec[0] == "after_tomorrow")
        if once and spec is None and _DATE_WORD.search(t):
            return None  # a day word this grammar does not read is refused, never set for the next such time
        named_day = once and spec is not None and spec[0] != "today"
        hhmm, fuzzy = resolve_clock(h, mi, mer, prefer_morning=wake or tomorrow or named_day, now=now)
        kind, sid, sname = _sound_of(sp)
        lab = _LABEL.search(t)
        when: Optional[date] = None
        if once and spec is not None:
            when = resolve_date(spec, hhmm, now)
            if when is None:  # "next week" / "February 30" / "today" at a time already gone: ask, never guess the day
                return AlarmCommand("set", time_hhmm=hhmm, needs_day=True, fuzzy_time=fuzzy)
        elif tomorrow:
            when = datetime.fromtimestamp(now, alarms.zoe_timezone()).date() + timedelta(days=1)
        return AlarmCommand("set", time_hhmm=hhmm, repeat=_repeat_of(t), once_date=when, label=(lab.group(1).strip().title() if lab else ""),
                            sound_kind=kind, sound_id=sid, sound_name=sname, fuzzy_time=fuzzy)
    if _LIST.search(t) and re.search(r"\b(?:my|the|any|our|next|which|what)\s+(?:\w+\s+)?alarms?\b|\balarms\b", t):
        return AlarmCommand("list")
    if imperative:
        return AlarmCommand("set")  # no time heard: the resolver asks for one
    return None


# ── resolving a command against the store ──────────────────────────────────────────────────────────────────────────────────────


def _db():
    from db_pool import get_db_ctx

    return get_db_ctx()


def _viewer(user_id: str) -> Optional[alarms.Viewer]:
    """A member acts as themselves; a guest only at the panel (the household rules); anyone else gets None (sign in)."""
    import panel_principal

    if user_id and not panel_principal.is_guest_id(user_id):
        return alarms.Viewer(user_id=user_id)
    return alarms.Viewer(user_id="", panel=True) if panel_principal.in_panel_turn() else None


def _which(rows: list[dict], cmd: AlarmCommand, v: Optional[alarms.Viewer] = None) -> list[dict]:
    out = rows
    if cmd.target_hhmm:
        out = [a for a in out if a["time_hhmm"] == cmd.target_hhmm]
    if cmd.target_label:
        # A label is private to its owner: the panel can match household labels only, so a spoken name is not a way to probe a member's.
        out = [a for a in out if cmd.target_label in a["label"].lower() and (v is None or a["user_id"] in (alarms.HOUSEHOLD, v.user_id))]
    return out


def _name(a: dict) -> str:
    return alarms.clock_phrase(a["time_hhmm"]) + (f" {a['label']}" if a["label"] else "")


def _result(cmd: AlarmCommand, spoken: str, status: str = "Alarm") -> dict[str, Any]:
    import skybridge_service as sb

    return {"handled": True, "intent": {"domain": "alarm", "action": cmd.action, "list_type": "", "list_name": "", "target_time": cmd.time_hhmm},
            "spoken_summary": spoken, "cards": [sb._status_card("Alarms", spoken, status=status)], "actions": []}


async def resolve(cmd: AlarmCommand, user_id: str) -> dict[str, Any]:
    v = _viewer(user_id)
    if v is None:
        return _result(cmd, "Sign in to use your alarms, or ask me at the panel.", "Sign in")
    try:
        async with _db() as db:
            return await _run(db, cmd, v)
    except alarms.AlarmError as exc:
        return _result(cmd, exc.reason, "Not set")


async def _run(db: Any, cmd: AlarmCommand, v: alarms.Viewer) -> dict[str, Any]:
    now = time.time()
    rows = [a for a in await alarms.list_alarms(db, v) if alarms.can_see(a, v) and (a["user_id"] == alarms.HOUSEHOLD or not v.panel)]
    if cmd.action in ("shift", "unshift", "shift_repeat"):
        return await _run_shift(db, cmd, v, rows, now)
    if cmd.action == "set":
        if not cmd.time_hhmm:
            return _result(cmd, "What time should I set the alarm for?", "Needs a time")
        if cmd.needs_day:
            return _result(cmd, "Which day should I set it for?", "Needs a day")
        a = await alarms.create_alarm(
            db, v.owner_id, time_hhmm=cmd.time_hhmm, repeat=cmd.repeat, once_date=cmd.once_date.isoformat() if cmd.once_date else None,
            label=cmd.label, sound_kind=cmd.sound_kind, sound_id=cmd.sound_id, sound_name=cmd.sound_name)
        # The receipt the write-claim guard checks "I've set your alarm" against (an alarm is a "timer" kind there).
        write_receipts.record(v.user_id or "guest", "timer", a.get("id", ""), "alarm_voice:set")
        spoken = f"Alarm set for {alarms.when_phrase(a['next_fire_at'], now)}"
        if a["repeat_rule"] == "once" and cmd.once_date:  # a named day is confirmed with its date, so a misheard day is caught
            spoken = f"Alarm set for {alarms.clock_phrase(a['time_hhmm'])} {day_phrase(cmd.once_date, now)}"
        spoken += f", {alarms.repeat_phrase(a['repeat_rule'])}." if a["repeat_rule"] != "once" else "."
        if a["sound_kind"] == "music":
            spoken = spoken[:-1] + f", with {a['sound_name'].lower()}."
        return _result(cmd, spoken, "Alarm set")
    if cmd.action == "list":
        live = [a for a in rows if a["enabled"]]
        if not live:
            return _result(cmd, "You have no alarms set.")
        if len(live) == 1:
            return _result(cmd, f"You have one alarm, {alarms.when_phrase(live[0]['next_fire_at'], now)}.")
        nxt = min(live, key=lambda a: a["next_fire_at"] or 1e18)
        return _result(cmd, f"You have {len(live)} alarms. The next is {alarms.when_phrase(nxt['next_fire_at'], now)}.")
    if cmd.action == "snooze":
        live = [a for a in await alarms.ringing(db) if alarms.can_silence(a, v)]
        if cmd.target_label:
            live = _which(live, cmd, v)
            if not live:
                return _result(cmd, "I couldn't find that alarm ringing.")
        if not live:
            return _result(cmd, "No alarm is ringing right now.")
        done = []
        for a in live:
            if a["snooze_count"] >= alarms.MAX_SNOOZES:
                continue
            try:
                done.append(await alarms.snooze(db, a, minutes=cmd.snooze_minutes or None, now=now))
            except alarms.AlarmError:
                continue  # it stopped ringing between the read and the write: nothing to snooze for that one
        if not done and live:
            return _result(cmd, "No alarm is ringing right now.")
        if not done:
            return _result(cmd, "That alarm has been snoozed as much as it can be. Say turn off the alarm.", "Snoozed out")
        mins = round((done[0]["snooze_until"] - now) / 60)
        return _result(cmd, f"Snoozed for {mins} minutes.", "Snoozed")
    if cmd.action == "off":
        # Ending a ring (or a snooze) comes first and honours the named alarm: "turn off my 7 am alarm" while it rings dismisses THAT one.
        live = _which([a for a in await alarms.active(db) if alarms.can_silence(a, v)], cmd, v)
        if live:
            for a in live:
                await alarms.dismiss(db, a, now=now)
            return _result(cmd, "Alarm off.", "Alarm off")
    editable = [a for a in rows if alarms.can_edit(a, v)]
    if cmd.action == "cancel" and cmd.all_alarms:
        for a in editable:
            await alarms.delete_alarm(db, a["id"])
        return _result(cmd, f"Cancelled {len(editable)} alarm{'s' if len(editable) != 1 else ''}." if editable else "You have no alarms to cancel.")
    hits = _which(editable, cmd, v)
    if cmd.action == "off":
        hits = [a for a in hits if a["enabled"]]
    elif cmd.action == "on":
        hits = [a for a in hits if not a["enabled"]]
    if not hits:
        return _result(cmd, "You have no alarms to change." if not editable else "I couldn't find that alarm.")
    if len(hits) > 1:
        return _result(cmd, f"You have {len(hits)} alarms: {', '.join(_name(a) for a in hits[:4])}. Which one?", "Which one?")
    a = hits[0]
    if cmd.action == "cancel":
        await alarms.delete_alarm(db, a["id"])
        return _result(cmd, f"Cancelled your {_name(a)} alarm.", "Cancelled")
    if cmd.action == "off":
        await alarms.update_alarm(db, a, {"enabled": False})
        return _result(cmd, f"Turned off your {_name(a)} alarm.", "Alarm off")
    b = await alarms.update_alarm(db, a, {"enabled": True})
    return _result(cmd, f"Your {_name(a)} alarm is back on, {alarms.when_phrase(b['next_fire_at'], now)}.", "Alarm on")


def _hm_plus(hhmm: str, minutes: int) -> Optional[str]:
    """HH:MM moved by ``minutes``; None when that leaves the day (a one-day change never moves the alarm to another date)."""
    total = int(hhmm[:2]) * 60 + int(hhmm[3:]) + minutes
    return f"{total // 60:02d}:{total % 60:02d}" if 0 <= total < 24 * 60 else None


async def _run_shift(db: Any, cmd: AlarmCommand, v: alarms.Viewer, rows: list[dict], now: float) -> dict[str, Any]:
    """One day's change: resolve the alarm that rings that day (the speaker's own, or a household one), never guess between two."""
    if cmd.action == "shift_repeat":
        return _result(cmd, "That changes the alarm itself, not one day. Say the new time, like 'set my alarm for 5:30 every Friday'.",
                       "Which change?")
    if cmd.needs_day:
        return _result(cmd, "Which day is that for?", "Needs a day")
    mine = [a for a in rows if alarms.can_edit(a, v) and a["enabled"] and (a["user_id"] == v.user_id or a["user_id"] == alarms.HOUSEHOLD)]

    def day_of(a: dict) -> Optional[date]:
        """The day the words name FOR THIS ALARM: by its effective ring that day (an override's time, a skip, already rung), so
        "on Friday" means today while today's (possibly moved) ring is still ahead, and next Friday once it has gone."""
        if not cmd.day_spec:
            return None
        d = resolve_date(cmd.day_spec, "23:59", now)
        if d is None:
            return None
        eff = alarms.effective_time(a, d)
        if eff and alarms._at(d, eff) > now:
            return d
        if cmd.day_spec[0] == "weekday" and d == datetime.fromtimestamp(now, alarms.zoe_timezone()).date():
            return d + timedelta(days=7)
        return d

    if cmd.action == "unshift":
        hits = []
        for a in mine:
            for d_iso in (a.get("overrides") or {}):
                d = date.fromisoformat(d_iso)
                if d == resolve_date(cmd.day_spec, "23:59", now) or d == day_of(a):
                    hits.append((a, d))
        if not hits:
            return _result(cmd, "There's no alarm change for that day to undo.")
        if len(hits) > 1:
            return _result(cmd, f"You have {len(hits)} alarm changes that day. Which alarm?", "Which one?")
        a, d = hits[0]
        await alarms.clear_override(db, a, d.isoformat(), now=now)
        return _result(cmd, f"Back to normal: {alarms.clock_phrase(a['time_hhmm'])} {day_phrase(d, now)}.", "Back to normal")
    hits = [(a, d) for a in mine for d in [day_of(a)] if d is not None and alarms.effective_time(a, d) is not None]
    if not hits:
        if cmd.time_hhmm:  # "I need to be up at 5 on Friday" with no alarm that day: that IS a new one-off
            d = resolve_date(cmd.day_spec, cmd.time_hhmm, now)
            if d is None:
                return _result(cmd, "That time has already passed. Which day?", "Needs a day")
            a = await alarms.create_alarm(db, v.owner_id, time_hhmm=cmd.time_hhmm, repeat="once", once_date=d.isoformat())
            write_receipts.record(v.user_id or "guest", "timer", a.get("id", ""), "alarm_voice:set")
            return _result(cmd, f"Alarm set for {alarms.clock_phrase(cmd.time_hhmm)} {day_phrase(d, now)}." + _offer(cmd), "Alarm set")
        d = resolve_date(cmd.day_spec, "23:59", now)
        when = day_phrase(d, now) if d else "that day"
        return _result(cmd, f"You don't have an alarm {when}. What time do you usually get up that day?", "Needs a time")
    if len(hits) > 1:
        return _result(cmd, f"You have {len(hits)} alarms that day: {', '.join(_name(a) for a, _ in hits[:4])}. Which one?", "Which one?")
    a, d = hits[0]
    was = alarms.effective_time(a, d) or alarms.day_time(a, d)
    new = cmd.time_hhmm or _hm_plus(was, cmd.shift_minutes)
    if new is None:
        return _result(cmd, "That would move it into another day. Tell me the time instead.", "Not changed")
    if new == a["time_hhmm"] and a["repeat_rule"] != "once":
        b = await alarms.clear_override(db, a, d.isoformat(), now=now)
        if b is not None:
            return _result(cmd, f"Back to normal: {alarms.clock_phrase(new)} {day_phrase(d, now)}.", "Back to normal")
    if a["repeat_rule"] == "once":
        await alarms.update_alarm(db, a, {"time_hhmm": new, "once_date": d.isoformat()})
    else:
        await alarms.set_override(db, a, d.isoformat(), time_hhmm=new, now=now)
    write_receipts.record(v.user_id or "guest", "timer", a.get("id", ""), "alarm_voice:shift")
    spoken = f"I'll wake you at {alarms.clock_phrase(new)} {day_phrase(d, now)} instead of {alarms.clock_phrase(was)}."
    return _result(cmd, spoken + _offer(cmd), "Alarm changed")


def _offer(cmd: AlarmCommand) -> str:
    """Offer - never write - the event behind the change ("I have a flight") for the calendar."""
    return f" Want me to put the {cmd.reason} in your calendar?" if cmd.reason else ""


# ── the brain's `alarm` tool (POST /api/system/intent-dispatch: alarm_set / alarm_list) ───────────────────────────────────────

_BRAIN_REQUEST_MAX = 160
_BRAIN_HINT = "say it like 'set an alarm for 6:30 am tomorrow' or 'wake me at 7 every weekday'"


async def run_brain_intent(intent_name: str, slots: dict, user_id: str) -> str:
    """Run the brain's alarm request through THIS grammar and resolver - the same words, the same rules as a spoken one.

    QA 2026-11-05: asked "can you do alarms?", the brain said it had no alarm function, while ``ZOE_ALARMS`` was on and "set an
    alarm for 6:30" worked by voice - the brain simply had no tool. ``alarm_set`` takes the request in words (``request``) and
    acts only when the grammar reads it as a SET: a request the grammar reads as cancel / off / on / snooze is refused here,
    so a model-written phrase can never delete or silence an alarm (those stay on voice and the panel). ``alarm_list`` reads.
    Flag off = 404, like every ``/api/alarms`` route. Returns the spoken line; a refusal is an HTTPException the caller passes
    through, so the brain's tool can say what was wrong."""
    from fastapi import HTTPException

    if not alarms.enabled():
        raise HTTPException(status_code=404, detail="alarms are not switched on in this home")
    if intent_name == "alarm_list":
        return (await resolve(AlarmCommand("list"), user_id))["spoken_summary"]
    request = re.sub(r"\s+", " ", str((slots or {}).get("request") or "")).strip()[:_BRAIN_REQUEST_MAX]
    if request and not _ALARM_WORD.search(" " + request.lower() + " "):
        request = f"set an alarm for {request}"      # the model passed only the time ("6:30 am tomorrow")
    cmd = parse(request) if request else None
    if cmd is None or cmd.action != "set":
        raise HTTPException(status_code=422, detail=f"alarm request not understood as setting an alarm; {_BRAIN_HINT}")
    return (await resolve(cmd, user_id))["spoken_summary"]
