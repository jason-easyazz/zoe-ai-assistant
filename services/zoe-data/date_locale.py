"""Household date order: ONE place that decides what ``7/8/1991`` means.

The house is in Australia (``ZOE_TIMEZONE`` = ``Australia/Perth``), where a numeric
date is DAY first: ``7/8/1991`` is 7 August 1991. Every reader of a numeric date in
user text (the person/memory extractors, the reminder + calendar grammar, the brain's
turn) goes through this module, so the order cannot drift between them. Before it,
nothing parsed a numeric date at all: the extractors stored the raw ``7/8/1991`` and
the local LLM (trained month-first) rewrote it as "July 8th" downstream.

The order comes from the service's own timezone (``time_utils.zoe_timezone``), not a
second setting: a US zone is month-first, everything else day-first. ``ZOE_DATE_ORDER``
(``dmy`` / ``mdy``) overrides it for a household whose timezone and habit disagree.

* ``7/8/1991``  -> 7 August 1991     (both <= 12: household order decides)
* ``26/10/1985`` -> 26 October 1985  (only one reading is a real date: it always parses)
* ``10/26/1985`` -> 26 October 1985  (a month-first date that cannot be day-first still parses)
* ``2026-10-05`` is ISO and is never touched; written months (``13/January/2022``) keep
  their month.
"""
from __future__ import annotations

import calendar
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
)
_MONTH_NUM = {m.lower(): i + 1 for i, m in enumerate(MONTHS)}
_MONTH_NUM.update({m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)})
_MONTH_NUM["sept"] = 9

_MDY_ZONES = frozenset({
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "America/Phoenix", "America/Anchorage", "America/Boise", "America/Detroit",
    "America/Juneau", "America/Sitka", "America/Nome", "America/Yakutat", "America/Adak",
    "America/Menominee", "Pacific/Honolulu",
})
_MDY_PREFIXES = ("US/", "America/Indiana/", "America/Kentucky/", "America/North_Dakota/")


def date_order() -> str:
    """``"dmy"`` or ``"mdy"`` for the household, read per call (tests flip the env)."""
    forced = (os.environ.get("ZOE_DATE_ORDER") or "").strip().lower()
    if forced in ("dmy", "mdy"):
        return forced
    try:
        from time_utils import zoe_timezone

        key = getattr(zoe_timezone(), "key", "") or ""
    except Exception:
        key = ""
    if key in _MDY_ZONES or key.startswith(_MDY_PREFIXES):
        return "mdy"
    return "dmy"


def day_first() -> bool:
    return date_order() == "dmy"


@dataclass(frozen=True)
class NumericDate:
    day: int
    month: int
    year: Optional[int]
    ambiguous: bool  # both readings were real dates: the household order picked one


def _valid(day: int, month: int, year: Optional[int]) -> bool:
    if not 1 <= month <= 12 or day < 1:
        return False
    # A year-less date may be a leap day; a dated one is checked against its year.
    return day <= calendar.monthrange(year if year else 2000, month)[1]


def _full_year(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    n = int(raw)
    if len(raw) == 4:
        return n
    # Two digits: a birthday-ish pivot. 91 -> 1991, but 05 -> 2005 (never a future year).
    return 2000 + n if n <= date.today().year % 100 else 1900 + n


def resolve_pair(a: int, b: int, year: Optional[int] = None, *,
                 dayfirst: Optional[bool] = None) -> Optional[NumericDate]:
    """Resolve the two leading numbers of ``a/b[/year]`` to a real date, or None.

    Household order first; the other order only when the preferred one is not a date
    (``10/26`` in a day-first house can only be 26 October)."""
    dayfirst = day_first() if dayfirst is None else dayfirst
    day, month = (a, b) if dayfirst else (b, a)
    if _valid(day, month, year):
        swapped_ok = _valid(month, day, year)
        return NumericDate(day, month, year, ambiguous=swapped_ok and day != month)
    if _valid(month, day, year):  # the other order is the only real date
        return NumericDate(month, day, year, ambiguous=False)
    return None


# A numeric date TOKEN inside free text. The lookarounds keep it off versions
# (1.2.3), phone/ID runs, money ("$7/8"), ISO dates (2026-10-05: its first group is
# four digits and the (?<!\d) refuses to start mid-number) and longer slash runs.
_SEP = r"[/.\-]"
_NUM_DATE_RE = re.compile(
    r"(?<![\w/.\-$:])(\d{1,2})(" + _SEP + r")(\d{1,2})(?:\2(\d{4}|\d{2}))?(?![\w/\-]|\.\d|:)"
)
# 13/January/2022, 13-Jan-2022, 13 / March / 2022
_DMONTHY_RE = re.compile(
    r"(?<![\w/.\-$:])(\d{1,2})\s?[/.\-]\s?([A-Za-z]{3,9})\s?[/.\-]\s?(\d{4})(?![\w/\-])"
)


def _token_date(m: "re.Match[str]", *, dayfirst: Optional[bool], strict: bool) -> Optional[NumericDate]:
    a, sep, b, yr = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
    if sep == "." and not (yr and len(yr) == 4):
        return None  # "1.5" / "3.14.15" are numbers and versions, not dates
    if sep == "-" and not yr:
        return None  # "10-12" is a range
    year = _full_year(yr)
    if year is None and strict:
        # Free text: a bare "a/b" is a fraction or a score ("1/2 a cup", "5/20") unless it
        # is a date in the HOUSEHOLD order that cannot be read the other way ("26/10").
        first_is_day = day_first() if dayfirst is None else dayfirst
        day, month = (a, b) if first_is_day else (b, a)
        if not (_valid(day, month, None) and not _valid(month, day, None)):
            return None
    return resolve_pair(a, b, year, dayfirst=dayfirst)


def render_date(day: int, month: int, year: Optional[int] = None) -> str:
    """The unambiguous spoken/stored form: ``7 August 1991`` (year optional)."""
    s = f"{day} {MONTHS[month - 1]}"
    return f"{s} {year}" if year else s


def parse_numeric_date(raw: str, *, dayfirst: Optional[bool] = None) -> Optional[NumericDate]:
    """Parse a string KNOWN to be a date (a birthday value, a reminder due date).

    Accepts ``7/8/1991``, ``7-8-91``, ``7.8.1991``, ``26/10``, ``13/January/2022``.
    ISO (``1991-08-07``) is not handled here — callers parse ISO themselves. Returns
    None when it is not a real date."""
    text = (raw or "").strip()
    m = _DMONTHY_RE.search(text)
    if m:
        month = _MONTH_NUM.get(m.group(2).lower())
        day, year = int(m.group(1)), int(m.group(3))
        if month and _valid(day, month, year):
            return NumericDate(day, month, year, ambiguous=False)
    m = _NUM_DATE_RE.search(text)
    if not m:
        return None
    return _token_date(m, dayfirst=dayfirst, strict=False)


def normalize_numeric_dates(text: str, *, dayfirst: Optional[bool] = None) -> str:
    """Rewrite every numeric date in ``text`` as ``7 August 1991`` — the form no
    model or regex reads two ways. Idempotent; text without a numeric date is
    returned unchanged (same object)."""
    if not text:
        return text

    def _sub_month(m: "re.Match[str]") -> str:
        month = _MONTH_NUM.get(m.group(2).lower())
        day, year = int(m.group(1)), int(m.group(3))
        return render_date(day, month, year) if month and _valid(day, month, year) else m.group(0)

    def _sub_num(m: "re.Match[str]") -> str:
        d = _token_date(m, dayfirst=dayfirst, strict=True)
        return render_date(d.day, d.month, d.year) if d else m.group(0)

    out = _DMONTHY_RE.sub(_sub_month, text)
    return _NUM_DATE_RE.sub(_sub_num, out)


@dataclass(frozen=True)
class FoundDate:
    token: str
    date: NumericDate


def find_numeric_dates(text: str, *, dayfirst: Optional[bool] = None) -> list[FoundDate]:
    """Every numeric date token in ``text`` with its household reading (free-text rules)."""
    out: list[FoundDate] = []
    for m in _NUM_DATE_RE.finditer(text or ""):
        d = _token_date(m, dayfirst=dayfirst, strict=True)
        if d:
            out.append(FoundDate(m.group(0), d))
    return out


def misread_pattern(d: NumericDate) -> Optional["re.Pattern[str]"]:
    """A regex for the WRONG-ORDER reading of an ambiguous date, as a model writes it
    ("July 8th, 1991", "8 July 1991", "1991-07-08"), or None when the date was not
    ambiguous (nothing to mis-read). Used to find stored facts a month-first model
    rewrote before the household order was applied."""
    if not d.ambiguous:
        return None
    wrong_month, wrong_day = d.day, d.month  # day-first date read month-first
    if not _valid(wrong_day, wrong_month, d.year):
        return None
    name = MONTHS[wrong_month - 1]
    mon = rf"(?:{name}|{name[:3]}\.?)"
    ordn = rf"{wrong_day}(?:st|nd|rd|th)?"
    year = rf"(?:,?\s+{d.year})?" if d.year else ""
    parts = [rf"\b{mon}\s+{ordn}\b{year}", rf"\b{ordn}\s+(?:of\s+)?{mon}\b{year}"]
    if d.year:
        parts.append(rf"\b{d.year}-{wrong_month:02d}-{wrong_day:02d}\b")
    return re.compile("|".join(parts), re.IGNORECASE)


# One line for every prompt that shows the local model user text (extractors, the
# nightly digest/consolidation, the NLU) — the model's own default is month-first.
PROMPT_RULE = (
    "Dates written with slashes or dashes are DAY first (DD/MM/YYYY, Australian): "
    "7/8/1991 is 7 August 1991, never July 8. Write dates out in words (7 August 1991)."
)


def day_first_hint(text: str) -> str:
    """A one-line note for the BRAIN's turn when the user's message carries a numeric
    date, else ``""`` (nothing is added to any other turn)."""
    seen = [f"{f.token} = {render_date(f.date.day, f.date.month, f.date.year)}"
            for f in find_numeric_dates(text)]
    if not seen:
        return ""
    return ("[Dates in this message are day-first (Australia, DD/MM/YYYY): "
            + "; ".join(dict.fromkeys(seen)) + ". Say the month by name.]")
