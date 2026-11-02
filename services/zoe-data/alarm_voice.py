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
    if days and re.search(r"\b(?:every|each)\b", text):
        return "days:" + ",".join(str(d) for d in days)
    if re.search(r"\b(?:every\s*day|daily|each\s+day|every\s+morning|every\s+night)\b", text):
        return "daily"
    return "once"


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


def parse(text: str, now: Optional[float] = None) -> Optional[AlarmCommand]:
    now = time.time() if now is None else now
    raw = re.sub(r"[.!?,;:]+$", "", (text or "").lower().strip())
    t = " " + raw + " "
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
    sp = _spoken_times(t)
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
        tomorrow = bool(re.search(r"\btomorrow\b", t))
        if _repeat_of(t) == "once" and _DATE_WORD.search(t):
            return None  # "7 next Monday" / "on Friday" / "on the 5th": a date this grammar cannot honour is refused, not set for the next 7:00
        hhmm, fuzzy = resolve_clock(h, mi, mer, prefer_morning=wake or tomorrow, now=now)
        kind, sid, sname = _sound_of(sp)
        lab = _LABEL.search(t)
        return AlarmCommand("set", time_hhmm=hhmm, repeat=_repeat_of(t), once_date=(datetime.fromtimestamp(now, alarms.zoe_timezone()).date()
                            + timedelta(days=1)) if tomorrow else None, label=(lab.group(1).strip().title() if lab else ""),
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
    if cmd.action == "set":
        if not cmd.time_hhmm:
            return _result(cmd, "What time should I set the alarm for?", "Needs a time")
        a = await alarms.create_alarm(
            db, v.owner_id, time_hhmm=cmd.time_hhmm, repeat=cmd.repeat, once_date=cmd.once_date.isoformat() if cmd.once_date else None,
            label=cmd.label, sound_kind=cmd.sound_kind, sound_id=cmd.sound_id, sound_name=cmd.sound_name)
        spoken = f"Alarm set for {alarms.when_phrase(a['next_fire_at'], now)}"
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
