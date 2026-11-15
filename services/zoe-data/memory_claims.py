"""Memory claims, the PURE half (memory redesign step 3): the claim shapes a model emits, the fixed attribute vocabulary,
and the value normalisers. Nothing in production calls this module yet.

The redesign (record: docs/knowledge/memory-claims.md once it lands; design note section B) moves the READING of a
correction, a kind statement or an emergency out of regex and word lists and into one constrained model pass. The model
proposes a claim; ``memory_claims_validate`` checks it against the authority (the people graph, the member's stored rows
and their attributes) with no text interpretation of its own. Everything that has to look at characters lives HERE:

* ``parse_output`` / ``parse_claim``: strict parsing of the model's JSON. Unknown fields, wrong types, values outside the
  closed enums or over the length caps make a ``Malformed`` item, never an exception (a malformed claim is an abstain).
* ``ATTRIBUTES``: the B.2 vocabulary. Each attribute maps onto an EXISTING ``structural_claims`` predicate; the slot rule
  and the unit set live here, so the shadow claim reader's retire semantics (``structural_claims.retires``) are unchanged.
* the normalisers: a value as said -> ``Norm(value_norm, unit)`` or ``None`` (abstain). Dates keep only the parts that
  were said (a missing month or year is never guessed), amounts apply a stated scale ("13.5k", "twelve thousand"), ages
  are not birth dates, an hour without am/pm stays unresolved until a stored row supplies it.
* the name helpers the validator needs (hyphen-kept name parts, the lazy-possessive base, whole-token occurrence) and the
  subject forms (member / relation / pronoun / name).

Pure: stdlib + ``people_graph`` / ``structural_claims`` / ``lexicons`` / ``date_locale`` helpers, no I/O, never raises.
"""
from __future__ import annotations

import calendar
import functools
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Union

import people_graph

# -- the closed vocabularies (design note B.1) --------------------------------------------------------------------

CORRECTION, KIND, EMERGENCY = "correction", "kind", "emergency"
CLAIM_TYPES = (CORRECTION, KIND, EMERGENCY)
ABSTAIN_REASONS = ("no_correction", "unsure")
PERSON, ANIMAL = "person", "animal"
KINDS = (PERSON, ANIMAL)
#: ``medical_safety._EVENT_WHAT`` keys
EVENTS = ("fall", "heart", "stroke", "breathing", "bleeding", "worst_headache")
CURRENT, PAST, FUTURE = "current", "past", "future"
EMERGENCY_TIMES = (CURRENT, PAST, "hypothetical", "conditional", "question")
CORRECTION_TIMES = (CURRENT, PAST, FUTURE)
MAX_TEXT = 160
MAX_CLAIMS = 3
#: the twin rows one utterance leaves behind (``value_correction.MAX_ROWS``); more than that is not one fact
MAX_ROWS = 3
#: the writer class every claim edit is made under (``correction_apply.SOURCE``; pinned by a test)
WRITER_CLASS = "conversation_correction"
#: ``medical_safety.detect`` class an emergency quote must carry (D.15)
DETECTOR_CALL = "red_call"

# verdicts, subject-ref kinds, registry roles, row statuses, resolver statuses / tiers
APPLY, AGREE, ABSTAIN, HOLD, REFUSE = "apply", "agree", "abstain", "hold", "refuse"
VERDICTS = (APPLY, AGREE, ABSTAIN, HOLD, REFUSE)
MEMBER, PET, LABEL = "member", "pet", "label"
SUBJECT_KINDS = (MEMBER, PERSON, PET, LABEL)
SUPERSEDED, ASSERTED = "superseded", "asserted"
APPROVED = "approved"
UNIQUE, AMBIGUOUS, NONE = "unique", "ambiguous", "none"
EXACT, TOKEN = "exact", "token"
#: the keys of a subject_ref / a row attribute / an event plan (design note F)
REF_KIND, REF_ID, REF_KEY = "kind", "id", "key"
FIELD_NAMES = (REF_KIND, REF_ID, REF_KEY, "subject_ref", "attribute", "value_norm", "unit", "span", "v", "person", "event",
               "said_at", "label")

LOG_INVALID = "MEMORY_CLAIM_INVALID class=%s verdict=%s rule=%s lane=%s"
LOG_CODES = (LOG_INVALID,)


@dataclass(frozen=True)
class AttributeSpec:
    name: str
    predicate: str          # the structural_claims predicate it maps onto (an existing one: the closed sets are unchanged)
    slot: bool              # one-valued: a new value replaces the old one
    units: frozenset
    needs_old: bool = False  # never supersedes on a restatement alone: only "X, not Y" corrects it


_MEASURE_UNITS = ("km", "kg", "minute", "hour", "second", "day", "week", "month", "year", "metre", "cm", "litre", "mile",
                  "percent")

#: design note B.2. ``origin_date`` / ``amount`` / ``measure`` / ``count`` map onto ``other`` and ``event_date`` /
#: ``time_of_day`` onto ``plan``: the note's "(new)" predicates are NOT added to ``structural_claims.PREDICATES`` because that
#: would change what the live shadow reader keeps and retires; the slot rule is carried here instead.
ATTRIBUTES: dict = {a.name: a for a in (
    AttributeSpec("event_date", "plan", True, frozenset({"date"})),
    AttributeSpec("origin_date", "other", True, frozenset({"date"})),
    AttributeSpec("birthday", "birthday", True, frozenset({"date"})),
    AttributeSpec("age", "age", True, frozenset({"years"})),
    AttributeSpec("amount", "other", True, frozenset({"money"})),
    AttributeSpec("measure", "other", True, frozenset(_MEASURE_UNITS)),
    AttributeSpec("count", "other", True, frozenset({"count"})),
    AttributeSpec("time_of_day", "plan", True, frozenset({"time"})),
    AttributeSpec("schedule", "activity", True, frozenset({"rrule"})),
    AttributeSpec("location", "residence", True, frozenset({"place"})),
    AttributeSpec("employer", "employer", False, frozenset({"org"}), needs_old=True),
    AttributeSpec("partner", "kin:partner", True, frozenset({"person"})),
    AttributeSpec("diet", "preference", False, frozenset({"label"}), needs_old=True),
)}
DATE_ATTRIBUTES = frozenset({"event_date", "origin_date", "birthday"})
UNITS = frozenset(u for a in ATTRIBUTES.values() for u in a.units)

# -- the claim shapes ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CorrectionClaim:
    turn: int
    quote: str
    subject: str
    attribute: str
    new: str
    time: str
    old: Optional[str] = None
    type: str = CORRECTION


@dataclass(frozen=True)
class KindClaim:
    turn: int
    quote: str
    subject: str
    kind: str
    species: Optional[str] = None
    type: str = KIND


@dataclass(frozen=True)
class EmergencyClaim:
    turn: int
    quote: str
    subject: str
    event: str
    time: str
    type: str = EMERGENCY


@dataclass(frozen=True)
class Malformed:
    """A claim (or a whole output) that did not parse. ``reason`` is a code, never the model's text."""
    reason: str
    type: str = ""


ModelClaim = Union[CorrectionClaim, KindClaim, EmergencyClaim]


@dataclass(frozen=True)
class Output:
    """The parsed model output: claims (or ``Malformed`` items) in emission order, the explicit abstain, or a whole-output
    ``error`` code (then ``items`` is empty)."""
    items: tuple = ()
    abstain: Optional[str] = None
    error: str = ""


_STR, _NULLABLE_STR, _INT = "str", "str?", "int"
_SHAPES = {
    CORRECTION: ({"type": _STR, "turn": _INT, "quote": _STR, "subject": _STR, "attribute": _STR, "new": _STR, "time": _STR},
                 {"old": _NULLABLE_STR}),
    KIND: ({"type": _STR, "turn": _INT, "quote": _STR, "subject": _STR, "kind": _STR}, {"species": _NULLABLE_STR}),
    EMERGENCY: ({"type": _STR, "turn": _INT, "quote": _STR, "subject": _STR, "event": _STR, "time": _STR}, {}),
}
_ENUMS = {"attribute": tuple(ATTRIBUTES), "kind": KINDS, "event": EVENTS}


def _typed(value: Any, kind: str) -> bool:
    if kind == _INT:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if kind == _NULLABLE_STR and value is None:
        return True
    return isinstance(value, str) and bool(value.strip()) and len(value) <= MAX_TEXT


def parse_claim(raw: Any) -> Union[ModelClaim, Malformed]:
    """One claim object -> a claim, or ``Malformed(reason)``. Strict: no aliases, no extra keys, no coercion. Never raises."""
    try:
        if not isinstance(raw, Mapping):
            return Malformed("not_an_object")
        ctype = raw.get("type")
        if ctype not in _SHAPES:
            return Malformed("bad_type")
        required, optional = _SHAPES[ctype]
        extra = set(raw) - set(required) - set(optional)
        if extra:
            return Malformed("unknown_field", ctype)
        for k, kind in required.items():
            if k not in raw or not _typed(raw[k], kind):
                return Malformed("bad_" + k, ctype)
        for k, kind in optional.items():
            if k in raw and not _typed(raw[k], kind):
                return Malformed("bad_" + k, ctype)
        for k, allowed in _ENUMS.items():
            if k in required and raw[k] not in allowed:
                return Malformed("bad_" + k, ctype)
        if ctype == CORRECTION:
            if raw["time"] not in CORRECTION_TIMES:
                return Malformed("bad_time", ctype)
            return CorrectionClaim(raw["turn"], raw["quote"], raw["subject"], raw["attribute"], raw["new"], raw["time"],
                                   raw.get("old"))
        if ctype == KIND:
            return KindClaim(raw["turn"], raw["quote"], raw["subject"], raw["kind"], raw.get("species"))
        if raw["time"] not in EMERGENCY_TIMES:
            return Malformed("bad_time", ctype)
        return EmergencyClaim(raw["turn"], raw["quote"], raw["subject"], raw["event"], raw["time"])
    except Exception:  # noqa: BLE001 - a malformed claim is an abstain, never an error
        return Malformed("unparseable")


def parse_output(raw: Any) -> Output:
    """The whole model output ``{"claims": [...], "abstain": null | "no_correction" | "unsure"}`` (a JSON string or an
    object). Never raises."""
    try:
        if isinstance(raw, (str, bytes)):
            raw = json.loads(raw)
        if not isinstance(raw, Mapping) or set(raw) - {"claims", "abstain"} or "claims" not in raw:
            return Output(error="bad_envelope")
        claims, abstain = raw.get("claims"), raw.get("abstain")
        if not isinstance(claims, list) or len(claims) > MAX_CLAIMS:
            return Output(error="bad_claims")
        if abstain is not None and abstain not in ABSTAIN_REASONS:
            return Output(error="bad_abstain")
        return Output(tuple(parse_claim(c) for c in claims), abstain)
    except Exception:  # noqa: BLE001
        return Output(error="unparseable")


def claim_id(user_id: str, turn_id: str, index: int) -> str:
    """``sha256(user_id|turn id|index)[:16]`` (design note B.1)."""
    return hashlib.sha256(f"{user_id}|{turn_id}|{index}".encode("utf-8")).hexdigest()[:16]


def planned_row_id(row_id: str, cid: str) -> str:
    """The id a planned replacement row is known by inside one validation run (the writer assigns the real one)."""
    return f"{row_id}>{cid}"


# -- names and subjects -------------------------------------------------------------------------------------------

MEMBER_FORMS = frozenset({"i", "me", "my", "myself", "user", "the user", "speaker"})
PRONOUNS = frozenset({"she", "he", "they", "it", "her", "him", "them", "his", "hers", "its", "their", "this", "that"})
_LABEL_DROP = frozenset({"a", "an", "the", "my", "our"})
_POSSESSIVE_S = re.compile(r"(?<=\w)['’ʼ]s\b", re.IGNORECASE)


def name_parts(text: str) -> tuple:
    """Whitespace tokens of ``text`` with a hyphen KEPT inside a token ("Ana-Lise Moss" -> ("ana-lise", "moss")) and a
    possessive 's dropped ("Juno's" -> "juno"). ``people_graph.fold_name`` turns "-" into a space, which is how "ana"
    came to match "Ana-Lise" by token (design note D.4); this form never does."""
    s = _POSSESSIVE_S.sub("", unicodedata.normalize("NFKC", str(text or "")))
    out = []
    for raw in s.split():
        pieces = [people_graph.fold_name(p) for p in raw.split("-")]
        pieces = [p.replace(" ", "") for p in pieces if p]
        if pieces:
            out.append("-".join(pieces))
    return tuple(out)


def occurs_in(text: str, phrase: str) -> bool:
    """Does ``phrase`` occur in ``text`` as a run of WHOLE name parts (hyphen kept, possessive dropped)?"""
    t, p = name_parts(text), name_parts(phrase)
    if not p:
        return False
    return any(t[i:i + len(p)] == p for i in range(len(t) - len(p) + 1))


def possessive_base(token: str) -> Optional[str]:
    """The lazy possessive (D.7): "kais" -> "kai". ``None`` when the folded token does not end in a plain "s"."""
    f = people_graph.fold_name(token)
    return f[:-1] if len(f) > 2 and " " not in f and f.endswith("s") else None


def label_key(text: str) -> str:
    """The key a thing subject matches row attributes by: folded, a leading a / an / the / my / our dropped."""
    toks = list(name_parts(text))
    while toks and toks[0] in _LABEL_DROP:
        toks.pop(0)
    return " ".join(toks)


def relation_key(kin: str) -> str:
    return "rel:" + kin


def is_pet_relationship(rel: Optional[str]) -> bool:
    """``correction_apply._make_pet`` marks a pet ``pet`` or ``pet <kind>``; anything else (empty included) is a person."""
    r = str(rel or "").strip().casefold()
    return r == "pet" or r.startswith("pet ")


@dataclass(frozen=True)
class SubjectForm:
    form: str        # member | relation | pronoun | name | unknown
    value: str = ""  # the kin code for a relation, the subject as said for a name


def subject_form(subject: str) -> SubjectForm:
    """How a subject as said is resolved (design note B.3). ``my <kin word>`` is a relation only for a word the lexicon
    knows (``lexicons.kin_code``); every other ``my X`` is a name / thing."""
    f = people_graph.fold_name(subject)
    if not f:
        return SubjectForm("unknown")
    if f in MEMBER_FORMS:
        return SubjectForm(MEMBER)
    toks = f.split()
    if len(toks) == 1 and toks[0] in PRONOUNS:
        return SubjectForm("pronoun")
    if len(toks) == 2 and toks[0] in ("my", "our"):
        import lexicons

        code = lexicons.kin_code(toks[1])
        if code:
            return SubjectForm("relation", code)
    return SubjectForm("name", str(subject).strip())


# -- the value normalisers ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Norm:
    value: dict
    unit: str
    scale: int = 1               # an amount's stated scale (1000 for "13.5k"), 1 when none was said
    stated: bool = True          # an amount's currency was said (False = the AUD default)
    alts: tuple = ()             # other readings an OLD value may also name ("12" beside "13.5k": 12 or 12000)

    def readings(self) -> tuple:
        return (self.value, *self.alts)


#: a number may start with its decimal point (".50" is 0.50: dropping the point read "$.50" as 50)
_TOKEN_RE = re.compile(r"\d+(?:[.,:]\d+)*|\.\d+|[^\W\d_]+|[$€£%]")
_AMPM_RE = re.compile(r"\b([ap])\.\s?m\.?", re.IGNORECASE)
_DECIMAL_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+")


#: a NEGATIVE value, which no stored attribute holds: a minus (hyphen, U+2212, an en / em dash used as one) that does not
#: follow a word character, then optionally a currency marker ("-$50", "-US$50", "−$50", "–$50"), then the digits or a
#: leading decimal point; and an accounting negative "($50)" / "(50)". A range or a hyphenated word ("10-$50",
#: "pay-$50") has a word character before the dash and is NOT read as a sign ("$-50" is two tokens and abstains anyway).
_SIGN = r"[-\u2212\u2013\u2014]"
_CUR_MARK = r"(?:(?:us|nz|au|a)?\s*[$€£]|aud|usd|nzd|eur|gbp)"
_NEGATIVE_RE = re.compile(rf"(?<![\w.]){_SIGN}\s*(?:{_CUR_MARK}\s*)?\.?\d"
                          rf"|\(\s*(?:{_CUR_MARK}\s*)?\.?\d[\d.,]*\s*(?:{_CUR_MARK}\s*)?\)", re.IGNORECASE)


#: currency / symbol characters a NUMERIC normaliser understands; any other symbol ("¥500", "~5", "+5", "±2", "5°") is a
#: meaning the tokeniser would silently drop, so the value abstains
_KNOWN_SYMBOLS = frozenset("$€£")
#: any minus form that does not join two word characters ("-mondays", "- 5", "5 - 10"): a numeric value never carries
#: one; "twenty-one", "2026-12-19" and "10-$50" (a range, which abstains on its own) have a word character before it
_LOOSE_MINUS_RE = re.compile(r"(?<![\w.])[-\u2212\u2013\u2014]")


def raw_unsafe(text: Any) -> bool:
    """Does the RAW text carry something the tokeniser would drop before a normaliser could see it - a sign (any minus
    form, before or after a currency marker, or an accounting "( )"), or a symbol it has no reading for? Checked on the
    whole input by every numeric normaliser (``_numeric``) BEFORE tokenising: three review rounds found the same class
    because each guard sat inside one normaliser and the tokens another normaliser was handed had lost the sign."""
    s = unicodedata.normalize("NFKC", str(text or ""))
    if _NEGATIVE_RE.search(s) or _LOOSE_MINUS_RE.search(s):
        return True
    return any(unicodedata.category(c)[0] == "S" and c not in _KNOWN_SYMBOLS for c in s)


def _numeric(fn):
    """Every numeric normaliser runs behind ``raw_unsafe`` on its own raw input."""
    @functools.wraps(fn)
    def guarded(text, *args, **kwargs):
        if raw_unsafe(text):
            return None
        return fn(text, *args, **kwargs)
    return guarded


def _toks(text: str) -> list:
    s = unicodedata.normalize("NFKC", str(text or "")).casefold().replace("’", "'")
    return _TOKEN_RE.findall(_AMPM_RE.sub(r"\1m", s))


def _dec(tok: str) -> Optional[Decimal]:
    if not _DECIMAL_RE.fullmatch(tok or ""):
        return None
    try:
        return Decimal(tok.replace(",", ""))
    except InvalidOperation:
        return None


_SMALL = {w: i for i, w in enumerate(("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                       "fourteen fifteen sixteen seventeen eighteen nineteen").split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}
_NUMBER_WORDS = frozenset(_SMALL) | frozenset(_TENS) | frozenset(_SCALES) | {"hundred", "and", "a"}


def _words_number(toks: list) -> Optional[int]:
    """Spelled numbers by the standard accumulator: units / teens / tens add to ``current``, "hundred" multiplies it,
    thousand / million / billion move ``max(current, 1) * scale`` into ``total`` ("one million five hundred thousand" is
    1,500,000; "a hundred and five" is 105; "twenty-one" arrives as two tokens). Anything it cannot read with certainty -
    a stray word, two numbers side by side ("five six", "twenty thirty"), a scale out of order ("thousand million"),
    "zero" inside a number, a trailing "and" - is ``None``."""
    if not toks or ("zero" in toks and len(toks) > 1):
        return None
    total = cur = 0
    prev, last_scale = "", None
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if t == "a":
            if i != 0 or nxt not in ("hundred", *_SCALES):
                return None
        elif t == "and":
            if prev not in ("hundred", *_SCALES) or not (nxt in _SMALL or nxt in _TENS):
                return None
        elif t in _SMALL:
            if prev in _SMALL or (prev in _TENS and not 1 <= _SMALL[t] <= 9) or prev == "a":
                return None
            cur += _SMALL[t]
        elif t in _TENS:
            if prev in _SMALL or prev in _TENS or prev == "a" or cur % 100:
                return None
            cur += _TENS[t]
        elif t == "hundred":
            if not (prev == "a" or (prev in _SMALL and _SMALL[prev] > 0)) or cur >= 100:
                return None
            cur = max(cur, 1) * 100
        elif t in _SCALES:
            scale = _SCALES[t]
            if (cur == 0 and prev != "a") or (last_scale is not None and scale >= last_scale) or cur >= 1000:
                return None
            total += max(cur, 1) * scale
            cur, last_scale = 0, scale
        else:
            return None
        prev = t
    return total + cur


def _number(toks: list) -> Optional[Decimal]:
    if len(toks) == 1 and _dec(toks[0]) is not None:
        return _dec(toks[0])
    n = _words_number(toks)
    return Decimal(n) if n is not None else None


def _plain(d: Decimal) -> str:
    """A Decimal in its shortest plain form: 13500, 13.5, 0.5 (no exponent, no trailing zeros)."""
    s = format(d.normalize(), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


# dates

_MONTHS = {}
for _i, _m in enumerate(calendar.month_name):
    if _m:
        _MONTHS[_m.casefold()] = _i
        _MONTHS[_m[:3].casefold()] = _i
_MONTHS["sept"] = 9
_WEEKDAYS = {w: c for w, c in zip(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
                                   ("MO", "TU", "WE", "TH", "FR", "SA", "SU"))}
WEEKDAY_ORDER = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_ORD_SUFFIX = frozenset({"st", "nd", "rd", "th"})
_ORD_UNITS = {w: i + 1 for i, w in enumerate(("first second third fourth fifth sixth seventh eighth ninth tenth eleventh "
                                               "twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth "
                                               "nineteenth").split())}
_ORD_TENS = {"twentieth": 20, "thirtieth": 30}
_DATE_FILLERS = frozenset({"the", "of", "on", "in"})
_NUMERIC_DATE_RE = re.compile(r"(?:(?:on|the)\s+)*(\d{1,2}[/.]\d{1,2}(?:[/.](\d+))?)\s*")
_ISO_RE = re.compile(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*")


def _date_ok(y: Optional[int], m: Optional[int], d: Optional[int]) -> bool:
    if m is not None and not 1 <= m <= 12:
        return False
    if y is not None and not 1900 <= y <= 2100:
        return False
    if d is not None and m is None and y is not None:
        return False                       # "the 12th, 2026": a day and a year with no month is not a date
    if d is not None:
        if m is None:
            return 1 <= d <= 31
        if y is None:
            return 1 <= d <= (29 if m == 2 else calendar.monthrange(2001, m)[1])
        return 1 <= d <= calendar.monthrange(y, m)[1]
    return m is not None or y is not None


def _date_value(y, m, d) -> dict:
    return {k: v for k, v in (("y", y), ("m", m), ("d", d)) if v is not None}


@_numeric
def norm_date(text: str) -> Optional[Norm]:
    """A calendar date as said: full ("12 December 2026", "2026-12-12", household-order "3/1/2027"), day + month in either
    order with ordinal or numeric days ("the twentieth of November", "December 19"), day-only ("the 19th"), month-only
    ("January"), month + year. Only the parts said are kept; a 2-digit year, a relative date ("next friday") or a lone
    weekday is ``None``."""
    raw = str(text or "").strip()
    m_iso = _ISO_RE.fullmatch(raw)
    if m_iso:
        y, m, d = (int(g) for g in m_iso.groups())
        return Norm(_date_value(y, m, d), "date") if _date_ok(y, m, d) else None
    m_num = _NUMERIC_DATE_RE.fullmatch(raw.casefold())
    if m_num:
        if m_num.group(2) is not None and len(m_num.group(2)) != 4:
            return None                    # "7/8/91": the century would be a guess
        try:
            import date_locale

            nd = date_locale.parse_numeric_date(m_num.group(1))
        except Exception:  # noqa: BLE001
            nd = None
        if nd is None or not _date_ok(nd.year, nd.month, nd.day):
            return None
        return Norm(_date_value(nd.year, nd.month, nd.day), "date")
    toks = _toks(raw)
    y = m = d = None
    weekday = False
    i = 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if t in _DATE_FILLERS:
            pass
        elif t in _WEEKDAYS:
            if weekday:
                return None                    # "monday-friday the 19th": two weekdays are no single date
            weekday = True
        elif t in _MONTHS:
            if m is not None:
                return None
            m = _MONTHS[t]
        elif t.isdigit():
            n = int(t)
            if len(t) == 4 and y is None:
                y = n
            elif len(t) <= 2 and d is None:
                d = n
                if nxt in _ORD_SUFFIX:
                    i += 1
            else:
                return None
        elif t in ("twenty", "thirty") and nxt in _ORD_UNITS and _ORD_UNITS[nxt] <= 9 and d is None:
            d = _TENS[t] + _ORD_UNITS[nxt]
            i += 1
        elif (t in _ORD_UNITS or t in _ORD_TENS) and d is None:
            d = _ORD_UNITS.get(t) or _ORD_TENS[t]
        else:
            return None
        i += 1
    if weekday and d is None:
        return None                        # "on friday": relative or recurring, not a date
    if not _date_ok(y, m, d):
        return None
    return Norm(_date_value(y, m, d), "date")


@_numeric
def norm_birthday(text: str) -> Optional[Norm]:
    """A birthday names a day (the month and year optional: "the 3rd, not the 4th" takes the stored month)."""
    n = norm_date(text)
    return n if n is not None and "d" in n.value else None


def date_matches(old: Mapping, stored: Mapping) -> bool:
    """D.9: partial dates compare on the fields BOTH have (a year on one side only compares on month and day)."""
    shared = [k for k in ("y", "m", "d") if k in old and k in stored]
    return bool(shared) and all(old[k] == stored[k] for k in shared)


def merge_date(new: Mapping, stored: Mapping) -> tuple:
    """``(merged value_norm, year_inherited)``: a partial new date takes the stored row's coarser parts - a day-only date
    the month, a date without a year the year - never a finer part (a month-only move drops the stored day). The
    completed date is checked: an impossible one is ``(None, ...)``. 29 February with no year anywhere stays possible (it
    exists in some year); the moment a year is present it must be a leap year."""
    out = dict(new)
    inherited = False
    if "d" in out and "m" not in out and "m" in stored:
        out["m"] = stored["m"]
    if "y" not in out and "y" in stored and "m" in out:
        out["y"] = stored["y"]
        inherited = True
    if not _date_ok(out.get("y"), out.get("m"), out.get("d")):
        return None, inherited                  # "the 31st" over February; 29 February in a year that has none
    return {k: out[k] for k in ("y", "m", "d") if k in out}, inherited


def date_not_before(value: Mapping, when: date) -> bool:
    """Is the (y, m[, d]) date on or after ``when``? A value without a year or month is never 'after' anything."""
    if "y" not in value or "m" not in value:
        return False
    return (value["y"], value["m"], value.get("d", 31)) >= (when.year, when.month, when.day)


def age_at(birthday: Mapping, when: date) -> Optional[int]:
    """Whole years old on ``when`` for a birthday with a year, else None."""
    if not all(k in birthday for k in ("y", "m", "d")):
        return None
    years = when.year - birthday["y"] - ((when.month, when.day) < (birthday["m"], birthday["d"]))
    return years if years >= 0 else None


# ages, counts, amounts, measures

_AGE_TAIL = frozenset({"years", "year", "yrs", "yr", "yo", "old"})


@_numeric
def norm_age(text: str) -> Optional[Norm]:
    """An age in years: "8", "eight", "8 years old", "3 and a half", "3.5". Halves only; 0-120. A date, a count noun or
    another unit ("8 months", "8th", "8 dogs") is None."""
    toks = _toks(text)
    while toks and toks[-1] in _AGE_TAIL:
        toks.pop()
    half = False
    if toks[-3:] == ["and", "a", "half"]:
        toks, half = toks[:-3], True
    v = _number(toks)
    if v is None:
        return None
    v += Decimal("0.5") if half else 0
    if not 0 <= v <= 120 or (v * 2) % 1:
        return None
    return Norm({"v": float(v)}, "years")


_UNIT_WORDS = {
    **dict.fromkeys(("km", "kms", "kilometre", "kilometres", "kilometer", "kilometers"), "km"),
    **dict.fromkeys(("kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"), "kg"),
    **dict.fromkeys(("min", "mins", "minute", "minutes"), "minute"),
    **dict.fromkeys(("hr", "hrs", "hour", "hours"), "hour"),
    **dict.fromkeys(("sec", "secs", "second", "seconds"), "second"),
    **dict.fromkeys(("day", "days"), "day"), **dict.fromkeys(("week", "weeks"), "week"),
    **dict.fromkeys(("month", "months"), "month"), **dict.fromkeys(("year", "years"), "year"),
    **dict.fromkeys(("metre", "metres", "meter", "meters", "m"), "metre"), **dict.fromkeys(("cm",), "cm"),
    **dict.fromkeys(("litre", "litres", "liter", "liters"), "litre"), **dict.fromkeys(("mile", "miles"), "mile"),
    **dict.fromkeys(("percent", "%"), "percent"),
}
_CURRENCY = {"$": None, "aud": "AUD", "usd": "USD", "nzd": "NZD", "eur": "EUR", "gbp": "GBP", "€": "EUR", "£": "GBP",
             "dollar": None, "dollars": None, "bucks": None, "buck": None, "euro": "EUR", "euros": "EUR",
             "pound": "GBP", "pounds": "GBP"}
_CURRENCY_PREFIX = {"us": "USD", "nz": "NZD", "a": "AUD", "au": "AUD"}     # "us$", "a$"
_AMOUNT_SCALE = {"k": 1000, "grand": 1000, "thousand": 1000, "m": 1_000_000, "million": 1_000_000, "mil": 1_000_000,
                 "b": 1_000_000_000, "bn": 1_000_000_000, "billion": 1_000_000_000}
DEFAULT_CURRENCY = "AUD"
#: a clock marker after a number is a TIME ("5pm", "5 am", "5 p.m."): never a count noun to strip (PR 2477 review)
_CLOCK_MARKS = frozenset({"am", "pm"})
_NOT_A_COUNT = frozenset(_UNIT_WORDS) | frozenset(_CURRENCY) | _AGE_TAIL | _CLOCK_MARKS | {"cents", "cent"}


@_numeric
def norm_count(text: str) -> Optional[Norm]:
    """A whole count ("3", "three", "3 dogs"): one trailing noun allowed, never a unit, money, age or clock word (a
    time is not a count: "5pm" and "5 am" abstain)."""
    toks = _toks(text)
    if len(toks) > 1 and toks[-1].isalpha() and toks[-1] not in _NUMBER_WORDS:
        if toks[-1] in _NOT_A_COUNT:
            return None
        toks = toks[:-1]
    v = _number(toks)
    if v is None or v % 1 or v < 0:
        return None
    return Norm({"v": int(v)}, "count")


@_numeric
def norm_amount(text: str) -> Optional[Norm]:
    """Money: "$13,500", "13.5k", "twelve thousand dollars", "2 grand", "1.2m", "US$40", "20 dollar". The scale is applied
    and remembered (an unscaled old value takes the new one's: ``normalise_pair``); the currency is the stated one or AUD.
    An amount that NAMES two different currencies ("USD 60 EUR", "US$60 euros") abstains: neither is picked by position.
    A generic word beside a named one ("$", "dollars", "bucks") names none and does not conflict."""
    toks = _toks(text)
    cur: Optional[str] = None
    named: set = set()

    def take(word: str) -> None:
        nonlocal cur
        if _CURRENCY[word]:
            cur = _CURRENCY[word]
            named.add(cur)

    if len(toks) >= 2 and toks[0] in _CURRENCY_PREFIX and toks[1] == "$":
        cur, toks = _CURRENCY_PREFIX[toks[0]], toks[2:]
        named.add(cur)
    while toks and toks[0] in _CURRENCY:
        take(toks.pop(0))
    while toks and toks[-1] in _CURRENCY:
        take(toks.pop())
    if len(named) > 1:
        return None
    scale = 1
    # a scale word after a NUMERAL ("13.5k", "12 thousand", "2 grand") multiplies it; after spelled words the scale is part
    # of the spelled number ("two hundred thousand", "one million five hundred thousand") and the accumulator reads it -
    # stripping it there applied it twice
    if len(toks) == 2 and toks[-1] in _AMOUNT_SCALE and _dec(toks[0]) is not None:
        scale, toks = _AMOUNT_SCALE[toks[-1]], toks[:-1]
    elif toks and toks[-1] == "grand" and not any(t in _SCALES for t in toks):
        scale, toks = 1000, toks[:-1]
    if any(t in ("cents", "cent") for t in toks):
        return None
    v = _number(toks)
    if v is None or v < 0:
        return None
    v *= scale
    if scale == 1 and any(t in _SCALES for t in toks):
        scale = max(_SCALES[t] for t in toks if t in _SCALES)    # "twelve thousand five hundred": already in the number
    stated = cur is not None
    return Norm({"v": _plain(v), "cur": cur or DEFAULT_CURRENCY}, "money", scale, stated)


@_numeric
def norm_measure(text: str, *, bare_ok: bool = False) -> Optional[Norm]:
    """A number with a measure unit ("5 km", "2.5 hours", "3%"); ``bare_ok`` lets an old value omit the unit (it then
    takes the new one's, ``normalise_pair``)."""
    toks = _toks(text)
    unit = ""
    if toks and toks[-1] in _UNIT_WORDS:
        unit, toks = _UNIT_WORDS[toks[-1]], toks[:-1]
    if not unit and not bare_ok:
        return None
    v = _number(toks)
    if v is None or v < 0:
        return None
    return Norm({"v": _plain(v), "u": unit}, unit)


# times of day and schedules

_HOUR_WORDS = {w: i for w, i in _SMALL.items() if 1 <= i <= 12}


@_numeric
def norm_time(text: str) -> Optional[Norm]:
    """A time of day: "5pm", "5:30", "17:00", "five pm", "noon". Resolved as ``{"h": 0-23, "mi"}``; an hour of 1-12 with
    no am/pm is UNRESOLVED ``{"h12", "mi"}`` - it inherits a stored row's half of the day (``resolve_time``) or abstains."""
    toks = [t for t in _toks(text) if t not in ("at", "o", "clock")]
    if toks in (["noon"], ["midday"]):
        return Norm({"h": 12, "mi": 0}, "time")
    if toks == ["midnight"]:
        return Norm({"h": 0, "mi": 0}, "time")
    ampm = toks.pop() if toks and toks[-1] in ("am", "pm") else ""
    if len(toks) != 1:
        return None
    t = toks[0]
    if t in _HOUR_WORDS:
        h, mi, padded = _HOUR_WORDS[t], 0, False
    else:
        parts = t.replace(".", ":").split(":")
        if len(parts) > 2 or not all(p.isdigit() for p in parts) or (len(parts) == 2 and len(parts[1]) != 2):
            return None
        h, mi, padded = int(parts[0]), int(parts[1]) if len(parts) == 2 else 0, len(parts[0]) == 2 and parts[0][0] == "0"
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    if ampm:
        if not 1 <= h <= 12:
            return None
        return Norm({"h": h % 12 + (12 if ampm == "pm" else 0), "mi": mi}, "time")
    if h >= 13 or h == 0 or padded:
        return Norm({"h": h, "mi": mi}, "time")
    return Norm({"h12": h, "mi": mi}, "time")


def resolve_time(value: Mapping, stored: Mapping) -> Optional[dict]:
    """An unresolved hour takes the stored row's half of the day; a resolved one is returned as is."""
    if "h" in value:
        return dict(value)
    if "h" not in stored:
        return None
    return {"h": value["h12"] % 12 + (12 if stored["h"] >= 12 else 0), "mi": value["mi"]}


def time_matches(old: Mapping, stored: Mapping) -> bool:
    if "h" in old and "h" in stored:
        return old["h"] == stored["h"] and old["mi"] == stored["mi"]
    o = old.get("h", old.get("h12", -1)) % 12
    s = stored.get("h", stored.get("h12", -1)) % 12
    return o == s and old.get("mi") == stored.get("mi")


_AT_RE = re.compile(r"\bat\b", re.IGNORECASE)
_FREQ = {"daily": "daily", "day": "daily", "weekly": "weekly", "week": "weekly", "monthly": "monthly", "month": "monthly",
         "yearly": "yearly", "year": "yearly", "annually": "yearly"}


_DAY_ABBR = {**{w: c for w, c in _WEEKDAYS.items()}, "mon": "MO", "tue": "TU", "tues": "TU", "wed": "WE", "thu": "TH",
             "thur": "TH", "thurs": "TH", "fri": "FR", "sat": "SA", "sun": "SU"}
#: a separator the tokeniser would DROP between two words: a hyphen or dash, "to", "through" / "thru", a slash.
#: "monday-friday" tokenised to "monday", "friday" and was stored as MO,FR only
_RANGE_SEP_RE = re.compile(r"(?<=\w)\s*(?:[-\u2010-\u2015\u2212/])\s*(?=\w)|\s(?:to|through|thru|till|until)\s",
                           re.IGNORECASE)
_DAY_RANGE_RE = re.compile(r"\b([a-z]+?)s?\s*(?:[-\u2010-\u2015\u2212]|\s(?:to|through|thru)\s)\s*([a-z]+?)s?\b",
                           re.IGNORECASE)
_NOT_A_RANGE = object()


def _day_range(raw: str):
    """The weekday range in a schedule's day part: ``(text without the range, [days])``, ``None`` when there is no
    range separator, or ``_NOT_A_RANGE`` (abstain) when a separator is there but is not a FORWARD range of two weekday
    names ("monday-friday", "mon - fri", "monday to friday" -> MO..FR). A range across the week boundary
    ("friday-monday") abstains - it is a choice, not a reading; a slash ("monday/friday": one, the other or both?)
    abstains; any other dropped separator abstains."""
    if not _RANGE_SEP_RE.search(raw):
        return None
    m = _DAY_RANGE_RE.search(raw)
    if not m:
        return _NOT_A_RANGE
    a, b = _DAY_ABBR.get(m.group(1).casefold()), _DAY_ABBR.get(m.group(2).casefold())
    if a is None or b is None:
        return _NOT_A_RANGE
    i, j = WEEKDAY_ORDER.index(a), WEEKDAY_ORDER.index(b)
    if j <= i:
        return _NOT_A_RANGE
    rest = raw[:m.start()] + " " + raw[m.end():]
    if _RANGE_SEP_RE.search(rest):
        return _NOT_A_RANGE                     # a second range, or a separator left over
    return rest, list(WEEKDAY_ORDER[i:j + 1])


@_numeric
def norm_schedule(text: str) -> Optional[Norm]:
    """A small recurrence: "on wednesdays", "mondays and thursdays", "every day", "weekly", "weekdays at 5pm" ->
    ``{"freq", "byday"?, "time"?}``."""
    raw = str(text or "")
    when = None
    head_tail = _AT_RE.split(raw, maxsplit=1)
    if len(head_tail) == 2:
        when = norm_time(head_tail[1])          # the RAW clock text, sign and all: never re-joined tokens
        if when is None:
            return None
        raw = head_tail[0]
    span = _day_range(raw)
    if span is _NOT_A_RANGE:
        return None
    if span:
        raw = span[0]
    toks = _toks(raw)
    freq, days = None, set(span[1] if span else ())
    for t in toks:
        if t in ("every", "each", "on", "and", "the", "a"):
            continue
        base = t[:-1] if t.endswith("s") and t[:-1] in _WEEKDAYS else t
        if base in _WEEKDAYS:
            days.add(_WEEKDAYS[base])
        elif t == "weekdays":
            days.update(WEEKDAY_ORDER[:5])
        elif t == "weekends":
            days.update(WEEKDAY_ORDER[5:])
        elif t in _FREQ and freq in (None, _FREQ[t]):
            freq = _FREQ[t]
        else:
            return None
    if days and freq not in (None, "weekly"):
        return None
    if not days and freq is None:
        return None
    value: dict = {"freq": freq or "weekly"}
    if days:
        value["byday"] = [d for d in WEEKDAY_ORDER if d in days]
    if when is not None:
        value["time"] = when.value
    return Norm(value, "rrule")


# labels

def _norm_label(unit: str):
    def norm(text: str) -> Optional[Norm]:
        f = people_graph.fold_name(text)
        return Norm({"label": f}, unit) if f else None
    return norm


def norm_partner(text: str) -> Optional[Norm]:
    """A partner as said; the validator turns it into a person id through the people graph (unresolved = abstain)."""
    f = people_graph.fold_name(text)
    return Norm({"name": f}, "person") if f else None


NORMALISERS = {
    "event_date": norm_date, "origin_date": norm_date, "birthday": norm_birthday, "age": norm_age,
    "amount": norm_amount, "measure": norm_measure, "count": norm_count, "time_of_day": norm_time,
    "schedule": norm_schedule, "location": _norm_label("place"), "employer": _norm_label("org"),
    "partner": norm_partner, "diet": _norm_label("label"),
}


def normalise(attribute: str, text: Optional[str]) -> Optional[Norm]:
    """``attribute``'s normaliser on ``text``; ``None`` = abstain (also for an unknown attribute). Never raises."""
    fn = NORMALISERS.get(attribute)
    if fn is None or not isinstance(text, str) or not text.strip():
        return None
    try:
        n = fn(text)
    except Exception:  # noqa: BLE001
        return None
    return n if n is not None and n.unit in ATTRIBUTES[attribute].units else None


def normalise_pair(attribute: str, old: Optional[str], new: str) -> tuple:
    """``(ok, old Norm | None, new Norm | None)`` for a correction. An old amount said without a scale may also carry the new
    one's ("13 thousand, not 12" -> 12 or 12000, ``Norm.readings``; the stored row decides which), a currency said on one
    side only is the other's too, an old measure said bare takes the new one's unit. ``ok`` is False when either side does
    not parse, or the units differ."""
    n_new = normalise(attribute, new)
    if n_new is None:
        return False, None, None
    if old is None:
        return True, None, n_new
    if attribute == "measure":
        n_old = norm_measure(old, bare_ok=True)
        if n_old is not None and not n_old.unit:
            n_old = Norm({"v": n_old.value["v"], "u": n_new.unit}, n_new.unit)
    else:
        n_old = normalise(attribute, old)
    if n_old is None:
        return False, None, n_new
    if attribute == "amount":
        cur_old, cur_new = n_old.value["cur"], n_new.value["cur"]
        if n_old.stated != n_new.stated:
            cur_old = cur_new = cur_old if n_old.stated else cur_new
        alts = ()
        if n_old.scale == 1 and n_new.scale > 1 and Decimal(n_old.value["v"]) < n_new.scale:
            # "13 thousand, not 12": the 12 may be 12 or 12 thousand ("$12,000" beside "13.5k" is only 12000)
            alts = ({"v": _plain(Decimal(n_old.value["v"]) * n_new.scale), "cur": cur_old},)
        n_old = Norm({"v": n_old.value["v"], "cur": cur_old}, "money", n_old.scale, True, alts)
        n_new = Norm({"v": n_new.value["v"], "cur": cur_new}, "money", n_new.scale, True)
        if cur_old != cur_new:
            return False, n_old, n_new
    if n_old.unit != n_new.unit:
        return False, n_old, n_new
    return True, n_old, n_new


def same_value(attribute: str, a: Mapping, b: Mapping) -> bool:
    """Two value_norms of ``attribute`` say the same thing (D.10). Amount / measure compare numerically."""
    try:
        if attribute in ("amount", "measure"):
            ka = "cur" if attribute == "amount" else "u"
            return Decimal(str(a["v"])) == Decimal(str(b["v"])) and a[ka] == b[ka]
        if attribute == "age":
            return float(a["v"]) == float(b["v"])
        if attribute == "time_of_day":
            return "h" in a and "h" in b and time_matches(a, b)
        return dict(a) == dict(b)
    except Exception:  # noqa: BLE001
        return False


def old_matches(attribute: str, old: Mapping, stored: Mapping) -> bool:
    """D.9: does a claim's OLD value name the stored attribute value? Partial dates compare on shared fields; an unresolved
    hour on its 12-hour reading; everything else as ``same_value``."""
    if attribute in DATE_ATTRIBUTES:
        return date_matches(old, stored)
    if attribute == "time_of_day":
        return time_matches(old, stored)
    return same_value(attribute, old, stored)


def parse_span(span: Any) -> Optional[tuple]:
    """``"start:end"`` -> ``(start, end)`` with 0 <= start < end, else None."""
    try:
        a, b = (int(x) for x in str(span).split(":"))
    except Exception:  # noqa: BLE001
        return None
    return (a, b) if 0 <= a < b else None


def span_value(attribute: str, text: str, span: Any) -> Optional[Norm]:
    """D.20: the value_norm code computes from a row attribute's span (``None`` when the span is not inside the text)."""
    ab = parse_span(span)
    if ab is None or ab[1] > len(text or ""):
        return None
    return normalise(attribute, text[ab[0]:ab[1]])


#: every string the validator may hold as a constant (the AST test in tests/test_memory_claims_validate.py)
VALIDATOR_VOCABULARY = frozenset(
    CLAIM_TYPES + KINDS + EVENTS + EMERGENCY_TIMES + CORRECTION_TIMES + VERDICTS + SUBJECT_KINDS + FIELD_NAMES
    + (SUPERSEDED, ASSERTED, APPROVED, UNIQUE, AMBIGUOUS, NONE, EXACT, TOKEN, DETECTOR_CALL, WRITER_CLASS, "relation",
       "pronoun", "name", "unknown")
    + tuple(ATTRIBUTES) + tuple(UNITS)
)
