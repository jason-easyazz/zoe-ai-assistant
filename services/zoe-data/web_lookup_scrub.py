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
_PHONE_RE = re.compile(r"(?<![^\W_])\+?\d+(?:[\W_]+\d+)+(?![^\W_])")   # ANY run of separators between digit groups, any length
# what may sit between the parts of an address: ANY run of non-alphanumerics ("12, Maple.Street", "12:Maple:Street",
# "12 (Maple) Street") - a class, not a list, like the phone separators
_ADDR_SEP = r"[\W_]+"
_HEX_RE = re.compile(r"(?i)^[0-9a-f]{16,}$")
_LOOKALIKE_SCRIPTS = frozenset({"LATIN", "CYRILLIC", "GREEK", "ARMENIAN", "CHEROKEE"})
_PART_RE = re.compile(r"[^\W_]+")
_COMPOUND_SPLIT = re.compile(r"[.\-'’_/]")
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
        for key in ("drop_words", "question_words", "relation_words", "cue_relation_words", "naming_words", "street_types",
                    "possessive_words", "unit_suffixes"):
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
    """NFKC; every decimal digit written as its ASCII digit ("٤٤١٧" is 4417, so it meets the same number checks);
    invisible format characters removed (so they cannot split a name); other controls become a space."""
    out = []
    for ch in unicodedata.normalize("NFKC", raw):
        cat = unicodedata.category(ch)
        if cat == "Cf":
            continue
        if cat == "Nd":
            out.append(str(unicodedata.digit(ch)))
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
        name_words.update(f for f in (fold(x) for x in _PART_RE.findall(_clean(name))) if len(f) >= 2)
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


MEMORY_GLUE_MIN = 6  # a glued memory phrase this long or longer is also matched INSIDE a token (any length as a whole token)
ADDRESS_NUMBER_AFTER = 3  # ...or this many after it ("Maple Street number 12", "Maple St no. 12")
NAME_SPLIT_WINDOW = 5  # a known name split into 2-3 pieces anywhere inside this many words is refused


def _split_across_filler(view: str, singles: set[str]) -> bool:
    """True when 2 or 3 words of ``view`` inside a NAME_SPLIT_WINDOW-word window join, in order, into a private word, WHATEVER
    sits between them ("rose x wood", "rose 2nd wood", "rose big wood" -> rosewood). One rule for every kind of filler, so a
    new kind of padding cannot reopen the hole; a coincidental match refuses a query, which is the accepted cost."""
    long = {s for s in singles if len(s) >= MIN_PRIVATE_LEN + 1}
    if not long:
        return False
    parts = [fold(p) for p in re.split(r"[\W_]+", view)]
    parts = [p for p in parts if p]
    for i, first in enumerate(parts):
        if not any(s.startswith(first) for s in long):
            continue
        window = parts[i + 1:i + NAME_SPLIT_WINDOW]
        for a in range(len(window)):
            two = first + window[a]
            if two in long:
                return True
            for b in range(a + 1, len(window)):
                if two + window[b] in long:
                    return True
    return False


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


def _run_word(t: str) -> str:
    """A word as the memory-run check compares it: a simple plural ending removed ("boxes" / "keys" -> "box" / "key"), so an
    inflected private phrase is still the phrase. Applied to BOTH sides."""
    if len(t) > 4 and t.endswith("es"):
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def _padding(t: str) -> bool:
    """Not a word for the run check: a standalone number or a single letter ("red 2 box", "red x box" is still "red box")."""
    return t.isdigit() or len(t) <= 1


def _memory_runs(tokens: list[str], terms: PrivateTerms, lex: dict) -> set[int]:
    """Indexes of query tokens that sit in a run of two or more content words shared with a memory text."""
    hit: set[int] = set()
    # padding (a standalone number, a single letter) is skipped on BOTH sides, and words are compared without a plural ending
    content = [(i, _run_word(t)) for i, t in enumerate(tokens) if t and not _padding(t) and t not in lex["drop_words"]
               and t not in lex["question_words"]]
    if len(content) < 2:
        return hit
    for text in terms.memory:
        mem = [_run_word(f) for f in (fold(p) for w in _words(_clean(text)) for p in _COMPOUND_SPLIT.split(w))
               if f and not _padding(f) and f not in lex["drop_words"]]
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
        raw = [f for f in (fold(p) for w in _words(_clean(text)) for p in _COMPOUND_SPLIT.split(w)) if f]
        is_content = [f not in lex["drop_words"] for f in raw]
        content = [f for f, c in zip(raw, is_content) if c]
        glue.update(a + b for a, b in zip(content, content[1:]))
        for i in range(len(raw)):
            for j in range(i + 2, min(i + 4, len(raw)) + 1):
                if sum(is_content[i:j]) >= 2:
                    glue.add("".join(raw[i:j]))
    # every length: a glued phrase that IS a whole word ("ox bow" -> "oxbow") is matched at any length; only the search INSIDE
    # a longer word keeps MEMORY_GLUE_MIN, so two short words cannot hide in every query. A coincidental match refuses or
    # strips (the accepted cost).
    return glue


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
        addr = re.compile(rf"(?i)(?<![^\W_])\d{{1,5}}[a-z]?(?:\s*/\s*\d{{1,5}})?{_ADDR_SEP}(?:[^\W\d_][^\W_]*(?:['’][^\W_]+)*{_ADDR_SEP}){{1,3}}(?:{street})(?![^\W_])\.?")
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
    glue_inside = {g for g in glue if len(g) >= MEMORY_GLUE_MIN}
    rx = _glue_rx(singles)
    drop: dict[int, str] = {}
    # An address in any shape, read on the words rather than the characters: a street word with a house number ANYWHERE
    # before it ("12 Old North Maple Tree Valley Hill Road" - no window to outgrow; the query is short) or up to
    # ADDRESS_NUMBER_AFTER words after it ("Maple Street 12", "Maple Street number 12"). The number, the street word and
    # everything between go; with the number after, so do the three words before.
    streets = {fold(w) for w in lex["street_types"]}
    house = re.compile(r"\d{1,5}[a-z]?")
    for si, tok in enumerate(tokens):
        if tok not in streets:
            continue
        span: set[int] = set()
        for k in range(si - 1, -1, -1):        # the nearest number before
            if house.fullmatch(tokens[k]):
                span.update(range(k, si + 1))
                break
        for k in range(si + 1, min(len(tokens), si + 1 + ADDRESS_NUMBER_AFTER)):
            if house.fullmatch(tokens[k]):
                span.update(range(max(0, si - 3), k + 1))
                break
        for k in span:
            if tokens[k] not in lex["question_words"]:
                drop.setdefault(k, "address")
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
        elif {tok, plain, bare[i], _run_word(tok), _run_word(plain)} & glue or any(g in tok for g in glue_inside):
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
        # is the one after that (given a cue) unless it is a function word (a first name plus a surname).
        # Over-stripping costs a weaker query; under-stripping sends a person's name.
        # After a PERSON relation word the first word is a name with no cue at all ("concerts for partner bill"); a weaker
        # query is the owner-accepted cost. A pet word ("dog food") is an everyday noun, so it still needs a cue ("our dog",
        # "dog called biscuit", "Dog Biscuit"), and so does the second word (a surname), or every relation word eats two.
        owned = i > 0 and tokens[i - 1] in lex.get("possessive_words", frozenset())   # "my friend", not "dog food"
        cued = named or owned or words[j][:1].isupper() if j < len(tokens) else False
        if j < len(tokens) and tokens[j] and tokens[j] not in lex["drop_words"] and tokens[j] not in lex["question_words"] \
                and (cued or tok not in lex.get("cue_relation_words", frozenset())):
            drop.setdefault(j, "relation_name")
            k = j + 1
            # a surname: after "my friend dana", "my friend called dana" or "friend Dana Smith"
            if (owned or named or words[j][:1].isupper()) and k < len(tokens) and tokens[k] and tokens[k] not in lex["drop_words"] and tokens[k] not in lex["question_words"] \
                    and tokens[k] not in lex["relation_words"]:
                drop.setdefault(k, "relation_name")
    # Memory runs are checked in the same three views as the private-word checks (as written, look-alike digits read as
    # letters, digits removed: "red box2" still holds "red box"), over whole tokens AND over the parts of compounds
    # ("red-box", "box-prices": each part keeps its token's index). Only tokens still alive take part, so the check can be
    # re-run after later drops.
    part_idx, part_views = [], ([], [], [])
    for i, w in enumerate(words):
        for p in _COMPOUND_SPLIT.split(w):
            f = fold(p)
            if not f:
                continue
            part_idx.append(i)
            part_views[0].append(f)
            part_views[1].append(fold(p.translate(_LOOKALIKE)))
            part_views[2].append(re.sub(r"\d", "", f) if not f.isdigit() else f)

    def _memory_hits() -> set[int]:
        alive = [i for i in range(len(tokens)) if i not in drop]
        live_parts = [k for k, i in enumerate(part_idx) if i not in drop]
        hits = {alive[k] for view in (tokens, plains, bare) for k in _memory_runs([view[i] for i in alive], terms, lex)}
        hits |= {part_idx[live_parts[k]] for view in part_views for k in _memory_runs([view[j] for j in live_parts], terms, lex)}
        return hits

    drop.update({i: "memory" for i in _memory_hits()})
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
    # dropping a word can JOIN two memory words ("red xqzv box" leaves "red box", "red xqzv box-prices" leaves "red box-"):
    # re-check the survivors, every view and every part, until nothing more is dropped
    while joined := _memory_hits():
        drop.update({i: "memory" for i in joined})

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
    # ...and every one of those with padding words removed (a lone letter or number: "rose x wood", "rose 2 wood").
    plain_query = query.translate(_LOOKALIKE)
    views = [query, plain_query, re.sub(r"\d", "", query)]
    views += [" ".join(w for w in v.split() if not _padding(fold(w))) for v in views]
    for view in views:
        squeezed = fold(view)
        if _split_across_filler(view, singles):
            return ScrubResult(False, "", REASON_TRIPWIRE, stripped, kind_set)
        for s in singles:
            if s in {fold(x) for w in view.split() for x in [w, *re.split(r"[.\-'’]", w)] if x} or (len(s) >= 4 and s in squeezed and _glued_span(view, s)) \
                    or (len(s) < 4 and _split_exactly(view, s)):
                return ScrubResult(False, "", REASON_TRIPWIRE, stripped, kind_set)
        for phrase in phrases:
            if "".join(phrase) in squeezed:
                return ScrubResult(False, "", REASON_TRIPWIRE, stripped, kind_set)
    if any(_memory_runs([fold(w) for w in re.split(r"[\s.\-'’_/]+", view)], terms, lex) or _memory_runs([fold(w) for w in view.split()], terms, lex)
           for view in views):
        return ScrubResult(False, "", REASON_TRIPWIRE, stripped, kind_set)
    if "@" in query or not query or any(g in fold(view) for view in views for g in glue_inside) \
            or any({fold(w), _run_word(fold(w))} & glue for view in views for w in re.split(r"[\W_]+", view) if w):
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
