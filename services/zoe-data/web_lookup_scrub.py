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

_LEXICON_DIR = Path(__file__).resolve().parent / "lexicons_data"
_WORD_RE = re.compile(r"[^\W_]+(?:['’.\-][^\W_]+)*")
_LINK_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://|\bwww\.|\b[a-z0-9\-]+\.(?:com|net|org|io|au|uk|co)\b/")
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_COORD_RE = re.compile(r"-?\d{1,3}\.\d{3,}\s*,\s*-?\d{1,3}\.\d{3,}")
_PHONE_RE = re.compile(r"(?<![^\W_])\+?\d[\d\s().\-]{5,}\d(?![^\W_])")
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
        for key in ("drop_words", "question_words", "relation_words", "naming_words", "street_types"):
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
    """The minimal public query for ``raw``, or a refusal. Pure; never raises on any input."""
    terms = terms or PrivateTerms()
    lex = lexicon or load_lexicon()
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

    singles, phrases, _names = _private_singles(terms, lex)
    glue = _memory_glue(terms, lex)
    rx = _glue_rx(singles)
    drop: dict[int, str] = {}
    for i, tok in enumerate(tokens):
        plain = fold(words[i].translate(_LOOKALIKE)) if any(c.isdigit() or c in "$@" for c in words[i]) and not tok.isdigit() else tok
        if tok and (tok in singles or plain in singles or _glued(tok, singles, rx) or _glued(plain, singles, rx)):
            drop[i] = "private"
        elif any(g in tok for g in glue):
            drop[i] = "memory"
    for phrase in phrases:
        n = len(phrase)
        for i in range(len(tokens) - n + 1):
            if tuple(tokens[i:i + n]) == phrase:
                drop.update({k: "private" for k in range(i, i + n)})
    for i, tok in enumerate(tokens[:-1]):  # "my friend Dana", "our dog called Biscuit"
        if tok not in lex["relation_words"]:
            continue
        j = i + 1
        named = tokens[j] in lex["naming_words"]
        if named:
            j += 1
        if j < len(tokens) and tokens[j] and tokens[j] not in lex["drop_words"] and (named or words[j][:1].isupper()):
            drop.setdefault(j, "relation_name")
    for i in _memory_runs(tokens, terms, lex):
        drop.setdefault(i, "memory")

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
        if not out or out[-1] != w:
            out.append(w)
    query = " ".join(out[:MAX_WORDS])[:MAX_CHARS].strip()
    # Belt and braces: the query that is about to leave is checked again, whole and with spaces removed.
    squeezed = fold(query)
    for s in singles:
        if s in {fold(w) for w in out} or (len(s) >= 4 and s in squeezed and _glued_span(query, s)):
            return ScrubResult(False, "", REASON_TRIPWIRE, stripped, kind_set)
    for phrase in phrases:
        if "".join(phrase) in squeezed:
            return ScrubResult(False, "", REASON_TRIPWIRE, stripped, kind_set)
    if "@" in query or not query or any(g in squeezed for g in glue):
        return ScrubResult(False, "", REASON_TRIPWIRE, stripped, kind_set)
    return ScrubResult(True, query, REASON_OK, stripped, kind_set)


def _glued_span(query: str, single: str) -> bool:
    """True when ``single`` appears across a word boundary in ``query`` (split over two kept words)."""
    toks = [fold(w) for w in query.split()]
    return any(single in a + b and single not in a and single not in b for a, b in zip(toks, toks[1:]))
