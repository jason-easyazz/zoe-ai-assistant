"""Two timelines on every memory row: when it was TRUE, and when Zoe LEARNED it.

Fidelity audit P2.1 (docs/research/memory-fidelity-audit-2026-10-05.md; the bi-temporal model in
docs/research/people-graph-temporal-model-2026-10-05.md, section 4.2). Pure stdlib + ``date_locale`` (the
household's day-first date order), no I/O, no model call.

The record, all epoch seconds UTC, written on EVERY row (no flag):

* ``added_ts``     (``learned_at``) when Zoe learned it - the transaction timeline, never rewritten.
* ``valid_from``   when it became true. The EVENT time when the person states one ("since 2018", "I moved
                   last March", "for five years"), else the capture time. ``valid_from_basis`` says which
                   (``stated`` / ``captured``); ``valid_from_precision`` (``year`` / ``month`` / ``day``) says
                   how exactly, so "since 2018" is never read back as "1 January 2018".
* ``valid_until``  when the person says it stops being true ("until June"), if they do.
* ``invalid_at``   when it stopped being true: written when a newer fact replaces the row (the successor's
                   ``valid_from``) or the row is archived. The row is KEPT - invalidate, never delete.
* ``expired_at``   when Zoe stopped believing it (the transaction time of that retirement).

An interval is HALF-OPEN: ``[valid_from, end)`` with ``end = min(invalid_at, valid_until)``; at ``end`` the
row is no longer true, so a successor's ``valid_from`` and its predecessor's ``invalid_at`` meet with no gap and
no overlap.

An event time is read ONLY from the person's own words (``parse_validity`` is handed the user's span by the caller,
and the caller refuses a model writer): a model's paraphrase of the user never earns one. A start date in the
future is an intention, not a validity, and is ignored.
"""
from __future__ import annotations

import calendar
import datetime as _dt
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

import date_locale
from memory_captured_at import parse_iso_utc

#: precision of a stated date, coarse to fine
YEAR, MONTH, DAY = "year", "month", "day"
STATED, CAPTURED = "stated", "captured"
#: metadata keys this module owns (the row export and the backfill read them)
KEYS = ("valid_from", "valid_from_basis", "valid_from_precision", "valid_until", "valid_until_precision",
        "invalid_at", "invalid_at_precision", "expired_at")
HISTORY_MAX = 3            # superseded predecessors a history question adds to a recall packet
_DAY = 86400.0


# ── numbers and instants ─────────────────────────────────────────────────────────────

def num(value: Any) -> Optional[float]:
    """A metadata value as epoch seconds, or None (bool / empty / junk are not a time)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        parsed = parse_iso_utc(s)
        return parsed.replace(tzinfo=_dt.timezone.utc).timestamp() if parsed else None


def to_epoch(ts: Any) -> float:
    """An ``as_of`` argument as epoch seconds: epoch number, ISO-8601 string (a naive one is UTC) or datetime
    (a naive one is UTC). ``ValueError`` when it is none of those: a read must never quietly answer "now"."""
    if isinstance(ts, _dt.datetime):
        aware = ts if ts.tzinfo else ts.replace(tzinfo=_dt.timezone.utc)
        return aware.timestamp()
    got = num(ts)
    if got is None:
        raise ValueError("as_of must be an epoch number, an ISO-8601 string or a datetime")
    return got


def _epoch(year: int, month: int = 1, day: int = 1) -> float:
    return float(calendar.timegm((year, month, day, 0, 0, 0)))


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    n = year * 12 + (month - 1) + delta
    return n // 12, n % 12 + 1


# ── a date expression inside free text ───────────────────────────────────────────────

_MONTHS: dict[str, int] = {m.lower(): i + 1 for i, m in enumerate(date_locale.MONTHS)}
_MONTHS.update({m[:3].lower(): i + 1 for i, m in enumerate(date_locale.MONTHS)})
_MONTHS["sept"] = 9
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
          r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_NUMWORDS = dict(zip("a an one two three four five six seven eight nine ten eleven twelve".split(),
                     [1, 1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]))
_NUM = r"(?:\d{1,3}|" + "|".join(sorted(_NUMWORDS, key=len, reverse=True)) + r")"
_UNIT = r"(day|week|month|year)s?"

_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})\b")
_NUMERIC = re.compile(r"\d{1,2}[\x2f.\-]\d{1,2}[\x2f.\-](?:\d{4}|\d{2})\b")
_DAY_MONTH = re.compile(rf"(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+({_MONTH})\b(?:,?\s+(\d{{4}})\b)?", re.I)
_MONTH_YEAR = re.compile(rf"({_MONTH})\b\.?,?\s+(?:of\s+)?(\d{{4}})\b", re.I)
_MONTH_DAY = re.compile(rf"({_MONTH})\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?!\d)(?:,?\s+(\d{{4}})\b)?", re.I)
_REL_MONTH = re.compile(rf"(last|this\s+past|this)\s+({_MONTH})\b", re.I)
_BARE_MONTH = re.compile(rf"({_MONTH})\b", re.I)
_YEAR = re.compile(r"(?:the\s+(?:year|summer|winter|spring|autumn|fall)\s+(?:of\s+)?|early\s+|late\s+|mid-?\s?)?"
                   r"(19\d\d|20\d\d)\b", re.I)
_RELATIVE = re.compile(r"(yesterday|today|last\s+(?:week|month|year)|this\s+year)\b", re.I)
_AGO = re.compile(rf"({_NUM})\s+{_UNIT}\s+ago\b", re.I)


@dataclass(frozen=True)
class DateExpr:
    start: float          # epoch seconds UTC, the first instant of the period named
    end: float            # the first instant AFTER the period (a half-open bound)
    precision: str
    length: int           # characters of the text consumed


def _month_num(word: str) -> int:
    return _MONTHS[word.lower().rstrip(".")]


def _period(precision: str, year: int, month: int = 1, day: int = 1) -> tuple[float, float]:
    if precision == YEAR:
        return _epoch(year), _epoch(year + 1)
    if precision == MONTH:
        ny, nm = _add_months(year, month, 1)
        return _epoch(year, month), _epoch(ny, nm)
    return _epoch(year, month, day), _epoch(year, month, day) + _DAY


def _valid_day(year: int, month: int, day: int) -> bool:
    return 1 <= year <= 9999 and 1 <= month <= 12 and 1 <= day <= calendar.monthrange(year, month)[1]


def _bare_month_year(month: int, now: _dt.datetime, direction: str) -> int:
    """The year of a month named without one: ``past`` = the most recent such month (this one counts),
    ``last`` = strictly before this month, ``future`` = the next one after this month."""
    if direction == "future":
        return now.year if month > now.month else now.year + 1
    if direction == "last":
        return now.year if month < now.month else now.year - 1
    return now.year if month <= now.month else now.year - 1


def parse_date_expr(text: str, now: _dt.datetime, direction: str = "past") -> Optional[DateExpr]:
    """The date expression that BEGINS ``text`` (anchored), or None. Day first for numeric dates (the
    household order, ``date_locale``); a written month is never ambiguous. ``direction`` resolves a month named
    without a year (see ``_bare_month_year``)."""
    s = text.lstrip()
    skipped = len(text) - len(s)

    def out(precision: str, y: int, m: int = 1, d: int = 1, n: int = 0) -> DateExpr:
        a, b = _period(precision, y, m, d)
        return DateExpr(a, b, precision, skipped + n)

    m = _ISO.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return out(DAY, y, mo, d, m.end()) if _valid_day(y, mo, d) else None
    m = _NUMERIC.match(s)
    if m:
        nd = date_locale.parse_numeric_date(m.group(0), purpose="birthday")
        if nd and nd.year and _valid_day(nd.year, nd.month, nd.day):
            return out(DAY, nd.year, nd.month, nd.day, m.end())
        return None
    m = _DAY_MONTH.match(s)
    if m:
        d, mo = int(m.group(1)), _month_num(m.group(2))
        y = int(m.group(3)) if m.group(3) else _bare_month_year(mo, now, direction)
        return out(DAY, y, mo, d, m.end()) if _valid_day(y, mo, d) else None
    m = _MONTH_YEAR.match(s)
    if m:
        return out(MONTH, int(m.group(2)), _month_num(m.group(1)), 1, m.end())
    m = _MONTH_DAY.match(s)
    if m:
        mo, d = _month_num(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else _bare_month_year(mo, now, direction)
        return out(DAY, y, mo, d, m.end()) if _valid_day(y, mo, d) else None
    m = _REL_MONTH.match(s)
    if m:
        mo = _month_num(m.group(2))
        word = m.group(1).lower()
        return out(MONTH, _bare_month_year(mo, now, "last" if word == "last" else "past"), mo, 1, m.end())
    m = _RELATIVE.match(s)
    if m:
        word = re.sub(r"\s+", " ", m.group(1).lower())
        today = _dt.date(now.year, now.month, now.day)
        if word == "today":
            return out(DAY, today.year, today.month, today.day, m.end())
        if word == "yesterday":
            y = today - _dt.timedelta(days=1)
            return out(DAY, y.year, y.month, y.day, m.end())
        if word == "last week":
            w = today - _dt.timedelta(days=7)
            return out(DAY, w.year, w.month, w.day, m.end())
        if word == "last month":
            y, mo = _add_months(now.year, now.month, -1)
            return out(MONTH, y, mo, 1, m.end())
        return out(YEAR, now.year - (1 if word == "last year" else 0), 1, 1, m.end())
    m = _AGO.match(s)
    if m:
        n = _NUMWORDS.get(m.group(1).lower()) or int(m.group(1))
        unit = m.group(2).lower()
        if unit == "year":
            return out(YEAR, now.year - n, 1, 1, m.end())
        if unit == "month":
            y, mo = _add_months(now.year, now.month, -n)
            return out(MONTH, y, mo, 1, m.end())
        back = _dt.date(now.year, now.month, now.day) - _dt.timedelta(days=n * (7 if unit == "week" else 1))
        return out(DAY, back.year, back.month, back.day, m.end())
    m = _YEAR.match(s)
    if m:
        y = int(m.group(1))
        return out(YEAR, y, 1, 1, m.end()) if y <= now.year + 50 else None
    m = _BARE_MONTH.match(s)
    if m:
        mo = _month_num(m.group(1))
        return out(MONTH, _bare_month_year(mo, now, direction), mo, 1, m.end())
    return None


# ── the stated validity of a fact ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Validity:
    """What the person said about WHEN: a start (inclusive) and/or an end (exclusive), epoch seconds UTC."""
    start: Optional[float] = None
    start_precision: str = ""
    end: Optional[float] = None
    end_precision: str = ""

    def __bool__(self) -> bool:
        return self.start is not None or self.end is not None


_NO_VALIDITY = Validity()

# A clause is cut at a semicolon, at "and", "but", "then", "so", and at a comma that opens a new subject
# ("I live in Bendigo, I have been here since 2015"); a comma inside a date ("March 5, 2019") stays.
_CLAUSE_SPLIT = re.compile(
    r"[;]|\s+(?:and|but|then|so|while|although)\s+|,\s*(?:and|but)\s+|,\s+(?=(?:i|we|my|he|she|they|it)\b)", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

_SINCE = re.compile(r"\b(since|from)\s+(?:about\s+|around\s+)?$", re.I)
_UNTIL = re.compile(r"\b(until|till|up\s+to|through|thru|to)\s+$", re.I)
_IN_ON = re.compile(r"\b(?:in|on|during|around|about|back\s+in|early\s+in|late\s+in)\s+$", re.I)
_START_VERB = re.compile(
    r"\b(?:moved|relocated|started|began|begun|joined|got|bought|married|born|retired|graduated|adopted|opened|"
    r"came|arrived|settled|enrolled|founded|built|took|picked|signed|promoted|hired|became|"
    r"have been|has been|had been|been)\b", re.I)
_END_VERB = re.compile(r"\b(?:left|quit|stopped|finished|ended|sold|moved\s+(?:away|out)|gave\s+up|dropped|"
                       r"cancel(?:l)?ed|split)\b", re.I)
_FUTURE = re.compile(r"\b(?:will|won't|going\s+to|gonna|plan(?:ning)?\s+to|want\s+to|wanna|hope\s+to|hoping\s+to|"
                     r"intend\s+to|might|may\s+(?:be|move|start)|next|soon|someday)\b|'ll\b", re.I)
_DURATION = re.compile(
    rf"(?:'ve\b|\bhave\b|\bhas\b|\bhad\s+been\b|\bbeen\b)[^.;]*?\bfor\s+(?:the\s+(?:last|past)\s+)?"
    rf"({_NUM})\s+{_UNIT}\b", re.I)


#: words that carry no topic when a validity phrase is tied to a fact: the verb frame, the anaphora and the cue
#: words of the phrase itself
_FRAME = frozenset("""the and for with from that this these those have has had been being was were are is am
    user here there them they their his her our its you your she him who whom what when where which then than also
    just still now very really only even about around since until till through during
    moved move moving relocated started start began begun joined got get bought married born retired
    left quit stopped finished ended sold gave dropped became came arrived settled""".split())


def _stem(tok: str) -> str:
    t = tok.lower().removesuffix("'s")
    for suf in ("ing", "ed", "es", "s"):
        if len(t) > len(suf) + 2 and t.endswith(suf):
            t = t[: -len(suf)]
            break
    return t[:-1] if len(t) > 3 and t.endswith("e") else t


def _topic(text: str) -> set[str]:
    """Content stems of ``text``: no digits, month names, ordinals or frame words."""
    out: set[str] = set()
    for w in re.findall(r"[A-Za-z][A-Za-z'’\-]*", text or ""):
        lw = w.lower()
        if lw in _MONTHS or lw in _FRAME or len(lw) < 3:
            continue
        out.add(_stem(lw))
    return out


def _bare_lead(clause: str, pos: int) -> bool:
    """A relative expression that needs no preposition: "moved last March", "started yesterday", "3 years ago"."""
    return bool(re.match(rf"(?:last\s|this\s|yesterday|today|{_NUM}\s+{_UNIT}\s+ago)", clause[pos:], re.I)) and (
        pos == 0 or clause[pos - 1].isspace())


def _exprs(clause: str, now: _dt.datetime) -> "list[tuple[str, DateExpr, int, int]]":
    """``(role, expression, start, stop)`` for every date expression in ``clause`` that a cue turns into a START
    ("since", "from", or a started-verb with "in" or a relative date) or an END (``end`` = the first instant of the
    period: "until June"; ``through`` = its last: "through June")."""
    found: list[tuple[str, DateExpr, int, int]] = []
    has_future = bool(_FUTURE.search(clause))
    pos = 0
    while pos < len(clause):
        before = clause[:pos]
        sm, um, im = _SINCE.search(before), _UNTIL.search(before), _IN_ON.search(before)
        if not (sm or um or im or _bare_lead(clause, pos)):
            pos += 1
            continue
        direction = "future" if um and um.group(1).lower() in ("until", "till", "through", "thru") else "past"
        expr = parse_date_expr(clause[pos:], now, direction)
        if expr is None:
            pos += 1
            continue
        role = ""
        if sm:
            role = "start"
        elif um:
            word = re.sub(r"\s+", " ", um.group(1).lower())
            if word != "to" or re.search(r"\b(?:from|between)\b", before):   # a bare "to 2019" is no range end
                role = "through" if word in ("through", "thru") else "end"
        elif not has_future:
            starts, ends = bool(_START_VERB.search(clause)), bool(_END_VERB.search(clause))
            role = "end" if ends and not starts else "start" if starts else ""
        if role:
            found.append((role, expr, pos, pos + expr.length))
        pos += max(expr.length, 1)
    return found


def _clauses(sentence: str) -> list[str]:
    return [c.strip() for c in _CLAUSE_SPLIT.split(sentence) if c and c.strip()]


def _strip_exprs(clause: str, hits: Sequence[Any], dur: Any) -> str:
    """``clause`` without its date expressions and duration phrase (their words are not the topic)."""
    cuts = sorted([(a, b) for _r, _e, a, b in hits] + ([dur.span()] if dur else []))
    out, pos = [], 0
    for a, b in cuts:
        out.append(clause[pos:a])
        pos = max(pos, b)
    out.append(clause[pos:])
    return " ".join(out)


def parse_validity(span: str, fact: str = "", *, now: Optional[_dt.datetime] = None) -> Validity:
    """The validity the person STATED about ``fact`` in their own words ``span`` ("I live in X, I have been here
    since 2015" -> start 2015), or an empty ``Validity``. ``fact`` ties a phrase to the right fact when the span holds
    several ("I live in X since 2015 and I work at Y since 2020" gives each fact its own year); empty = the span IS
    the fact. A start in the future is dropped (an intention, not a validity). ``now`` is the reference for relative
    phrases ("last March": the capture instant). Never raises."""
    try:
        return _parse(span or "", fact or "", now)
    except Exception:  # noqa: BLE001 - a parser bug must cost the event time, never the memory
        return _NO_VALIDITY


def _parse(span: str, fact: str, now: Optional[_dt.datetime]) -> Validity:
    if not span.strip() or len(span) > 4000:
        return _NO_VALIDITY
    if now is None:
        ref = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    else:
        ref = now.astimezone(_dt.timezone.utc).replace(tzinfo=None) if now.tzinfo else now
    now_s = ref.replace(tzinfo=_dt.timezone.utc).timestamp()
    ftok = _topic(fact) if fact.strip() else set()
    start: Optional[tuple[float, str]] = None
    end: Optional[tuple[float, str]] = None
    for sentence in (s for s in _SENTENCE_SPLIT.split(span) if s.strip()):
        clauses = _clauses(sentence)
        for i, clause in enumerate(clauses):
            hits = _exprs(clause, ref)
            dur = _DURATION.search(clause) if not _FUTURE.search(clause) else None
            if not hits and not dur:
                continue
            if ftok:
                own = _topic(_strip_exprs(clause, hits, dur))
                prev = _topic(clauses[i - 1]) if i else set()
                # the phrase belongs to this fact when its clause is about the fact, or is only a pointer back
                # ("... I have been here since 2015") at a clause that is
                if not (own & ftok or (not own and (prev & ftok or i == 0))):
                    continue
            for role, expr, _a, _b in hits:
                if role == "start" and start is None:
                    start = (expr.start, expr.precision)
                elif role in ("end", "through") and end is None:
                    end = (expr.end if role == "through" else expr.start, expr.precision)
            if dur and start is None:
                n = _NUMWORDS.get(dur.group(1).lower()) or int(dur.group(1))
                unit = dur.group(2).lower()
                if unit == "year":
                    start = (_epoch(ref.year - n), YEAR)
                elif unit == "month":
                    y, mo = _add_months(ref.year, ref.month, -n)
                    start = (_epoch(y, mo), MONTH)
                else:
                    d = _dt.date(ref.year, ref.month, ref.day) - _dt.timedelta(days=n * (7 if unit == "week" else 1))
                    start = (_epoch(d.year, d.month, d.day), DAY)
    if start is not None and start[0] > now_s + _DAY:
        start = None                      # "since 2030": an intention, not a validity
    if start is not None and end is not None and end[0] < start[0]:
        end = None                        # "from 2020 until 2019": inconsistent, trust neither end
    if start is None and end is None:
        return _NO_VALIDITY
    return Validity(start[0] if start else None, start[1] if start else "",
                    end[0] if end else None, end[1] if end else "")


# ── a row's interval ─────────────────────────────────────────────────────────────────

def row_start(meta: Mapping[str, Any]) -> Optional[float]:
    """When the row became true: ``valid_from``; for a row written before the stamp was unconditional, the time
    it was learned (``added_ts``, else the ISO ``added_at``). None only when the row carries neither."""
    for key in ("valid_from", "added_ts", "added_at"):
        got = num(meta.get(key))
        if got is not None:
            return got
    return None


def row_end(meta: Mapping[str, Any]) -> Optional[float]:
    """When the row stopped being true (exclusive): the earlier of ``invalid_at`` and ``valid_until``; None = open."""
    ends = [e for e in (num(meta.get("invalid_at")), num(meta.get("valid_until"))) if e is not None]
    return min(ends) if ends else None


def valid_at(meta: Mapping[str, Any], ts: float, *, successor_start: Optional[float] = None) -> bool:
    """Was the row true at ``ts``? Half-open ``[start, end)``. A superseded row that carries no ``invalid_at`` (the
    store before the stamp was unconditional) ends where its successor began (``successor_start``); with no way to
    tell, it is NOT claimed valid - an as-of read never answers from a row it cannot bound."""
    status = str(meta.get("status") or "")
    if status not in ("approved", "superseded"):
        return False
    start, end = row_start(meta), row_end(meta)
    if end is None and status == "superseded":
        end = successor_start
        if end is None:
            return False
    if start is not None and ts < start:
        return False
    return end is None or ts < end


def retire_fields(old: Mapping[str, Any], successor: Optional[Mapping[str, Any]], *, now: float) -> dict[str, Any]:
    """The validity keys for a row a newer fact replaces: ``invalid_at`` = when the successor became true (its
    ``valid_from``; the capture time when it stated none), never before the row began and never after the row's own
    stated ``valid_until``; ``expired_at`` = ``now``, when Zoe stopped believing it. The text is never touched."""
    end = row_start(successor) if successor else None
    end = now if end is None else min(end, now)
    begin = row_start(old)
    if begin is not None and end < begin:
        end = begin
    until = num(old.get("valid_until"))
    if until is not None and until < end:
        end = until
    out: dict[str, Any] = dict(invalid_at=end, expired_at=now)
    prec = str((successor or {}).get("valid_from_precision") or "")
    if prec and str((successor or {}).get("valid_from_basis") or "") == STATED:
        out["invalid_at_precision"] = prec
    return out


def archive_fields(meta: Mapping[str, Any], *, now: float) -> dict[str, Any]:
    """The validity keys for a row that is archived: ``invalid_at`` (kept if already set) and ``expired_at`` = ``now``."""
    out: dict[str, Any] = {}
    if num(meta.get("invalid_at")) is None:
        end = now
        until = num(meta.get("valid_until"))
        if until is not None and until < end:
            end = until
        begin = row_start(meta)
        out["invalid_at"] = max(end, begin) if begin is not None else end
    if num(meta.get("expired_at")) is None:
        out["expired_at"] = now
    return out


#: the keys a retirement writes on the retired row (``retire_fields`` + ``superseded_by_id``); an un-retire drops them
RETIRE_KEYS = ("invalid_at", "invalid_at_precision", "expired_at", "superseded_by_id")


def restore_fields(meta: Mapping[str, Any], *, now: float) -> tuple[dict[str, Any], tuple[str, ...]]:
    """The two-timelines change for a row that was retired WRONGLY (an un-retire): ``(set, drop)``.

    A row retired for a different person's or attribute's fact was never replaced, so it is true again from
    where it began: a new validity interval OPEN-ENDED from the ORIGINAL ``valid_from`` (kept; ``added_ts`` /
    ``added_at`` stand in, marked ``backfill``, for a row that predates the stamp). ``invalid_at`` /
    ``expired_at`` / ``superseded_by_id`` are dropped. A ``valid_until`` the person stated themselves is theirs and
    stays. ``restored_at`` (epoch) says when Zoe took the retirement back. The text and ``added_ts`` are never
    touched: the row was learned when it was learned."""
    out: dict[str, Any] = {"restored_at": now}
    if num(meta.get("valid_from")) is None:
        begin = row_start(meta)
        if begin is not None:
            out["valid_from"], out["valid_from_basis"] = begin, "backfill"
    return out, RETIRE_KEYS


def stamp(validity: Validity, captured: float) -> dict[str, Any]:
    """The keys a NEW row gets: ``valid_from`` (stated start, else ``captured``) and its basis, plus the stated
    end and the precision of each stated date."""
    if validity.start is not None:
        out: dict[str, Any] = dict(valid_from=validity.start, valid_from_basis=STATED,
                                   valid_from_precision=validity.start_precision)
    else:
        out = dict(valid_from=captured, valid_from_basis=CAPTURED)
    if validity.end is not None:
        out["valid_until"] = validity.end
        out["valid_until_precision"] = validity.end_precision
    return out


# ── saying it ────────────────────────────────────────────────────────────────────────

def render(ts: Any, precision: str = "") -> str:
    """An instant as the person would say it at the precision they gave: ``2018``, ``March 2019``, ``5 March 2019``.
    An unknown precision reads at month precision (a capture time is never claimed to the day)."""
    got = num(ts)
    if got is None:
        return ""
    d = _dt.datetime.fromtimestamp(got, _dt.timezone.utc)
    if precision == YEAR:
        return str(d.year)
    if precision == DAY:
        return f"{d.day} {date_locale.MONTHS[d.month - 1]} {d.year}"
    return f"{date_locale.MONTHS[d.month - 1]} {d.year}"


def window_phrase(meta: Mapping[str, Any]) -> str:
    """"from 2018 until March 2024" for a retired row: the start only when the person STATED it (a capture time is
    not when it began), the end whenever known. "" when neither is."""
    parts = []
    if str(meta.get("valid_from_basis") or "") == STATED and num(meta.get("valid_from")) is not None:
        parts.append("from " + render(meta.get("valid_from"), str(meta.get("valid_from_precision") or "")))
    end = row_end(meta)
    if end is not None:
        prec = str(meta.get("invalid_at_precision") or "") if num(meta.get("invalid_at")) == end else \
            str(meta.get("valid_until_precision") or "")
        parts.append("until " + render(end, prec))
    return " ".join(parts)


def before_that(text: str, meta: Mapping[str, Any]) -> str:
    """A superseded fact as a recall-packet line that cannot be mistaken for a current one."""
    win = window_phrase(meta)
    return f"Before that ({win}): {text}" if win else f"Before that: {text}"


# ── "where did I live before?" ───────────────────────────────────────────────────────

#: a history cue, only ever read on a QUESTION (a statement "I used to live in X" is a write, not a read)
_HISTORY_CUE = re.compile(
    r"\bused\s+to\b|\buse\s+to\b|\bpreviously\b|\bformerly\b|\bbefore\b|\bprior\s+to\b|\bback\s+then\b|"
    r"\bin\s+the\s+past\b|\b(?:my|our|the)\s+(?:old|previous|former|prior)\b", re.I)
#: words of the question that name WHEN or HOW it is asked, not WHAT about
_HISTORY_WORDS = frozenset("""before previously formerly used use prior back then past old previous former earlier
    ago did does was were what where who when how which would could tell remember recall know say said""".split())


def is_history_question(text: str) -> bool:
    """True when ``text`` ASKS about how things used to be ("where did I live before?", "what did I used to
    like?", "who was my dentist previously?"): a recall question (``memory_quality.is_recall_question``) with a
    history cue. A statement never is one."""
    raw = (text or "").strip()
    if not raw or not _HISTORY_CUE.search(raw):
        return False
    try:
        from memory_quality import is_recall_question
        return bool(is_recall_question(raw))
    except Exception:  # noqa: BLE001
        return raw.endswith("?")


def history_topic(question: str) -> set[str]:
    """The stems a superseded row must share with a history question to be about the same thing."""
    skip = {_stem(w) for w in _HISTORY_WORDS} | set(_HISTORY_WORDS)
    return {t for t in _topic(question) if t not in skip}


def rank_history(question: str, rows: "Iterable[tuple[str, str, Mapping[str, Any]]]", *,
                 anchor_ids: Iterable[str] = (), entity_id: str = "", limit: int = HISTORY_MAX
                 ) -> "list[tuple[str, str, Mapping[str, Any]]]":
    """The superseded ``(id, text, metadata)`` rows that answer a history question, most relevant first: the
    predecessors of the rows the question already retrieved (``anchor_ids``), then rows that share a topic stem
    with it, newest retirement first. A question with no topic word of its own and no anchor gets nothing: rows
    about no common topic are not the history of this question."""
    want = history_topic(question)
    anchors = set(anchor_ids)
    scored = []
    for rid, text, meta in rows:
        if entity_id and str(meta.get("entity_id") or "") != entity_id:
            continue
        shared = len(want & _topic(text))
        if rid not in anchors and not shared and not entity_id:
            continue
        scored.append(((1 if rid in anchors else 0, shared, num(meta.get("invalid_at")) or 0.0, rid), rid, text, meta))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [(rid, text, meta) for _k, rid, text, meta in scored[:max(0, limit)]]


# ── the backfill plan (dry-run only) ─────────────────────────────────────────────────

def backfill_plan(rows: "Sequence[tuple[str, str, Mapping[str, Any]]]") -> "dict[str, Any]":
    """What a backfill WOULD write to existing rows - a pure function, nothing is applied here. Returns
    ``{"updates": {id: {key: value}}, "counts": {...}}``. Rules (idempotent, metadata only, text and embeddings
    untouched, nothing deleted):

    1. a row without ``valid_from`` gets its learned time (``added_ts``, else ``added_at``), basis ``backfill``;
    2. a ``superseded`` row without ``invalid_at`` ends where its successor began (``superseded_by_id``'s
       ``valid_from`` or learned time; never before the row began); with no findable successor it is left (``unbounded``);
    3. an ``archived`` row without ``invalid_at`` ends when it was archived (``reviewed_at``); with no stamp it is left.

    Stated event times are NOT re-derived from old text here: a row's wording lost the user's phrase, and the source
    excerpt is on only a few rows, so that is a separate, reviewed step (``restated`` counts the user-class rows whose
    excerpt would give one)."""
    by_id = {rid: meta for rid, _t, meta in rows}
    updates: dict[str, dict[str, Any]] = {}
    counts = dict(rows=len(by_id), valid_from=0, invalid_at_superseded=0, invalid_at_archived=0,
                  unbounded=0, restated=0)
    for rid, text, meta in rows:
        up: dict[str, Any] = {}
        begin = row_start(meta)
        if num(meta.get("valid_from")) is None and begin is not None:
            up["valid_from"], up["valid_from_basis"] = begin, "backfill"
            counts["valid_from"] += 1
        status = str(meta.get("status") or "")
        if num(meta.get("invalid_at")) is None and status in ("superseded", "archived"):
            if status == "superseded":
                succ = by_id.get(str(meta.get("superseded_by_id") or ""))
                end = row_start(succ) if succ is not None else None
            else:
                end = num(meta.get("reviewed_at"))
            if end is None:
                counts["unbounded"] += 1
            else:
                up["invalid_at"] = max(end, begin) if begin is not None else end
                counts["invalid_at_superseded" if status == "superseded" else "invalid_at_archived"] += 1
        excerpt = str(meta.get("source_excerpt") or "")
        if (excerpt and status in ("approved", "superseded") and str(meta.get("valid_from_basis") or "") != STATED
                and str(meta.get("authority_class") or "") in ("user_stated", "user_confirmed")):
            at = num(meta.get("added_ts"))
            if at is not None and parse_validity(excerpt, text, now=_dt.datetime.fromtimestamp(at, _dt.timezone.utc)):
                counts["restated"] += 1
        if up:
            updates[rid] = up
    return dict(updates=updates, counts=counts)
