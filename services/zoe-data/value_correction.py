"""value_correction - a spoken correction of a stored value reaches the STORED rows, in any later session.

Why (QA round 5, 2026-11-08): "Oliver is twenty-five, not twenty-four" and "Anaya is 7 now" held inside the session (the brain
reads its own context) and then the OLD value won in a new session: both rows were served, and the correction was stored as a
meta-fact ("The user corrected a previous statement..."). ``correction_apply`` already rewrites a stored row from a correction,
but it is flag-dark (``ZOE_CORRECTION_APPLY``), reads a lead-cued value pair only, and ``maybe_fix_recent`` is bounded to the
SAME session. This is the always-on, post-turn tier of the same machinery (it reuses ``correction_apply.same_shape``,
``note_correction`` and the ``conversation_correction`` user-turn writer):

* ``statement(text)`` -> ``Correction(subject, new, old)`` for "Oliver is twenty-five, not twenty-four" / "correction: anaya is 7
  not 6" / "Anaya is 7 now" (``old`` None: an AGE restatement). A subject is a name, or "my <relation>". A bare value pair
  ("Tea, not coffee") is never read: the subject is what keeps it from rewriting an unrelated row.
* ``apply(...)`` finds the member's approved rows that name the subject AND hold the old value as a whole word (an age: the
  subject's age number), and supersedes each through ``MemoryService.review(edit)`` under the user-class writer
  ``conversation_correction`` with the member's own words as the anchor - the new row is USER-STATED, the old one is
  ``superseded`` (not served), nothing else is touched. Evidence only: no matching row, or more than ``MAX_ROWS``, changes nothing.
  The correction is also registered for the digest of an earlier turn that has not landed yet (``note_correction``).

Pure matching (``statement``, ``rewrite``); the write is one ``review`` per row. Never raises.
"""
from __future__ import annotations

import logging
import re
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

MAX_ROWS = 3            # the twin rows one utterance leaves behind; more than that is not one fact
MAX_WORDS = 18          # a correction is a short sentence
SCAN_LIMIT = 2000

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
         "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety"}
_WORD_N = {w: i for i, w in enumerate(_ONES)}
_WORD_N.update({w: n for n, w in _TENS.items()})


def number_of(token: str) -> Optional[int]:
    """0-120 from digits ("25") or words ("twenty-five", "twenty five", "six"); None for anything else. Pure."""
    t = " ".join((token or "").lower().replace("-", " ").split())
    if t.isdigit():
        n = int(t)
        return n if n <= 120 else None
    if t in _WORD_N:
        return _WORD_N[t]
    parts = t.split()
    if len(parts) == 2 and parts[0] in _WORD_N and parts[1] in _WORD_N and _WORD_N[parts[0]] in _TENS and 0 < _WORD_N[parts[1]] < 10:
        return _WORD_N[parts[0]] + _WORD_N[parts[1]]
    if t in ("one hundred", "a hundred", "hundred"):
        return 100
    return None


_HALF_TAIL_RE = re.compile(r"(?:[\s-]+and[\s-]+a[\s-]+half|\.5)$", re.IGNORECASE)


def half_of(token: str) -> Optional[int]:
    """The whole part of "3 and a half" / "three and a half" / "3.5" (3), else None. Pure."""
    t = (token or "").strip().lower()
    m = _HALF_TAIL_RE.search(t)
    return number_of(t[:m.start()]) if m else None


def value_of(token: str) -> Optional[float]:
    """An age as a number: 7, 3.5 ("three and a half"), or None. Pure."""
    n = number_of(token)
    if n is not None:
        return float(n)
    h = half_of(token)
    return h + 0.5 if h is not None else None


def number_like(n: int, like: str) -> str:
    """``n`` written the way ``like`` was ("24" -> digits, "twenty-four" -> hyphenated words, "twenty four" -> spaced words)."""
    if like.strip().isdigit():
        return str(n)
    if n >= 100:
        return "one hundred" if n == 100 else str(n)
    if n < 20:
        word = _ONES[n]
    else:
        word = _TENS[n - n % 10] + (("-" if "-" in like else " ") + _ONES[n % 10] if n % 10 else "")
    return word


_NUM = (r"(?:\d{1,3}|(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?"
        r"|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen"
        r"|nineteen|one hundred)")
# "3 and a half" / "three and a half" / "3.5": an age with its half (QA 6b: "wren is 3 and a half, not 3" was never read as a
# correction, so the stored "three" stayed)
_HALF = r"(?:(?:\s+|-)and(?:\s+|-)a(?:\s+|-)half|\.5)"
_NUMH = rf"{_NUM}{_HALF}"
_YEARS = r"(?:\s+(?:years?[ -]old|yrs?\s+old|yo))?"
_WORD = r"[A-Za-z][A-Za-z'’\-]{1,24}"
_REL = (r"(?:sister|brother|mum|mom|mother|dad|father|wife|husband|partner|son|daughter|boyfriend|girlfriend|nan|nana|grandma|"
        r"grandmother|grandpa|grandfather|aunt|auntie|uncle|cousin|niece|nephew|friend|boss)")
# "ashas swimming is on wednesday not thursday": a lazily typed possessive "<name>s <noun>" (the apostrophe dropped) is a subject too
_POSS = rf"(?!(?:the|my|our|this|that|his|her|its|their|your)\s){_WORD}['’]?s\s+{_WORD}"
_SUBJ = rf"(?P<subj>(?:my|our)\s+{_REL}|(?:the|my|our)\s+{_WORD}(?:\s+{_WORD})?|{_POSS}|{_WORD})"
_CUE = (r"(?P<cue>(?:(?:a\s+(?:small|quick)\s+)?correction|actually|actualy|actully|acutally|actualley|sorry|no|nope|wait|oh|hang\s+on|i\s+(?:misspoke|meant|said)|"
        r"(?:that(?:['’]s|\s+is|\s+was)\s+(?:wrong|incorrect|not\s+right)))[,.;:!\-\s]+)*")
_LEAD = rf"^\W*(?:(?:good\s+)?(?:morning|evening|afternoon)[,.\s]+)?(?:(?:and|also|oh\s+and|and\s+also)[,.\s]+)?{_CUE}"
_PAIR_RE = re.compile(_LEAD + rf"{_SUBJ}\s+(?:is|was|are)\s+(?:actually\s+|really\s+)?(?P<n>{_NUMH}|{_WORD}(?:\s+{_WORD}){{0,2}}|{_NUM}){_YEARS}[,;]?\s+"
                      rf"(?:and\s+)?not\s+(?P<o>{_NUMH}|{_WORD}(?:\s+{_WORD}){{0,2}}|{_NUM}){_YEARS}\W*(?:i\s+misspoke\W*)?$", re.IGNORECASE)
_AGE_NOW_RE = re.compile(_LEAD + rf"{_SUBJ}\s+(?:is|just\s+turned|turned|turns)\s+(?:(?P<pre>actually|now)\s+)?(?P<n>{_NUMH}|{_NUM}){_YEARS}"
                         # "actually isla is 6 after all, i got mixed up" (QA round 9: correcting BACK was never read)
                         rf"(?:\s+(?P<post>now|after\s+all))?(?:\W*$|\s*[,;]\s+(?!not\b)\S.*$)", re.IGNORECASE)
# "its 76 for margaret, not 78": the value comes first, the subject after "for" (QA 6b)
_FOR_RE = re.compile(_LEAD + rf"(?:it['’]?s|that['’]?s|thats|it\s+is)\s+(?:actually\s+)?(?P<n>{_NUMH}|{_NUM}){_YEARS}\s+for\s+{_SUBJ}[,;]?\s+"
                     rf"(?:and\s+)?not\s+(?P<o>{_NUMH}|{_NUM}){_YEARS}\W*$", re.IGNORECASE)
# "kais 21 not 22" / "also kai 21 not 22": a lazily typed name (the possessive apostrophe and the "is" both dropped) and two numbers
# (QA round 7: this shape was never read, and the digest filed the stray "Kais: 21" as a person)
_BARE_RE = re.compile(_LEAD + rf"(?P<subj>{_WORD})\s+(?P<n>{_NUMH}|{_NUM}){_YEARS}[,;]?\s+(?:and\s+)?not\s+(?P<o>{_NUMH}|{_NUM}){_YEARS}\W*$",
                      re.IGNORECASE)
_PREP_RE = re.compile(r"^(?:on|at|in|by|for|around|about)\s+", re.IGNORECASE)


# -- quantity values: a date's day, an amount, an age, written any way (QA round 8, 2026-11-12) -------------------------------------
# "sorry the dentist appointment is the 27th of november not the 20th" was read by nothing: the pair above takes a value made of
# letters or a bare 0-120 number, so an ordinal ("27th"), a date ("the 20th of november") or an amount ("12 thousand") never formed a
# correction, and the stored rows ("... on the 20th of November", "... on the twentieth of November") stayed approved beside the new
# value. A QUANTITY is compared by its number, whatever its spelling, so ONE correction reaches every phrasing a writer left behind.
_ORD_ONES = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9,
             "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16,
             "seventeenth": 17, "eighteenth": 18, "nineteenth": 19}
_ORD_TENS = {"twentieth": 20, "thirtieth": 30}
_ORD_WORD = (r"(?:(?:twenty|thirty)[- ](?:" + "|".join(list(_ORD_ONES)[:9]) + r")|"
             + "|".join(sorted(list(_ORD_ONES) + list(_ORD_TENS), key=len, reverse=True)) + r")")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")
_MONTH_ABBR = {**{m[:3]: m for m in _MONTHS}, "sept": "september"}
_MONTH_RE = re.compile(r"(?<![A-Za-z])(?:" + "|".join(_MONTHS) + "|" + "|".join(sorted(_MONTH_ABBR, key=len, reverse=True))
                       + r")(?![A-Za-z])", re.IGNORECASE)
_SCALE = {"hundred": 100, "thousand": 1000, "grand": 1000, "k": 1000, "million": 1_000_000, "mil": 1_000_000, "m": 1_000_000}
_QTY_RE = re.compile(
    r"(?<![A-Za-z0-9:.,])\$?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|" + _ORD_WORD + "|" + _NUM + r")"
    r"(?P<ord>st|nd|rd|th)?(?P<half>" + _HALF + r")?"
    r"(?:\s*(?P<scale>k|m)\b|\s+(?P<scalew>hundred|thousand|grand|million|mil)\b)?(?![A-Za-z0-9:])",
    re.IGNORECASE)
#: the words a quantity VALUE may carry besides its number: "the 27th of november", "on the 20th", "12 thousand dollars", "9 years
#: old". Anything else ("is", "said", a name) makes it a clause, never a value.
_QTY_VALUE_WORDS = frozenset({"the", "on", "of", "at", "in", "by", "about", "around", "roughly", "like", "dollars", "dollar", "bucks",
                              "years", "year", "yrs", "old", "yo", "now", "aud", "usd"})


class Quantity(NamedTuple):
    start: int
    end: int
    value: float
    ordinal: bool
    scale: float
    text: str

    @property
    def scaled(self) -> bool:
        return self.scale != 1


def _num_value(raw: str) -> Optional[float]:
    t = (raw or "").strip().lower().replace(",", "")
    try:
        return float(t)
    except ValueError:
        pass
    if t in _ORD_ONES:
        return float(_ORD_ONES[t])
    if t in _ORD_TENS:
        return float(_ORD_TENS[t])
    parts = t.replace("-", " ").split()
    if len(parts) == 2 and parts[0] in ("twenty", "thirty") and parts[1] in _ORD_ONES and _ORD_ONES[parts[1]] < 10:
        return float((20 if parts[0] == "twenty" else 30) + _ORD_ONES[parts[1]])
    n = number_of(t)
    return None if n is None else float(n)


def quantities(text: str) -> list:
    """Every number in ``text`` with its value, written any way: "20", "20th", "twentieth", "twenty-seven", "13 and a half
    thousand", "$12,000", "12k". A time's digits ("10:20") are not a quantity. Pure."""
    out = []
    for m in _QTY_RE.finditer(text or ""):
        raw = m.group("num") or ""
        base = _num_value(raw)
        if base is None:
            continue
        if m.group("half"):
            base += 0.5
        scale = (m.group("scale") or m.group("scalew") or "").lower()
        if scale in ("k", "m") and not raw[:1].isdigit():
            continue            # "one m" is not a million
        mult = float(_SCALE[scale]) if scale else 1.0
        base *= mult
        last = raw.lower().replace("-", " ").split()[-1]
        ordinal = bool(m.group("ord")) or last in _ORD_ONES or last in _ORD_TENS
        out.append(Quantity(m.start(), m.end(), base, ordinal, mult, m.group(0)))
    return out


def _month_of(text: str) -> Optional[str]:
    m = _MONTH_RE.search(text or "")
    if not m:
        return None
    w = m.group(0).lower()
    return w if w in _MONTHS else _MONTH_ABBR.get(w)


def quantity_value(value: str) -> Optional[Quantity]:
    """The ONE quantity a correction value is ("the 27th of november", "12 thousand dollars", "9"), or None: no number, two
    numbers, or a word that makes it a clause rather than a value. Pure."""
    qs = quantities(value)
    if len(qs) != 1:
        return None
    q = qs[0]
    rest = _MONTH_RE.sub(" ", (value[:q.start] + " " + value[q.end:]).lower())
    if any(w not in _QTY_VALUE_WORDS for w in re.findall(r"[a-z]+", rest)):
        return None
    return q


def is_quantity_pair(new: str, old: str) -> bool:
    """Both halves of a correction are one quantity each (and not both plain ages, which the age path reads). Pure."""
    return quantity_value(new) is not None and quantity_value(old) is not None


# "<subject> is <quantity>[,] not <quantity>[, anything after]": the value pair above with quantity values, and a trailing clause
# allowed after the old value ("actually eli is 9 not 8, he had his birthday last month" - the pair above is anchored at the end
# of the sentence, so that common shape was never a correction). The values are validated by ``quantity_value``.
#: one character of a value: anything but sentence punctuation, EXCEPT a point or comma between two digits ("13.5 thousand",
#: "$13,500" - without it those corrections matched nothing and the old amount stayed stored)
_QVAL = r"(?:[^,;.!?]|(?<=\d)[.,](?=\d))"
# QA round 9: a two-word subject with no determiner ("book club is on the 19th, not the 12th") and a verb word between "is" and the
# value ("the rego is due on the 21st not the 14th") were read by nothing, so the old date stayed approved beside the new one.
_SUBJ2 = _SUBJ[:-1] + rf"|{_WORD}\s+{_WORD})"
_STATE = r"(?:(?:actually|really|now|still|due|booked|scheduled|set|happening|meant\s+to\s+be)\s+)*"
_QPAIR_RE = re.compile(_LEAD + rf"{_SUBJ2}\s+(?:is|was|are|will\s+be|['’]s)\s+{_STATE}(?P<n>{_QVAL}{{1,40}}?)[,;]?\s+"
                       rf"(?:and\s+)?not\s+(?P<o>{_QVAL}{{1,30}}?)\s*(?:(?:[;!?]|[.,](?!\d))\s*(?:.*))?$", re.IGNORECASE)
# "book club moved again, its on the 19th of december now" / "the dentist got moved from the 5th to the 12th": a MOVED date. With no
# old value the subject's one stored date is restated (``rewrite``); a chain (5th -> 12th -> 19th) is the same sentence again.
_MOVED_RE = re.compile(_LEAD + rf"{_SUBJ2}\s+(?:(?:has|have|got|was|is|been|just)\s+)*(?:moved|changed|rescheduled|shifted|pushed\s+back)"
                       rf"(?:\s+again)?(?:\s+from\s+(?P<o>{_QVAL}{{1,30}}?)\s+to\s+|[,;]?\s+(?:(?:and\s+)?(?:it['’]?s|its|it\s+is)\s+(?:now\s+)?)?"
                       rf"(?:(?:to|on|until|till|for)\s+)?)(?P<n>{_QVAL}{{1,40}}?)(?:\s+now)?\s*[.!]*$", re.IGNORECASE)


_AGE_SUFFIX_RE = re.compile(r"\s+(?:years?[ -]old|yrs?\s+old|yo)\s*$", re.IGNORECASE)


def _bare_value(v: str) -> str:
    """"on friday" -> "friday": a value's lead preposition is not part of the value (the stored row says "exam on Thursday")."""
    stripped = _PREP_RE.sub("", v.strip())
    return stripped if stripped and value_of(v) is None else v.strip()


_BARE_NOT_NAME = frozenset({"about", "around", "over", "under", "roughly", "maybe", "only", "just", "almost", "nearly", "still",
                            "page", "price", "cost", "costs", "score", "total", "temp", "monday", "tuesday", "wednesday", "thursday", "friday",
                            "saturday", "sunday", "number", "size", "age", "aged"})
_NOT_SUBJECT = frozenset({"it", "this", "that", "there", "here", "he", "she", "they", "we", "i", "you", "what", "who", "which", "the",
                          "a", "an", "my", "our", "your", "his", "her", "their", "so", "its", "zoe", "today", "tomorrow", "yesterday",
                          "tonight", "everything", "nothing", "something", "someone", "everyone", "anything"})


_OWNER_NOUN_RE = re.compile(r"^(?P<o>[A-Za-z][A-Za-z\-]{1,24}?)(?P<p>['’]s|s)\s+(?P<n>[A-Za-z][A-Za-z\-]{1,24})$")


def owner_noun(subject: str):
    """(owner, noun) for the subject "ashas swimming" / "Asha's swimming", else None. With an apostrophe the owner is exact ("asha");
    without one the typed word is kept whole ("ashas": a lazily typed possessive OR a name that ends in s, so a row decides which -
    see ``_owner_re``). The noun is lower-cased. Pure."""
    s = (subject or "").strip()
    if re.match(r"(?i)^(?:the|my|our|this|that|his|her|its|their|your)\s", s):
        return None
    m = _OWNER_NOUN_RE.match(s)
    if not m:
        return None
    lazy = not m.group("p").startswith(("'", "’"))
    word = m.group("o") + ("s" if lazy else "")
    return word[:1].upper() + word[1:].lower(), m.group("n").lower()


def explicit_possessive(subject: str) -> bool:
    """True when the subject was typed with its apostrophe ("Carlos's swimming"): the owner is then exactly that word, never a shortened name."""
    m = _OWNER_NOUN_RE.match((subject or "").strip())
    return bool(m) and m.group("p").startswith(("'", "’")) and owner_noun(subject) is not None


def _may_be_lazy(word: str) -> bool:
    """Could ``word`` be a name typed with a stray final s ("kais" for Kai)? A name that ends in s may also be exactly that name."""
    return len(word) > 3 and word.lower().endswith("s") and not word.lower().endswith("ss")


def may_shorten(corr: "Correction", exact_exists: bool, ambiguous: bool = False) -> bool:
    """THE one rule for shortening a typed name to a known one ("kais" -> Kai, "ashas" -> Asha), shared by every writer that resolves a
    correction's subject (``apply`` here, ``person_extractor.apply_person_fact``): only when the correction is a LAZY form (the bare
    "kais 21 not 22", or an owner+noun possessive typed without its apostrophe), the name could carry a stray s, and NOTHING exact
    exists - no row / person under the name as typed. An explicit possessive ("Carlos's ...") and a plain "Carlos is 21 not 22" name
    Carlos: they never reach Carlo. ``ambiguous``: the typed name resolved to SEVERAL people (two Carloses) - that is a match to ask about, never an
    absence to repair, so it never shortens. Pure."""
    on = owner_noun(corr.subject)
    lazy_form = bool(corr.lazy) or (on is not None and not explicit_possessive(corr.subject))
    return lazy_form and not exact_exists and not ambiguous and _may_be_lazy((on or (corr.subject,))[0])


async def person_status(name: str, user_id: str) -> str:
    """How the member's people graph answers a typed name: ``exact`` (one person), ``ambiguous`` (several), ``none`` (POSITIVELY nobody) or
    ``error`` (the lookup could not be made). The same resolver ``person_extractor.apply_person_fact`` uses. Anything but ``none`` blocks a
    shortening to a different name: an unreadable graph is not an absence. Never raises."""
    try:
        import person_extractor as pe

        db, opened = await pe._ensure_db(None)
        if db is None:
            return "error"
        try:
            res = await pe._resolve_person(name, user_id, db)
        finally:
            if opened:
                await db.close()
        if res is None:
            return "error"
        if getattr(res, "ambiguous", False):
            return "ambiguous"
        return "exact" if getattr(res, "person_id", None) else "none"
    except Exception:  # noqa: BLE001
        return "error"


def _owner_noun_re(owner: str, noun: str, lazy: bool = False):
    """The row says the OWNER has the NOUN: "<owner>('s) <noun>" side by side. The owner is the word as typed; with ``lazy`` (nobody has
    a row under the exact word) also without its final s; never a longer word. "Dev's swimming with Asha" is not Asha's swimming."""
    alts = [owner]
    if lazy and _may_be_lazy(owner):
        alts.append(owner[:-1])
    alt = "|".join(re.escape(a) for a in sorted(alts, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9'’-])(?:{alt})(?:['’]s)?\s+{re.escape(noun)}(?![A-Za-z0-9-])", re.IGNORECASE)


class Correction(NamedTuple):
    subject: str             # a name ("Oliver") or "my <relation>"
    new: str
    old: Optional[str]       # None: an age restated with no old value ("Anaya is 7 now")
    cued: bool = False
    lazy: bool = False       # the subject is a bare name typed WITHOUT its possessive apostrophe ("kais 21 not 22"): the only plain-name form that may shorten


def statement(text: str) -> Optional[Correction]:
    """The subject-keyed value correction in ``text``, or None. Pure. Each sentence is read on its own; the first wins.

    A value pair ("<subject> is <new>, not <old>") needs the two values to be the same KIND of thing (``same_shape``: no
    clause, no verb) and, unless both are numbers, a correction cue ("correction", "actually", "sorry", "no", "I meant"...) -
    "Tom is tall, not short" with no cue and no number is a description. "<subject> is <N> now" restates an age."""
    from correction_apply import same_shape

    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Za-z])", " ".join((text or "").split())):
        if not sentence or len(sentence.split()) > MAX_WORDS:
            continue
        m = _PAIR_RE.match(sentence)
        if m:
            subj = m.group("subj").strip()
            if subj.lower() in _NOT_SUBJECT:
                continue
            new, old = _bare_value(m.group("n")), _bare_value(m.group("o"))
            if new.lower() == old.lower():
                continue
            cued = bool((m.group("cue") or "").strip()) or bool(re.search(r"\b(?:actually|really)\s+\S", sentence, re.IGNORECASE))
            both_num = value_of(new) is not None and value_of(old) is not None
            if not both_num and (not cued or not same_shape(new, old)):
                continue
            return Correction(subj, new, old, cued)
        m = _BARE_RE.match(sentence)
        if m:
            subj = m.group("subj").strip()
            if subj.lower() in _NOT_SUBJECT or subj.lower() in _BARE_NOT_NAME:
                continue
            typed_apostrophe = bool(re.search(r"['’]s$", subj))
            subj = re.sub(r"['’]s$", "", subj)      # "kais" stays as typed: a lazy possessive OR a name ending in s - the row decides
            if subj.lower() in _NOT_SUBJECT or subj.lower() in _BARE_NOT_NAME or subj.lower().rstrip("s") in _NOT_SUBJECT:
                continue                    # "thats 21 not 22" is no name
            new, old = m.group("n").strip(), m.group("o").strip()
            if value_of(new) is not None and value_of(old) is not None and value_of(new) != value_of(old):
                return Correction(subj, new, old, True, not typed_apostrophe)
            continue
        m = _FOR_RE.match(sentence)
        if m:
            subj = m.group("subj").strip()
            new, old = m.group("n").strip(), m.group("o").strip()
            if subj.lower() not in _NOT_SUBJECT and value_of(new) != value_of(old):
                return Correction(subj, new, old, True)
            continue
        m = _AGE_NOW_RE.match(sentence)
        if m:
            subj = m.group("subj").strip()
            if subj.lower() in _NOT_SUBJECT:
                continue
            cued = bool((m.group("cue") or "").strip() or m.group("pre") or m.group("post")
                        or re.search(r"\bturn(?:ed|s)\b", sentence, re.IGNORECASE))
            if not cued:
                continue            # "Oliver is 25" with nothing saying it replaces a number is just a statement
            return Correction(subj, m.group("n").strip(), None, True)
        m = _QPAIR_RE.match(sentence)
        if m:
            # a quantity pair: "the dentist appointment is the 27th of november not the 20th", "eli is 9 not 8, he had his birthday".
            # Two numbers of the same subject are a correction on their own (as the plain number pair above), no cue needed.
            subj = m.group("subj").strip()
            new, old = m.group("n").strip(), m.group("o").strip()
            if _not_a_subject(subj) or not is_quantity_pair(new, old):
                continue
            qn, qo = quantity_value(new), quantity_value(old)
            if qn.value == qo.value and (_month_of(new) or "") == (_month_of(old) or ""):
                continue
            # "9 years old not 8 years old" is the plain age pair "9 not 8": with the suffix left on, it missed the age path and the
            # unrestricted matcher rewrote ANY 8 of that subject ("Eli has 8 dogs" became 9 dogs)
            bare_n, bare_o = _AGE_SUFFIX_RE.sub("", new), _AGE_SUFFIX_RE.sub("", old)
            if value_of(bare_n) is not None and value_of(bare_o) is not None:
                new, old = _bare_value(bare_n), _bare_value(bare_o)       # a plain age pair: the age path rewrites it
            return Correction(subj, new, old, bool((m.group("cue") or "").strip()))
        m = _MOVED_RE.match(sentence)
        if m:
            subj, new, old = m.group("subj").strip(), m.group("n").strip(), (m.group("o") or "").strip() or None
            subj = re.sub(r"(?i)\s+(?:has|have|got|was|is|been|just)$", "", subj)     # "my exam got moved": the subject is "my exam"
            qn = quantity_value(new)
            if _not_a_subject(subj) or qn is None or not (qn.ordinal or _month_of(new)):
                continue                    # only a DATE moves; "the meeting moved to friday" is no number
            if old is not None and (quantity_value(old) is None or _same_value(new, old)):
                continue
            return Correction(subj, new, old, True)
    return None


def _not_a_subject(subj: str) -> bool:
    """A subject that is a pronoun / filler ("it", "thats"), or a bare two-word one holding such a word ("so it", "i think")."""
    words = subj.lower().split()
    if subj.lower() in _NOT_SUBJECT:
        return True
    return len(words) == 2 and words[0] not in ("the", "my", "our") and any(
        w in _NOT_SUBJECT or w in _BARE_NOT_NAME or w in ("is", "was", "think", "guess", "reckon", "got", "has") for w in words)


def _subject_re(subject: str, lazy: bool = False):
    """Whole-word match of the subject in a stored row: a name as is, "my <rel>" as the relation word ("User's dad ...")."""
    s = subject.strip()
    on = owner_noun(s)
    if on:
        return _owner_noun_re(on[0], on[1], lazy)
    m = re.match(rf"(?i)(?:my|our)\s+({_REL})$", s)
    if m:
        # the USER's relative only: "User's dad ...", "my dad ...", or a row that opens on it - never "Arjun's dad ..."
        return re.compile(rf"(?:^|\buser['’]s\s+|\b(?:my|our)\s+){re.escape(m.group(1))}(?![A-Za-z0-9-])", re.IGNORECASE)
    m = re.match(r"(?i)(my|our)\s+(.+)$", s)
    if m:
        # the USER's own <noun> ("my birthday"): "User's birthday ..." or an unpossessed mention, never "Oliver's birthday ..."
        noun = re.escape(m.group(2))
        return re.compile(rf"(?:\buser['’]s\s+{noun}|(?<!['’]s\s)(?<![A-Za-z0-9'’]){noun})(?![A-Za-z0-9])", re.IGNORECASE)
    m = re.match(r"(?i)the\s+(.+)$", s)
    if m:
        s = m.group(1)          # "the pitch" -> any row that names the pitch ("User has a board pitch on Thursday")
    stem = s[:-1] if lazy and _may_be_lazy(s) else None
    if stem:
        # a name typed with a stray s ("kais 21 not 22" for Kai): the word as typed, or without its s
        return re.compile(rf"(?<![A-Za-z0-9'’-])(?:{re.escape(s)}|{re.escape(stem)}(?:s(?=:))?)(?![A-Za-z0-9-])", re.IGNORECASE)
    # exactly the word: "Carlo" never reaches "Carlos: 22", and a hyphenated part is never the word ("ana" is not "Ana-Lise")
    return re.compile(rf"(?<![A-Za-z0-9'’-]){re.escape(s)}(?![A-Za-z0-9-])", re.IGNORECASE)


_AGE_IN_ROW = rf"(?P<pre>\b(?:is|aged?|turned|turns|was)\s+)(?P<num>{_NUM})(?P<half>{_HALF})?\b(?P<post>{_YEARS})"
# the compact "Name: 22" row: the colon must come right after the subject, and a number that is the hour of a time ("10:22") is no age
_COMPACT_AGE = re.compile(rf"^(?P<pre>\s*:\s*)(?P<num>{_NUM})(?P<half>{_HALF})?\b(?!:\d)(?:(?P<post>\s+(?:years?[ -]old|yrs?\s+old|yo))|(?=\s*(?:[,;.(]|$)))", re.IGNORECASE)


def _age_matches(tail: str) -> list:
    """The age numbers in the text after the subject: a compact ``: N`` straight after it, then every "is/aged/turned N"."""
    out = []
    cm = _COMPACT_AGE.match(tail)
    if cm:
        out.append(cm)
    out.extend(re.finditer(_AGE_IN_ROW, tail, re.IGNORECASE))
    return out


def _row_value(am) -> Optional[float]:
    n = number_of(am.group("num"))
    return None if n is None else n + (0.5 if am.group("half") else 0.0)


def _render_value(v: float, like: str) -> str:
    """``v`` (7 or 3.5) written the way the row's number was ("three" -> "three and a half")."""
    whole = int(v)
    return number_like(whole, like) + (" and a half" if v - whole else "")


def _ordinal_suffix(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _render_quantity(new: Quantity, like: Quantity) -> str:
    """The corrected number as the member said it, matched to the row's ordinal / plain form ("twentieth" -> "27th", "20 November"
    -> "27 November")."""
    txt = new.text
    m = re.fullmatch(r"(\d+)(st|nd|rd|th)?", txt, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        return f"{n}{_ordinal_suffix(n)}" if like.ordinal else str(n)
    return txt


def _is_day_of_month(text: str, q: Quantity) -> bool:
    """The number at ``q`` is written as a day of the month: an ordinal ("20th", "twentieth"), or a number with a month name as its
    neighbour ("20 November", "November 20", "20th of November"). Pure."""
    if q.ordinal:
        return True
    after = re.match(r"\s*(?:of\s+)?([A-Za-z]+)", text[q.end:])
    before = re.search(r"([A-Za-z]+)\s*$", text[:q.start])
    return bool((after and _month_of(after.group(1))) or (before and _month_of(before.group(1))))


#: the unit a number's neighbouring word names: money words, and measures read reliably from the word after the number
_MONEY_WORDS = frozenset({"dollar", "dollars", "bucks", "buck", "cents", "cent", "grand", "aud", "usd"})
_MEASURE_WORDS = {
    **dict.fromkeys(("km", "kms", "kilometre", "kilometres", "kilometer", "kilometers"), "km"),
    **dict.fromkeys(("kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"), "kg"),
    **dict.fromkeys(("min", "mins", "minute", "minutes"), "minute"),
    **dict.fromkeys(("hr", "hrs", "hour", "hours"), "hour"),
    **dict.fromkeys(("sec", "secs", "second", "seconds"), "second"),
    **dict.fromkeys(("day", "days"), "day"), **dict.fromkeys(("week", "weeks"), "week"),
    **dict.fromkeys(("month", "months"), "month"), **dict.fromkeys(("year", "years"), "year"),
    **dict.fromkeys(("metre", "metres", "meter", "meters"), "metre"), **dict.fromkeys(("cm",), "cm"),
    **dict.fromkeys(("litre", "litres", "liter", "liters"), "litre"), **dict.fromkeys(("mile", "miles"), "mile"),
    **dict.fromkeys(("percent", "per cent", "%"), "percent"),
}


def _unit_at(text: str, q: Quantity) -> Optional[str]:
    """The UNIT of the number at ``q`` in ``text``, read from the number itself and its neighbours: "date" (a day of the month),
    "money" (a currency sign, dollars / bucks / cents / grand / AUD, or a scale word used as an amount: "12 thousand", "12k"), "age"
    ("9 years old", "aged 9", "turned 9"), a measure from the next word ("5 km", "20 minutes"), or None when no
    unit can be read (a bare number, "20 Example Street", "3 dogs"). Pure."""
    if _is_day_of_month(text, q):
        return "date"
    before, after = text[:q.start], text[q.end:]
    if "$" in q.text or re.search(r"[$£€]\s*$", before):
        return "money"
    words = re.match(r"\s*(%|[A-Za-z]+)(?:[ -]([A-Za-z]+))?", after)
    w1 = (words.group(1) or "").lower() if words else ""
    w2 = (words.group(2) or "").lower() if words else ""
    if w1 in _MONEY_WORDS:
        return "money"
    if (w1 in ("year", "years", "yrs") and w2 == "old") or w1 == "yo" or re.search(r"\baged?\s*$", before, re.IGNORECASE):
        return "age"
    # an age-row hit counts when it SAYS age ("turned 9", "Eli: 9 yo"); a bare "is 20" is no unit ("the bill is 20")
    if any(am.start("num") <= q.start < am.end("num") and (am.group("post") or re.search(r"turn|age", am.group("pre"), re.I))
           for am in _age_matches(text)):
        return "age"
    if w1 in _MEASURE_WORDS:
        return _MEASURE_WORDS[w1]
    if q.scaled:
        return "money"
    return None


def _correction_unit(corr: Correction, qn: Quantity, qo: Quantity) -> Optional[str]:
    """The unit the correction's own words give ("27 dollars not 20", "the 27th not the 20th"), or None for bare numbers."""
    return _unit_at(corr.new, qn) or _unit_at(corr.old, qo)


def _rewrite_quantity(head: str, tail: str, corr: Correction) -> Optional[str]:
    """The quantity half of ``rewrite``: the ONE number after the subject whose value is the old one (any spelling: "20th",
    "twentieth", "12 thousand", "twelve thousand"), replaced by the new one. An old value said without its scale takes the new
    one's ("13 thousand, not 12"); a month the correction names must not contradict the row's ("the 27th of november not the
    20th" never touches a row about the 20th of December). Several candidate numbers, or none, change nothing."""
    qn, qo = quantity_value(corr.new), quantity_value(corr.old)
    if qn is None or qo is None:
        return None
    want = {qo.value}
    if not qo.scaled and qn.scaled:
        want.add(qo.value * qn.scale)
    month_new, month_old = _month_of(corr.new), _month_of(corr.old)
    row_month = _month_of(tail)
    ctx = month_old or month_new
    if ctx and row_month and row_month != ctx:
        return None
    spans = quantities(tail)
    if not spans and len(quantities(head)) == 1:
        # the subject comes LAST ("User has saved twelve thousand dollars for the caravan fund"): a row holding exactly one
        # number is read whole; with two or more the number before the subject may be someone else's, so nothing changes
        head, tail = "", head + tail
        spans = quantities(tail)
        row_month = _month_of(tail)
        if ctx and row_month and row_month != ctx:
            return None
    # the number must be the corrected ATTRIBUTE, not just the same digits: a DATE correction ("the 27th not the 20th") rewrites a
    # day-of-month (an ordinal, or a number beside a month name), never "costs 20 dollars" or "20 Example Street"; and a correction
    # that is not a date never rewrites a day-of-month. The same holds for "already up to date": "on the 20th that costs 27
    # dollars" does not hold the corrected DATE just because 27 appears in it.
    date_corr = bool(qn.ordinal or qo.ordinal or month_new or month_old)
    unit = "date" if date_corr else _correction_unit(corr, qn, qo)
    units = [_unit_at(tail, q) for q in spans]
    # The SUBJECT's own number is the first number after the subject mention that COULD be the corrected value: of the
    # correction's unit, or unitless (never a date for a non-date correction, only a date for a date one); a number of another
    # unit ("on the 3rd", "a 27 km drive") is not the subject's value and is skipped. Only that number is ever judged:
    #   - it equals the new value: the row is up to date ("gas bill is 27, with a 20 dollar late fee" - the fee is not touched);
    #   - it is the old value and the only candidate of the unit: it is rewritten;
    #   - anything else: nothing changes, even when another number of the unit holds the old value (it may not be the subject's).
    if unit == "date":
        fits = lambda u: u == "date"                                    # noqa: E731
    elif unit is None:
        fits = lambda u: u != "date"                                    # noqa: E731 - bare numbers ("9 not 8")
    else:
        fits = lambda u: u == unit or u is None                         # noqa: E731
    own = next((q for q, u in zip(spans, units) if fits(u)), None)
    if own is None:
        return None
    if own.value == qn.value and (not month_new or month_new == row_month):
        return None                 # the row already holds the corrected value
    # the candidates: numbers of the correction's unit holding the old value; a unitless number only when none is (never for a
    # date: "20 Example Street" is not the 20th). Two candidates are never guessed between.
    hits = [q for q, u in zip(spans, units) if q.value in want and (u == unit if unit is not None else fits(u))]
    if not hits and unit not in (None, "date"):
        hits = [q for q, u in zip(spans, units) if q.value in want and u is None]
    if len(hits) != 1 or hits[0] is not own:
        return None
    q = hits[0]
    if unit == "date" and (not head or not _date_attached(tail, q)):
        return None                 # the date may be someone else's ("run by Bea, whose birthday is on the 20th"): unchanged
    return head + _put_date(tail, q, qn, month_new if unit == "date" else None)


_RELATIVE_RE = re.compile(r"\b(?:whose|who|whom|which|her|his|their|its)\b", re.IGNORECASE)
_KIN_RE = re.compile(rf"\b{_REL}\b", re.IGNORECASE)
_DAY_NAMES = frozenset({"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"})


def _date_attached(tail: str, q: "Quantity") -> bool:
    """Is the date at ``q`` the corrected SUBJECT's own (``tail`` starts right after the subject mention)? Conservative (QA round 9
    review: "book club is run by Bea, whose birthday is on the 12th" had Bea's birthday rewritten): no other person (a capitalised
    name, a kin word) and no relative clause (whose / who / which / her / his / their ...) between the subject and the date. One
    exception, shown to be the subject's: a comma-bounded aside straight after the subject with no relative word and no number,
    followed by the subject's own verb ("book club, run by Bea, is on the 12th"). Pure."""
    between = tail[:q.start]
    aside = re.match(r"^\s*,\s*([^,]*?)\s*,\s*(?=(?:is|was|are|will\s+be|has\s+been|got|gets)\b)", between, re.IGNORECASE)
    if aside and not _RELATIVE_RE.search(aside.group(1)) and not re.search(r"\d", aside.group(1)):
        between = between[aside.end():]
    if _RELATIVE_RE.search(between) or _KIN_RE.search(between):
        return False
    names = [w for w in re.findall(r"\b[A-Z][a-z]+\b", between)
             if not _MONTH_RE.fullmatch(w) and w.lower() not in _DAY_NAMES and w not in ("User", "I")]
    return not names


def _put_date(tail: str, q: "Quantity", qn: "Quantity", month_new: Optional[str]) -> str:
    """``tail`` with the number at ``q`` replaced by the new one; a month the correction NAMED replaces the month beside the date
    ("the 12th of December" / "December 12"), or is added when the row has none ("on the 12th" -> "on the 5th of January", QA round
    9 review: the stated month was dropped). With no month said, the row's month is kept. Pure."""
    rendered, before, after = _render_quantity(qn, q), tail[:q.start], tail[q.end:]
    if month_new:
        month = month_new.capitalize()
        ma = re.match(r"(\s*(?:of\s+)?)([A-Za-z]+)", after)
        mb = re.search(r"([A-Za-z]+)(\s*)$", before)
        if ma and _MONTH_RE.fullmatch(ma.group(2)):
            after = after[:ma.start(2)] + month + after[ma.end(2):]
        elif mb and _MONTH_RE.fullmatch(mb.group(1)):
            before = before[:mb.start(1)] + month + before[mb.end(1):]
        else:
            rendered += f" of {month}"
    return before + rendered + after


def _restate_date(head: str, tail: str, new: str) -> Optional[str]:
    """A MOVED date with no old value ("book club moved again, its on the 19th of december"): the row's ONE day-of-month after the
    subject becomes the new one. No date, or two (which one moved?), changes nothing; a row already on the new date is left alone."""
    qn = quantity_value(new)
    if qn is None or not (qn.ordinal or _month_of(new)):
        return None
    dates = [q for q in quantities(tail) if _unit_at(tail, q) == "date"]
    if len(dates) != 1:
        return None
    q, month_new, row_month = dates[0], _month_of(new), _month_of(tail)
    if q.value == qn.value and (not month_new or month_new == row_month):
        return None
    if not _date_attached(tail, q):
        return None                 # the one date is not clearly the subject's: unchanged
    if not _SCHEDULED_BEFORE_RE.search(_date_expr_start(tail, q)):
        return None                 # "was founded on the 12th": not when it HAPPENS, so a moved date never touches it
    return head + _put_date(tail, q, qn, month_new)


#: the ONLY phrases that introduce a date a "moved to" / restated date may replace (QA round 9 review: "moved to the 19th" rewrote
#: "was founded on the 12th"); any other verb in front of the date (founded, born, started, since, married, joined, died) leaves it
_SCHEDULED_BEFORE_RE = re.compile(r"\b(?:is\s+on|is\s+at|meets\s+on|happens\s+on|is\s+scheduled\s+for|moved\s+to|was\s+set\s+for)"
                                  r"\s+(?:the\s+)?$", re.IGNORECASE)


def _date_expr_start(tail: str, q: "Quantity") -> str:
    """``tail`` up to where the date at ``q`` starts, the month counted as part of the date when it comes first ("on December 12")."""
    before = re.search(r"([A-Za-z]+)\s+$", tail[:q.start])
    if before and _MONTH_RE.fullmatch(before.group(1)):
        return tail[:before.start(1)]
    return tail[:q.start]


def rewrite(row_text: str, corr: Correction, lazy_s: bool = False) -> Optional[str]:
    """``_rewrite_row``, refused (None) when the result would hold LESS than the row did (``drops_value``). Pure."""
    out = _rewrite_row(row_text, corr, lazy_s)
    return None if out is not None and drops_value(row_text, out) else out


def _rewrite_row(row_text: str, corr: Correction, lazy_s: bool = False) -> Optional[str]:
    """``row_text`` with the corrected value, or None when the row is not the fact being corrected. Pure.

    The row must name the subject; a value pair needs the old value as a whole word AFTER the subject's first mention (the
    value is the subject's, not an earlier name's) and not already hold the new one; an age restatement rewrites the one age
    number that follows the subject ("Anaya is six", "Oliver, aged 24"). A row that mentions another number first is left alone."""
    text = row_text or ""
    sm = _subject_re(corr.subject, lazy_s).search(text)
    if not sm:
        return None
    tail = text[sm.end():]
    if corr.old is not None:
        old_n, new_n = value_of(corr.old), value_of(corr.new)
        if old_n is not None and new_n is not None:
            # an AGE pair: the subject's number, whatever its spelling ("24" == "twenty-four", "3" -> "3 and a half")
            for am in _age_matches(tail):
                if _row_value(am) == old_n:
                    return (text[:sm.end()] + tail[:am.start("num")] + _render_value(new_n, am.group("num"))
                            + tail[(am.end("half") if am.group("half") else am.end("num")):])
            return None
        if is_quantity_pair(corr.new, corr.old):
            return _rewrite_quantity(text[:sm.end()], tail, corr)
        old_re = re.compile(rf"(?<![A-Za-z0-9'’]){re.escape(corr.old)}(?![A-Za-z0-9'’])", re.IGNORECASE)
        if re.search(rf"(?<![A-Za-z0-9'’]){re.escape(corr.new)}(?![A-Za-z0-9'’])", tail, re.IGNORECASE):
            return None
        om = old_re.search(tail)
        if not om:
            return None
        new = corr.new[:1].upper() + corr.new[1:] if om.group(0)[:1].isupper() else corr.new
        return text[:sm.end()] + tail[:om.start()] + new + tail[om.end():]
    new_n = value_of(corr.new)
    if new_n is None:
        return _restate_date(text[:sm.end()], tail, corr.new)
    ages = _age_matches(tail)
    if not ages:
        return None
    am = ages[0]
    old_n = _row_value(am)
    if old_n is None or old_n == new_n:
        return None
    return (text[:sm.end()] + tail[:am.start("num")] + _render_value(new_n, am.group("num"))
            + tail[(am.end("half") if am.group("half") else am.end("num")):])


_FILLER = frozenset({"the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "for", "in", "on", "at", "and", "or", "user", "users",
                     "not", "it", "its", "that", "this", "has", "have", "had", "now", "year", "years", "old", "aged", "age", "yo",
                     "corrected", "actually", "turned", "turns"})


def invented_on_correction_turn(fact: str, user_message: str) -> bool:
    """True for a digest fact the correction turn cannot have said: it holds the corrected (new or old) value but names a field
    or word the turn never used. QA 6b: "actually its 76 for margaret, not 78" produced "User's blood pressure is 78" - the digest
    attached the stray number to an invented attribute of the user. The correction itself is applied to the stored rows
    (``apply``); the digest writes nothing new about a corrected value. Pure; False when the turn is not a correction."""
    corr = statement(user_message)
    if corr is None or not (fact or "").strip():
        return False
    values = [v for v in (corr.new, corr.old) if v]
    low = (fact or "").lower()
    subj = re.sub(r"(?i)^(?:the|my|our)\s+", "", corr.subject.strip()).lower()
    if subj and re.search(rf"(?<![a-z0-9-]){re.escape(subj)}(?![a-z0-9-])", low):
        # a fact that names the corrected subject ("User's wife Margaret is 76") is about the correction - unless it gives the subject
        # a KIND the member never said: "kit is 10 not 9" is never "User's pet is named Kit" (QA round 9)
        return invented_kind(fact, user_message)
    if not any(re.search(rf"(?<![A-Za-z0-9]){re.escape(v.lower())}(?![A-Za-z0-9])", low) for v in values):
        return False
    said = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", (user_message or "").lower().replace("'s", ""))}
    for w in re.findall(r"[a-z0-9]+", low.replace("'s", "")):
        if w in _FILLER or w.rstrip("s") in said:
            continue
        return True
    return False


def invented_kind(fact: str, said_text: str) -> bool:
    """Does ``fact`` call something an animal ("pet", "cat") with a word ``said_text`` never used? A model's guess about what KIND
    of thing a name is never becomes a fact. Pure."""
    from memory_quality import ANIMAL_WORDS

    said = {w.rstrip("s") for w in re.findall(r"[a-z]+", (said_text or "").lower())}
    return any(w in ANIMAL_WORDS and w.rstrip("s") not in said for w in re.findall(r"[a-z]+", (fact or "").lower()))


def kind_guess_on_correction(fact: str, turns: list) -> bool:
    """The transcript-level ``invented_on_correction_turn`` (idle consolidation re-mines a whole session): ``fact`` names a subject
    one of ``turns`` corrected, and types it with an animal word none of the turns said. Pure."""
    low = (fact or "").lower()
    for t in turns or ():
        corr = statement(t)
        subj = re.sub(r"(?i)^(?:the|my|our)\s+", "", corr.subject.strip()).lower() if corr else ""
        if subj and re.search(rf"(?<![a-z0-9-]){re.escape(subj)}(?![a-z0-9-])", low):
            return invented_kind(fact, "\n".join(turns))
    return False


# -- a correction outlives the transcripts written before it (QA round 8) -------------------------------------------------------------
# The idle consolidation re-reads a whole EARLIER session minutes after it went quiet. In the round, the member corrected a date in
# session 2; session 1 was consolidated a minute later and wrote the corrected-away date again ("... on the 20th of November"),
# approved, beside the correction. A fact a later transcript writer mines from words said BEFORE the correction is stale when it names
# the corrected subject and holds the old value: ``superseded_by_correction`` says so and the writer drops it. In process, bounded,
# for ``REMEMBER_S`` (the consolidation runs within minutes; a restart forgets, the cost is one stale near-duplicate).
REMEMBER_S = 6 * 3600
_REMEMBERED: "dict[str, list[tuple[float, Correction]]]" = {}
_REMEMBER_MAX_USERS = 256


def _subject_key(subject: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"(?i)^(?:the|my|our)\s+", "", (subject or "").strip())).lower().replace("\u2019", "'")


def _same_value(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    qa, qb = quantity_value(a), quantity_value(b)
    if qa is not None and qb is not None:
        ma, mb = _month_of(a), _month_of(b)            # "the 20th" and "the 20th of november" are the same value: a month only
        return qa.value == qb.value and (not ma or not mb or ma == mb)      # disagrees when both name one
    return re.sub(r"\s+", " ", a.strip().lower()) == re.sub(r"\s+", " ", b.strip().lower())


def _remember(user_id: str, corr: Correction) -> None:
    import time

    uid = (user_id or "").strip().lower()
    if not uid:
        return                      # a RESTATEMENT (old None) is remembered too: see superseded_by_correction
    now = time.time()
    keep = [(t, c) for t, c in _REMEMBERED.get(uid, []) if now - t <= REMEMBER_S]
    keep.append((now, corr))
    _REMEMBERED[uid] = keep[-16:]
    while len(_REMEMBERED) > _REMEMBER_MAX_USERS:
        _REMEMBERED.pop(next(iter(_REMEMBERED)))


def superseded_by_correction(user_id: str, fact: str, said_at: Optional[float] = None) -> bool:
    """True when ``fact`` holds a value THIS member corrected away in the last ``REMEMBER_S`` (it names the corrected subject and
    the old value, so ``rewrite`` would change it). ``said_at`` (epoch seconds) is when the words the fact was mined from were LAST
    said: only a correction made AFTER them can make the fact stale. "12k, no 13.5k", then days later "it is 12k again" is a new
    fact, not the corrected-away one - without the time the newer balance was dropped. Unknown time (None) keeps the old behaviour.
    Pure apart from the in-process registry; never raises."""
    import time

    try:
        uid = (user_id or "").strip().lower()
        now = time.time()
        live = [(t, c) for t, c in _REMEMBERED.get(uid, []) if now - t <= REMEMBER_S and (said_at is None or t > said_at)]
        latest: dict = {}
        for t, c in live:                               # in order said: the last one per subject is where the value stands now
            latest[_subject_key(c.subject)] = c
        for t, corr in live:
            # EVERY value a subject was corrected away from stays stale (20 -> 27 -> 28: both 20 and 27), except the one the member
            # corrected BACK to (27 not 20, then 20 not 27: the 20 is the latest fact, only the 27 is stale)
            last = latest[_subject_key(corr.subject)]
            if corr is not last and _same_value(corr.old, last.new):
                continue
            if corr.old is None and corr is not last:
                continue            # a restatement ("moved to the 19th") is only ever judged as the subject's LATEST value
            if rewrite(fact or "", corr) is not None:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def drops_value(old_text: str, new_text: str) -> bool:
    """Would replacing ``old_text`` with ``new_text`` leave the subject with LESS (QA round 10: "Tobin: turned 6" was replaced by
    "Tobin: next month" and the age was gone)? True when the new text is empty while the old one is not, or when a value the old
    text holds (a number, a date, an amount) has no counterpart of its unit in the new text. Values are COUNTED, one new value
    standing in for one old value: a date needs a date; an age, amount or measure needs the same unit or a bare number; a bare
    number needs any number that is not a date. So "runs from the 3rd to the 5th" -> "on the 10th" (two dates, one left) is
    refused, as is two amounts -> one. Accepted conservative outcome: a real correction that REDUCES the count ("from the 3rd to
    the 5th" -> "only on the 4th") is refused too, and both rows stay. And at most ONE old value may be missing from the new text
    (a value = date or not, plus its number, any spelling: "6" and "six" are one value), so another subject's value never stands
    in for a lost one ("X is 6 and Y is 9" -> "X's age is unknown; Y is 10 and Z is 4" loses two: refused). A real correction
    changes one value. Accepted conservative outcome: a two-value correction in one go ("from the 3rd to the 5th" -> "from the
    4th to the 6th") is refused too and both rows stay: never less outranks a tidy merge. Text with no number is never judged. A
    refused replacement leaves the row as it was. Pure; never raises."""
    try:
        from collections import Counter

        old, new = old_text or "", new_text or ""
        if not old.strip():
            return False
        if not new.strip():
            return True
        qo = quantities(old)
        if not qo:
            return False
        pool = Counter(_unit_at(new, q) for q in quantities(new))

        def take(*choices) -> bool:
            for c in choices:
                if pool[c] > 0:
                    pool[c] -= 1
                    return True
            return False

        # the most specific first: a date only a date covers; a named unit its own, then a bare number; a bare old number last,
        # by any unused value that is not a date
        for u in sorted((_unit_at(old, q) for q in qo), key=lambda x: 0 if x == "date" else 2 if x is None else 1):
            if u == "date":
                ok = take("date")
            elif u is None:
                ok = take(None, *[x for x in pool if x not in ("date", None)])
            else:
                ok = take(u, None)
            if not ok:
                return True

        def values(text: str) -> Counter:
            return Counter((_unit_at(text, q) == "date", q.value) for q in quantities(text))

        return sum((values(old) - values(new)).values()) > 1
    except Exception:  # noqa: BLE001 - a pair that cannot be read is never replaced
        return True


def _displaced(old_text: str, new_text: str) -> Optional[str]:
    """The words a rewrite REPLACED in a row ("on the 12th of December" -> "on the 19th of December": "12th"; with the month
    changed too: "12th December"), punctuation stripped, or None. Pure."""
    import difflib

    a, b = old_text.split(), new_text.split()
    gone = [w for op, i1, i2, _j1, _j2 in difflib.SequenceMatcher(None, a, b).get_opcodes() if op != "equal" for w in a[i1:i2]]
    out = " ".join(re.sub(r"[^\w'’-]+$", "", w) for w in gone).strip()
    return out or None


def _displaced_value(old_text: str, new_text: str, corr: Correction) -> Optional[str]:
    """What a restatement displaced from ``old_text``. A DATE: the WHOLE date expression as the row has it (day, its month and a
    year when present: "12th of December"), never the word diff - a month-only move ("12th of December" -> "12th of January")
    diffs to "December" alone, which no stale full-date fact matches (QA round 9 review). Anything else: the word diff. Pure."""
    qn = quantity_value(corr.new)
    if qn is None or not (qn.ordinal or _month_of(corr.new)):
        return _displaced(old_text, new_text)
    sm = _subject_re(corr.subject).search(old_text or "")
    if not sm:
        return None
    tail = old_text[sm.end():]
    dates = [q for q in quantities(tail) if _unit_at(tail, q) == "date"]
    return _date_expr(tail, dates[0]) if len(dates) == 1 else None


def _date_expr(text: str, q: "Quantity") -> str:
    """The date at ``q`` with the month beside it and a year after it, as written ("12th of December 2026", "December 12"). Pure."""
    start, end = q.start, q.end
    after = re.match(r"\s*(?:of\s+)?([A-Za-z]+)", text[end:])
    before = re.search(r"([A-Za-z]+)\s+$", text[:start])
    if after and _MONTH_RE.fullmatch(after.group(1)):
        end += after.end()
    elif before and _MONTH_RE.fullmatch(before.group(1)):
        start = before.start(1)
    year = re.match(r",?\s+(\d{4})\b", text[end:])
    if year:
        end += year.end()
    return text[start:end].strip()


def reset_state() -> None:
    _REMEMBERED.clear()


async def _distinct_people(rows: list, subject: str, user_id: str) -> int:
    """How many DISTINCT people the rows a correction would rewrite belong to, from data only. (1) The rows' own linkage: a
    person id (``entity_id`` of an ``entity_type`` person row, never a pending ``slug:``) - two ids are two people. (2) Rows with
    no such linkage: the people graph for the typed NAME (``people_graph`` via ``person_extractor._resolve_person``): an
    ambiguous answer is that many people. Neither can tell (no linkage, the graph unique / empty / unreadable, or the subject is
    no name: "my son", "the dentist"): 1, i.e. today's behaviour, every matching row rewritten. Never raises."""
    ids = set()
    unlinked = 0
    for r in rows:
        meta = getattr(r, "metadata", None) or {}
        eid = str(meta.get("entity_id") or "").strip()
        if str(meta.get("entity_type") or "").strip().lower() == "person" and eid and not eid.startswith("slug:"):
            ids.add(eid)
        else:
            unlinked += 1
    if len(ids) > 1:
        return len(ids)
    name = (owner_noun(subject) or (subject,))[0]
    if not unlinked or re.match(r"(?i)^(?:the|my|our|a|an)\s", name or "") or not re.match(r"^[A-Za-z][A-Za-z'’\-]*$", name or ""):
        return max(len(ids), 1)
    try:
        import person_extractor as pe

        db, opened = await pe._ensure_db(None)
        if db is None:
            return max(len(ids), 1)
        try:
            res = await pe._resolve_person(name, user_id, db)
        finally:
            if opened:
                await db.close()
        if res is not None and getattr(res, "ambiguous", False):
            return max(len(getattr(res, "matches", ()) or ()), 2)
    except Exception:  # noqa: BLE001
        pass
    return max(len(ids), 1)


async def apply(user_message: str, user_id: str, session_id: Optional[str], *, svc=None,
                speaker_verified: Optional[bool] = None) -> int:
    """Supersede the member's stored rows the correction in ``user_message`` is about. Returns the rows rewritten (0 = nothing
    changed). An unverified speaker, an unidentified member or an opted-out member changes nothing. Never raises."""
    if speaker_verified is False:
        return 0
    corr = statement(user_message)
    if corr is None:
        return 0
    try:
        from correction_apply import SOURCE, _is_unidentified, note_correction

        if _is_unidentified(user_id):
            return 0
        try:
            import user_prefs

            if await user_prefs.is_memory_opted_out(user_id):
                return 0
        except Exception:  # noqa: BLE001
            pass
        if svc is None:
            from memory_service import get_memory_service

            svc = get_memory_service()
        uid = user_id.strip().lower()
        hits = []
        exact_subject_row = False
        exact_re = _subject_re(corr.subject, False)
        _on = owner_noun(corr.subject)
        # the exact OWNER, whatever attribute the row is about: "Carlos is 22" means Carlos exists, so "carlos swimming" is never Carlo's
        owner_re = re.compile(rf"(?<![A-Za-z0-9'’-]){re.escape(_on[0])}(?![A-Za-z0-9-])", re.IGNORECASE) if _on else None
        for r in await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT):
            meta = r.metadata or {}
            if str(meta.get("user_id") or meta.get("wing") or "").strip().lower() != uid:
                continue
            if exact_re.search(r.text or "") or (owner_re and owner_re.search(r.text or "")):
                exact_subject_row = True
            new_text = rewrite(r.text or "", corr)
            if new_text and new_text != r.text:
                hits.append((r, new_text))
        if not hits and may_shorten(corr, exact_subject_row):
            # the memory rows alone cannot say whether the typed name is a real person: Carlos may be in the people graph with no row naming him
            # yet. Ask the SAME resolver apply_person_fact uses; only a positive "no such person" lets the shortening through.
            _typed = (owner_noun(corr.subject) or (corr.subject,))[0]
            _status = await person_status(_typed, user_id)
        else:
            _status = "skip"
        if not hits and _status == "none":
            # nobody has a row under the name exactly as typed: "kais 21 not 22" is Kai's (the apostrophe was not typed). Never the
            # other way round - "Carlos" does not rewrite Carlo's row while a Carlos row exists, nor when Carlos is the typed name.
            for r in await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT):
                meta = r.metadata or {}
                if str(meta.get("user_id") or meta.get("wing") or "").strip().lower() != uid:
                    continue
                new_text = rewrite(r.text or "", corr, lazy_s=True)
                if new_text and new_text != r.text:
                    hits.append((r, new_text))
        if len(hits) > 1:
            people = await _distinct_people([r for r, _t in hits], corr.subject, user_id)
            if people > 1:
                # the rows belong to MORE THAN ONE person who answers to this name (golden C27: two people with one name, both
                # rewritten): nothing is rewritten, the correction stays as said
                logger.info("VALUE_CORRECTION_AMBIGUOUS_SUBJECT user=%s rows=%d people=%d", user_id, len(hits), people)
                return 0
        if corr.old is not None and session_id and not _is_unidentified(user_id):
            # the misheard / misspoken fact may not be stored yet (the post-turn digest of an earlier turn lands minutes later):
            # that digest rewrites the old value before it writes (correction_apply.correct_fact)
            try:
                from memory_extractor import _load_recent_user_messages

                earlier = await _load_recent_user_messages(user_id, session_id, user_message)
                note_correction(user_id, session_id, corr.new, corr.old, user_message, list(earlier or []))
            except Exception:  # noqa: BLE001
                pass
        # Remembered whether or not a row was rewritten: the corrected-away fact may still be waiting in an earlier session's idle
        # pass (stated 15 s before the correction, filed minutes after it), and that pass must not store the old value. A
        # RESTATEMENT with no old value ("book club moved to the 19th") is remembered with the value it DISPLACED from each row it
        # rewrites (QA round 9 review: a late idle pass re-filed the 12th); with nothing rewritten, as a restatement of the subject
        # (any OTHER value of it, mined from turns older than this, is stale).
        displaced = ([_displaced_value(r.text or "", t, corr) for r, t in hits]
                     if corr.old is None and 0 < len(hits) <= MAX_ROWS else [])
        for old in [d for d in displaced if d] or [None]:
            _remember(user_id, corr._replace(old=old) if old else corr)
        if not hits or len(hits) > MAX_ROWS:
            return 0
        excerpt = " ".join(user_message.split())
        done = 0
        for r, new_text in hits:
            ref = await svc.review(
                r.id, decision="edit", edits=new_text, actor=SOURCE, note="spoken value correction (durable supersede)",
                source_excerpt=excerpt, anchor_text=excerpt, session_id=session_id,
                **({} if speaker_verified is None else {"speaker_verified": speaker_verified}))
            if ref is not None:
                done += 1
        if done:
            logger.info("VALUE_CORRECTION_APPLIED user=%s rows=%d", user_id, done)
        return done
    except Exception as exc:  # noqa: BLE001 - a correction never breaks the turn
        logger.warning("value_correction failed user=%s: %s", user_id, type(exc).__name__)
        return 0
