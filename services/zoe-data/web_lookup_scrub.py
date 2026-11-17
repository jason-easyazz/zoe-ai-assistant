"""The web-lookup QUERY SCRUBBER: what leaves the house is a minimal query.

``scrub_query(raw, terms)`` is a PURE function (no I/O beyond one cached read of
``lexicons_data/web_lookup_<lang>.json``, no clock, no logging). It turns the
brain's proposed search query into the few public words worth sending, or
refuses. Rule of the house: **when in doubt, do not send.**

What is stripped:
  * every known private NAME (members, contacts, pets) and PLACE, however it is
    written: any case, possessive, accents, dotted / hyphenated, zero-width
    characters, full-width letters, glued to another private word or a number;
  * a name that follows a relation word ("my friend Dana", "dog called Biscuit");
  * anything from MEMORY: a run of two or more content words shared with a
    memory text, its numbers, and its mid-sentence capitalised words;
  * e-mail addresses, phone and other long numbers, coordinates, street addresses;
  * people-pointing and filler words ("my", "our", "please") - the query is keywords.

What refuses the whole lookup: a link, an encoded-looking token, letters spelled
out one by one, a word mixing two alphabets, an over-long query, a query that is mostly memory, or one with
too little left after stripping to be a question about the public world.

The result never carries what was removed - only how many and of which kind.
Never log ``raw``; the kinds and the reason are safe to log.
Operator doc: docs/knowledge/web-lookup.md.
"""

from __future__ import annotations

import gzip
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

MAX_RAW_CHARS = 300  # the sidecar already slices the tool argument to this
MAX_WORDS = 10
MAX_CHARS = 100
MIN_PRIVATE_LEN = 3  # a private word shorter than this only matches as a whole token
EMBEDDED_PRIVATE_LEN = 6  # a private word this long is stripped wherever it appears inside a token
ENCODED_TOKEN_CHARS = 24

REASON_OK = "ok"
REASON_EMPTY = "empty"
REASON_TOO_LONG = "too_long"
REASON_LINK = "link"
REASON_ENCODED = "encoded"
REASON_SPELLED_OUT = "spelled_out"
REASON_MIXED_SCRIPT = "mixed_script"
REASON_MEMORY_HEAVY = "memory_heavy"
REASON_PRIVATE_ONLY = "private_only"
REASON_TRIPWIRE = "tripwire"
REASON_NO_ALLOW_LIST = "no_allow_list"
REASON_ERROR = "error"   # a broken lexicon or list: refuse rather than raise

_LEXICON_DIR = Path(__file__).resolve().parent / "lexicons_data"
_WORD_RE = re.compile(r"[^\W_]+(?:['’.\-][^\W_]+)*")
# A link: a scheme, www., or a domain (any top-level domain) followed by a path, query, fragment or port, which
# is what lets a link carry data ("example.com?token=..."). A bare domain alone ("sites like reddit.com") is a
# word, not a link.
_LINK_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://|\bwww\.|\b[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.[a-z]{2,24}[/?#:]")
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_COORD_RE = re.compile(r"-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}")
_PHONE_RE = re.compile(r"(?<![^\W_])\+?\d[\d\s().\-/–—·]{5,}\d(?![^\W_])")   # any separator: 0491/570/006 too
_HEX_RE = re.compile(r"(?i)^[0-9a-f]{16,}$")
_LOOKALIKE_SCRIPTS = frozenset({"LATIN", "CYRILLIC", "GREEK", "ARMENIAN", "CHEROKEE"})
_PART_RE = re.compile(r"[^\W_]+")
# Digits and signs written in place of letters ("m1rabel", "t0biah").
_LOOKALIKE = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "$": "s", "@": "a"})


@dataclass(frozen=True, slots=True)
class PrivateTerms:
    """What the caller knows to be private. Texts, never ids."""

    names: frozenset[str] = frozenset()
    places: frozenset[str] = frozenset()
    memory: tuple[str, ...] = ()

    @classmethod
    def of(cls, names: Iterable[str] = (), places: Iterable[str] = (), memory: Iterable[str] = ()) -> "PrivateTerms":
        return cls(frozenset(n for n in names if n), frozenset(p for p in places if p), tuple(m for m in memory if m))


@dataclass(frozen=True, slots=True)
class ScrubResult:
    send: bool
    query: str  # "" unless ``send``
    reason: str
    stripped: int = 0
    kinds: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def load_lexicon() -> dict:
    """Every enabled ``web_lookup_<lang>.json``, word lists unioned. A language with no file adds nothing."""
    out: dict = {"answer": {}, "instruction_cues": []}
    for path in sorted(_LEXICON_DIR.glob("web_lookup_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("enabled"):
            continue
        for key in ("drop_words", "question_words", "relation_words", "naming_words", "street_types", "possessive_words", "unit_suffixes"):
            out[key] = frozenset(out.get(key, frozenset())) | {fold(w) for w in data.get(key, [])}
        out["instruction_cues"] = list(out["instruction_cues"]) + list(data.get("instruction_cues", []))
        out["answer"].setdefault(data.get("lang", path.stem), data.get("answer", {}))
    return out


def fold(text: str) -> str:
    """Compare-form of one word: NFKC, casefolded, accents and every non-alphanumeric removed, possessive dropped."""
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text or "").casefold())
    s = re.sub(r"['’]s$", "", s)
    return "".join(c for c in s if c.isalnum())


def _clean(raw: str) -> str:
    """NFKC; invisible format characters removed (so they cannot split a name); other controls become a space."""
    out = []
    for ch in unicodedata.normalize("NFKC", raw):
        cat = unicodedata.category(ch)
        if cat == "Cf":
            continue
        out.append(" " if cat.startswith("C") or cat.startswith("Z") else ch)
    return "".join(out)


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _private_singles(terms: PrivateTerms, lex: dict) -> tuple[set[str], list[tuple[str, ...]], set[str]]:
    """(single private words, multi-word private phrases, name words) in compare-form."""
    singles: set[str] = set()
    phrases: list[tuple[str, ...]] = []
    name_words: set[str] = set()
    for name in terms.names:
        parts = [fold(w) for w in _words(_clean(name))]
        parts = [p for p in parts if p]
        name_words.update(parts)  # every word of a person's name is private on its own
        # ...and so is each half of a hyphenated or apostrophised one ("Mary-Jane", "O'Brien" -> "brien").
        name_words.update(f for f in (fold(x) for x in _PART_RE.findall(_clean(name))) if len(f) >= MIN_PRIVATE_LEN)
        if len(parts) > 1:
            phrases.append(tuple(parts))
    for place in terms.places:
        parts = [p for p in (fold(w) for w in _words(_clean(place))) if p]
        if len(parts) == 1:
            singles.add(parts[0])
        elif parts:
            phrases.append(tuple(parts))
    for text in terms.memory:  # a capitalised word that does not open the text reads as a proper noun
        for i, w in enumerate(_words(_clean(text))):
            f = fold(w)
            if f and (any(c.isdigit() for c in f) or (i > 0 and w[:1].isupper() and f not in lex["drop_words"])):
                singles.add(f)
    return singles | name_words, phrases, name_words


def _glue_rx(singles: set[str]) -> re.Pattern | None:
    cands = sorted((s for s in singles if len(s) >= MIN_PRIVATE_LEN), key=len, reverse=True)
    return re.compile("|".join(re.escape(c) for c in cands)) if cands else None


def _glued(token: str, singles: set[str], rx: re.Pattern | None) -> bool:
    """True when ``token`` is private words glued together (plus digits or up to two stray letters), or
    holds a long private word anywhere. KNOWN LIMIT: a private word under ``EMBEDDED_PRIVATE_LEN`` letters glued
    to three or more unrelated letters is not seen ("chris" must not strip "christmas")."""
    if any(len(s) >= EMBEDDED_PRIVATE_LEN and s in token for s in singles):
        return True
    if rx is None:
        return False
    rest, n = rx.subn("", token)
    return n > 0 and len(re.sub(r"\d", "", rest)) <= 2


def _scripts(word: str) -> set[str]:
    """The writing systems of a word's letters (first word of each letter's Unicode name)."""
    return {unicodedata.name(c, "?").split(" ")[0] for c in word if c.isalpha()}


def _memory_runs(tokens: list[str], terms: PrivateTerms, lex: dict) -> set[int]:
    """Indexes of query tokens that sit in a run of two or more content words shared with a memory text."""
    hit: set[int] = set()
    content = [(i, t) for i, t in enumerate(tokens) if t and t not in lex["drop_words"] and t not in lex["question_words"]]
    if len(content) < 2:
        return hit
    for text in terms.memory:
        mem = [f for f in (fold(w) for w in _words(_clean(text))) if f and f not in lex["drop_words"]]
        pairs = set(zip(mem, mem[1:]))
        for (i, a), (j, b) in zip(content, content[1:]):
            if (a, b) in pairs:
                hit.update((i, j))
    return hit


def _memory_glue(terms: PrivateTerms, lex: dict) -> set[str]:
    """Every span of two to four memory words holding two content words, squeezed into one string (eight letters or
    more), so a memory phrase is still seen when it is glued, hyphenated or padded with a number."""
    glue: set[str] = set()
    for text in terms.memory:
        raw = [f for f in (fold(w) for w in _words(_clean(text))) if f]
        is_content = [f not in lex["drop_words"] for f in raw]
        content = [f for f, c in zip(raw, is_content) if c]
        glue.update(a + b for a, b in zip(content, content[1:]))
        for i in range(len(raw)):
            for j in range(i + 2, min(i + 4, len(raw)) + 1):
                if sum(is_content[i:j]) >= 2:
                    glue.add("".join(raw[i:j]))
    return {g for g in glue if len(g) >= 8}


def scrub_query(raw: object, terms: PrivateTerms | None = None, *, lexicon: dict | None = None) -> ScrubResult:
    """The minimal public query for ``raw``, or a refusal. Pure; never raises on any input: an unreadable or
    malformed lexicon, or anything else unexpected, refuses (fails closed) instead."""
    try:
        return _scrub(raw, terms or PrivateTerms(), lexicon or load_lexicon())
    except Exception:  # noqa: BLE001 - refusing is the safe answer; raising would leave the caller to guess
        return ScrubResult(False, "", REASON_ERROR)


def _scrub(raw: object, terms: PrivateTerms, lex: dict) -> ScrubResult:
    if not isinstance(raw, str) or not raw.strip():
        return ScrubResult(False, "", REASON_EMPTY)
    if len(raw) > MAX_RAW_CHARS:
        return ScrubResult(False, "", REASON_TOO_LONG)
    text = _clean(raw)
    kinds: list[str] = []

    def _strip(rx: re.Pattern, kind: str, s: str, ok=lambda m: True) -> str:
        def sub(m: re.Match) -> str:
            if not ok(m):
                return m.group(0)
            kinds.append(kind)
            return " "
        return rx.sub(sub, s)

    text = _strip(_EMAIL_RE, "email", text)
    if _LINK_RE.search(text):
        return ScrubResult(False, "", REASON_LINK)
    text = _strip(_COORD_RE, "coordinates", text)
    street = "|".join(sorted(map(re.escape, lex["street_types"]), key=len, reverse=True))
    if street:
        addr = re.compile(rf"(?i)\b\d{{1,5}}[a-z]?(?:\s*/\s*\d{{1,5}})?\s+(?:[^\W\d_][\w'’\-]*\s+){{1,3}}(?:{street})\b\.?")
        text = _strip(addr, "address", text)
    text = _strip(_PHONE_RE, "number", text, ok=lambda m: sum(c.isdigit() for c in m.group(0)) >= 7)

    words = _words(text)
    tokens = [fold(w) for w in words]
    for tok in tokens:
        mixed = len(tok) >= 12 and sum(c.isdigit() for c in tok) >= 4 and sum(c.isalpha() for c in tok) >= 4
        if len(tok) >= ENCODED_TOKEN_CHARS or mixed or (_HEX_RE.match(tok) and not tok.isdigit()):
            return ScrubResult(False, "", REASON_ENCODED)
    # A look-alike letter from another alphabet inside one word ("\u041cirabel" with a Cyrillic Em).
    if any(len(_scripts(w) & _LOOKALIKE_SCRIPTS) > 1 for w in words):
        return ScrubResult(False, "", REASON_MIXED_SCRIPT)
    run = 0
    for tok in tokens:
        run = run + 1 if len(tok) == 1 and tok.isalpha() else 0
        if run >= 3:
            return ScrubResult(False, "", REASON_SPELLED_OUT)

    if not allow_words():  # no list, no lookup: nothing at all can be checked, so nothing leaves
        return ScrubResult(False, "", REASON_NO_ALLOW_LIST)
    singles, phrases, _names = _private_singles(terms, lex)
    glue = _memory_glue(terms, lex)
    rx = _glue_rx(singles)
    drop: dict[int, str] = {}
    plains = [fold(words[i].translate(_LOOKALIKE)) if any(c.isdigit() or c in "$@" for c in words[i]) and not tok.isdigit()
              else tok for i, tok in enumerate(tokens)]
    bare = [re.sub(r"\d", "", t) if t and not t.isdigit() else t for t in tokens]   # rose2 -> rose, blue4 -> blue
    for i, tok in enumerate(tokens):
        plain = plains[i]
        # each part of a hyphenated or dotted word too ("rose-mary" for Rose: the parts are dictionary words)
        # (fold() joins the parts, and "rosemary" is itself a dictionary word, so split the word as written)
        parts = {f for x in re.split(r"[.\-'’]", words[i]) for f in (fold(x), fold(x.translate(_LOOKALIKE)), re.sub(r"\d", "", fold(x))) if f}
        if tok and (tok in singles or plain in singles or bare[i] in singles or parts & singles or _glued(tok, singles, rx)
                    or _glued(plain, singles, rx) or _glued(bare[i], singles, rx)):
            drop[i] = "private"
        elif any(g in tok for g in glue):
            drop[i] = "memory"
    for phrase in phrases:
        n = len(phrase)
        for i in range(len(tokens) - n + 1):
            if phrase in (tuple(tokens[i:i + n]), tuple(plains[i:i + n]), tuple(bare[i:i + n])):   # l4rkspur, blue4
                drop.update({k: "private" for k in range(i, i + n)})
    for i, tok in enumerate(tokens[:-1]):  # "my friend Dana", "our dog called Biscuit"
        if tok not in lex["relation_words"]:
            continue
        j = i + 1
        named = tokens[j] in lex["naming_words"]
        if named:
            j += 1
        # No capital letter is required: voice transcripts and model-written queries are often all lower case,
        # and "my friend dana smith" must not leave the house. The word after a relation word is a name, and so
        # is the one after that unless it is a function word (a first name plus a surname). Over-stripping costs
        # a weaker query; under-stripping sends a person's name.
        owned = i > 0 and tokens[i - 1] in lex.get("possessive_words", frozenset())   # "my friend", not "dog food"
        if j < len(tokens) and tokens[j] and tokens[j] not in lex["drop_words"] and tokens[j] not in lex["question_words"] \
                and (named or owned or words[j][:1].isupper()):
            drop.setdefault(j, "relation_name")
            k = j + 1
            # a surname: after "my friend dana", "my friend called dana" or "friend Dana Smith"
            if (owned or named or words[j][:1].isupper()) and k < len(tokens) and tokens[k] and tokens[k] not in lex["drop_words"] and tokens[k] not in lex["question_words"] \
                    and tokens[k] not in lex["relation_words"]:
                drop.setdefault(k, "relation_name")
    # the same three views as the private-word checks: as written, look-alike digits read as letters, digits removed
    # ("red box2" still holds the memory phrase "red box")
    for view in (tokens, plains, bare):
        for i in _memory_runs(view, terms, lex):
            drop.setdefault(i, "memory")
    # THE ALLOW-LIST, last and strongest: a word may leave the house only if it is an ordinary dictionary word
    # (allow_words(), SCOWL plus a small supplement) or a number of at most four digits. Everything the rules
    # above did not recognise (a name, a code, an invented spelling, a glued or split private term) is dropped,
    # so a new trick has to produce real dictionary words to get anything out.
    allow = allow_words()
    units = lex.get("unit_suffixes", frozenset())
    for i, tok in enumerate(tokens):
        # judged on the word AS WRITTEN (casefolded), so a hyphenated compound is checked part by part ("gluten-free" is two
        # dictionary words; its folded form "glutenfree" is none)
        if i not in drop and tok and tok not in lex["drop_words"] and not _allowed(words[i].casefold(), allow, units):
            drop[i] = "unknown"

    def _content(idx: Iterable[int]) -> int:
        return sum(1 for i in idx if tokens[i] and tokens[i] not in lex["drop_words"] and tokens[i] not in lex["question_words"])

    before = _content(range(len(tokens)))
    memory_hits = sum(1 for v in drop.values() if v == "memory")
    kinds.extend(drop.values())
    kept_idx = [i for i in range(len(tokens)) if i not in drop and tokens[i] and tokens[i] not in lex["drop_words"]]
    stripped = len(kinds)
    kind_set = tuple(sorted(set(kinds)))
    if memory_hits and memory_hits * 2 >= before:
        return ScrubResult(False, "", REASON_MEMORY_HEAVY, stripped, kind_set)
    after = _content(kept_idx)
    if after == 0:
        return ScrubResult(False, "", REASON_PRIVATE_ONLY if stripped else REASON_EMPTY, stripped, kind_set)
    if stripped and after < 2:
        return ScrubResult(False, "", REASON_PRIVATE_ONLY, stripped, kind_set)

    out: list[str] = []
    for i in kept_idx:
        w = words[i].casefold()
        w = re.sub(r"['’]s$", "", w)
        # The word that LEAVES is checked again as written, part by part ("p.r.i.c.e" folds to price but is sent
        # as five single letters, which is not a dictionary word).
        if not _emit_ok(w, allow, units):
            continue
        if not out or out[-1] != w:
            out.append(w)
    query = ""
    for w in out[:MAX_WORDS]:  # cut at whole words only: a cut word is not a dictionary word
        if len(query) + len(w) + (1 if query else 0) > MAX_CHARS:
            break
        query = f"{query} {w}" if query else w
    # Belt and braces: the query that is about to leave is checked again, whole and with spaces removed.
    # ...and once more with look-alike digits read as letters ("m1 ra bel").
    # ...and with digits removed altogether ("rose2 mary" padding a known name, "blue4 planter" a memory span).
    plain_query = query.translate(_LOOKALIKE)
    for view in (query, plain_query, re.sub(r"\d", "", query)):
        squeezed = fold(view)
        for s in singles:
            if s in {fold(x) for w in view.split() for x in [w, *re.split(r"[.\-'’]", w)] if x} or (len(s) >= 4 and s in squeezed and _glued_span(view, s)) \
                    or (len(s) < 4 and _split_exactly(view, s)):
                return ScrubResult(False, "", REASON_TRIPWIRE, stripped, kind_set)
        for phrase in phrases:
            if "".join(phrase) in squeezed:
                return ScrubResult(False, "", REASON_TRIPWIRE, stripped, kind_set)
    if "@" in query or not query or any(g in squeezed for g in glue):
        return ScrubResult(False, "", REASON_TRIPWIRE, stripped, kind_set)
    return ScrubResult(True, query, REASON_OK, stripped, kind_set)


ALLOW_PATH = _LEXICON_DIR / "web_lookup_allow_en.txt.gz"


@lru_cache(maxsize=1)
def allow_words() -> frozenset:
    """The words a query may carry out (built by tools/generators/build_web_lookup_allowlist.py). A missing or
    unreadable list allows NOTHING, so every query is refused: the gate fails closed."""
    try:
        with gzip.open(ALLOW_PATH, "rt", encoding="utf-8") as f:
            return frozenset(w for w in (line.strip() for line in f) if w)
    except (OSError, EOFError, UnicodeDecodeError):
        return frozenset()


_WORD_NUMBER = re.compile(r"^([a-z]{3,})(\d{1,4})$")    # a dictionary word and a short number: covid19, iphone17
_NUMBER_UNIT = re.compile(r"^(\d{1,4})([a-z]{1,3})$")    # a short number and a LISTED unit: 4k, 5g, 10mm, 3rd


def _allowed_part(x: str, allow: frozenset, units: frozenset) -> bool:
    if (x.isdigit() and len(x) <= 4) or x in allow:
        return True
    u = _NUMBER_UNIT.match(x)
    if u:
        return u.group(2) in units
    m = _WORD_NUMBER.match(x)
    return bool(m) and m.group(1) in allow


def _allowed(tok: str, allow: frozenset, units: frozenset = frozenset()) -> bool:
    """A token is allowed when every part of it (split on . - and apostrophes) is a dictionary word, a short number,
    a dictionary word followed by a short number, or a short number with a unit from the lexicon's list. Anything
    else (a code like x7k2, a name, an invented spelling, 12zz) is not."""
    parts = [x for x in re.split(r"[.\-'’]", tok) if x]
    return bool(parts) and all(_allowed_part(x, allow, units) for x in parts)


def _emit_ok(word: str, allow: frozenset, units: frozenset) -> bool:
    """The outgoing word as written: one part may be anything _allowed accepts; a word built of several parts
    (around . - or apostrophes) needs every part to be a dictionary word of two or more letters or a short number,
    so letters spelled out with dots (p.r.i.c.e) never pass."""
    parts = [x for x in re.split(r"[.\-'’]", word) if x]
    if len(parts) <= 1:
        return _allowed(word, allow, units)
    return all((x.isdigit() and len(x) <= 4) or (len(x) >= 2 and x in allow) for x in parts)


def _split_exactly(query: str, single: str) -> bool:
    """True when a run of two or more kept words joins to EXACTLY ``single`` ("ki m" for Kim, "b o" for Bo). Short
    names are matched only this way: containment across words would refuse ordinary queries ("pizza lasagna" holds
    "al")."""
    toks = [fold(w) for w in query.split()]
    for i in range(len(toks)):
        acc = toks[i]
        for j in range(i + 1, len(toks)):
            acc += toks[j]
            if acc == single:
                return True
            if len(acc) >= len(single):
                break
    return False


def _glued_span(query: str, single: str) -> bool:
    """True when ``single`` appears across word boundaries in ``query``: split over ANY run of kept words, so
    "mi ra bel" is caught as well as "mira bel" (a run is bounded by the name's own length in characters)."""
    toks = [fold(w) for w in query.split()]
    for i in range(len(toks)):
        acc = toks[i]
        for j in range(i + 1, len(toks)):
            acc += toks[j]
            if single in acc and not any(single in t for t in toks[i:j + 1]):   # one word holding it is the
                return True                                                       # whole-word rules' job
            if len(acc) > len(single) + len(toks[i]) + len(toks[j]):
                break
    return False
