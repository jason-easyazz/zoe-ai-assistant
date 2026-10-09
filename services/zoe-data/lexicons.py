"""Per-language LEXICONS as DATA: the words the memory floors used to hard-code in English regexes.

Why this exists (docs/research/structural-floors-2026-10-09.md section 5.2): the three lexical floors
(``memory_authority``, ``role_guess_guard``, ``people_roles``) decided polarity, contrast, end-state, hedge and
role by English word lists compiled into regexes, so a new phrasing was a new rule and a second language was a
rewrite. The WORDS now live in ``lexicons_data/<lang>.json``; the logic that uses them is language-neutral.

What a lexicon is allowed to do - the rule this module exists to keep:

* it is a PRE-FILTER. ``markers`` / ``negation_scope`` answer "does this turn carry a negation / end-state / hedge /
  question / past marker at all, and does it bind to the value in question?". That decides whether a claim row is
  *suspicious* (and so worth the off-path verifier), and it may VETO a promotion. It never AUTHORISES one: the
  authority for a claim is the extractor's structured claim row plus the language-independent checks in
  ``structural_claims`` (verbatim quote, value in quote, speaker, own-words wall).
* a language with no usable lexicon simply contributes no markers (no veto) - it is never guessed from English.
* a language is ``enabled`` in its file only after the labelled-set rows for that language pass
  (``tests/test_structural_floors_labelled.py``); a file with ``reviewed: false`` was written by the author of the
  research note and has not been read by a native speaker (E6 in the note).

English is special in one way only: ``memory_authority`` / ``role_guess_guard`` / ``people_roles`` BUILD their
regexes from ``en.json`` (the words were moved, not rewritten; ``tests/test_lexicon_english_identity.py`` pins the
compiled patterns byte for byte against the pre-move ones).

Stdlib only. Never raises on a missing language: an unknown code behaves as an empty lexicon.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

DIR = Path(__file__).with_name("lexicons_data")
LANGS = ("en", "es", "fr", "de", "zh", "ja")

_KANA_RE = re.compile(r"[぀-ヿｦ-ﾟ]")
_HAN_RE = re.compile(r"[㐀-鿿]")


@lru_cache(maxsize=None)
def load(lang: str) -> dict[str, Any]:
    """The lexicon for ``lang`` (``{}`` when there is none). Cached; the files are data, read once."""
    code = (lang or "").strip().lower().split("-")[0]
    path = DIR / f"{code}.json"
    if not code or not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def available() -> tuple[str, ...]:
    return tuple(code for code in LANGS if load(code))


def enabled(lang: str) -> bool:
    """A language is ON only when its file says so (its labelled-set rows pass)."""
    lex = load(lang)
    return bool(lex) and bool(lex.get("enabled"))


def words(lang: str, key: str) -> list[str]:
    value = load(lang).get(key) or []
    return list(value) if isinstance(value, list) else []


def is_cjk(lang: str) -> bool:
    return bool(load(lang).get("cjk"))


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


# -- script-based language detection ----------------------------------------------------------------------

def detect_scored(text: str) -> tuple[str, int]:
    """``(language, evidence)``: kana -> ``ja``, Han without kana -> ``zh`` (evidence 1), else the Latin-script lexicon
    whose ``detect_words`` match the most tokens (ties and no match -> ``en`` with evidence 0). A pre-filter choice."""
    s = text or ""
    if _KANA_RE.search(s):
        return "ja", 1
    if _HAN_RE.search(s):
        return "zh", 1
    toks = re.findall(r"[^\W\d_]+", _nfkc(s).casefold())
    best, best_n = "en", 0
    for code in ("en", "es", "fr", "de"):
        probe = set(words(code, "detect_words"))
        n = sum(1 for t in toks if t in probe)
        if n > best_n:
            best, best_n = code, n
    return best, best_n


def detect(text: str) -> str:
    return detect_scored(text)[0]


# -- the regex builders (English modules call these; so do the structural checks) ----------------------------

def alt(items: Iterable[str]) -> str:
    """``a|b|c`` of regex fragments, order preserved (an alternation is order-sensitive)."""
    return "|".join(items)


def _bounded(lang: str, body: str) -> str:
    return f"(?:{body})" if is_cjk(lang) else rf"(?<!\w)(?:{body})(?!\w)"


def word_re(lang: str, key: str, *, flags: int = re.IGNORECASE) -> Optional["re.Pattern[str]"]:
    """Whole-word (or, for a CJK lexicon, substring) alternation over ``key``'s fragments, or None when empty."""
    frags = words(lang, key)
    if not frags:
        return None
    return re.compile(_bounded(lang, alt(frags)), flags)


@lru_cache(maxsize=None)
def _cached_re(lang: str, key: str) -> Optional["re.Pattern[str]"]:
    return word_re(lang, key)


_KIN_INDEX: Optional[dict[str, str]] = None


def kin_index() -> dict[str, str]:
    """``surface form (casefold) -> kin code`` over every available language (the first language in ``LANGS``
    order wins a collision)."""
    out: dict[str, str] = {}
    for lang in LANGS:
        for code, forms in (load(lang).get("kin_codes") or {}).items():
            for form in forms:
                out.setdefault(_nfkc(form).casefold(), code)
    return out


def kin_code(word: str, lang: Optional[str] = None) -> Optional[str]:
    """The language-neutral kin code for a surface word ("mum" / "madre" / "母" -> ``mother``), or None. With
    ``lang`` only that language's forms are read; without, every available language's."""
    global _KIN_INDEX
    w = _nfkc(word).casefold().strip()
    if not w:
        return None
    if lang:
        for code, forms in (load(lang).get("kin_codes") or {}).items():
            if w in {_nfkc(f).casefold() for f in forms}:
                return code
        return None
    if _KIN_INDEX is None:
        _KIN_INDEX = kin_index()
    return _KIN_INDEX.get(w)


def kin_surfaces(code: str, lang: str) -> list[str]:
    return list((load(lang).get("kin_codes") or {}).get(code) or [])


def loose_code(code: str) -> bool:
    """A loose label (friend / colleague / boss / neighbour) is not a family or partner role."""
    return any(code in (load(lang).get("loose_codes") or []) for lang in LANGS)


# -- markers: does the text carry a polarity / modality marker at all? ----------------------------------------

_BREAKS = ",;.!?。！？、，；¿¡"


def _spans(rx: Optional["re.Pattern[str]"], text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in rx.finditer(text)] if rx is not None else []


def negation_spans(text: str, lang: str) -> list[tuple[int, int]]:
    """Spans of negation markers in ``_nfkc(text)`` (word and phrase markers, plus clitic suffixes like ``n't``)."""
    if not load(lang):
        return []
    s = _nfkc(text)
    parts = words(lang, "negation_words") + words(lang, "negation_phrases")
    out: list[tuple[int, int]] = []
    if parts:
        out += _spans(re.compile(_bounded(lang, alt(parts)), re.IGNORECASE), s)
    for suf in words(lang, "negation_suffix"):
        out += _spans(re.compile(suf + ("" if is_cjk(lang) else r"\b"), re.IGNORECASE), s)
    return sorted(set(out))


def ended_spans(text: str, lang: str) -> list[tuple[int, int, str]]:
    """``(start, end, kind)`` of end-state markers in ``_nfkc(text)``. kind: ``verb`` (stopped / dropped / ...),
    ``phrase`` (no longer / not any more: the negation is part of the marker) or ``aux_neg`` (``did not drop``:
    the negation is part of the verb phrase and DENIES the end)."""
    if not load(lang):
        return []
    s = _nfkc(text)
    out: list[tuple[int, int, str]] = []
    verbs, phrases = words(lang, "ended_verbs"), words(lang, "ended_phrases")
    if verbs:
        out += [(a, b, "verb") for a, b in _spans(re.compile(_bounded(lang, alt(verbs)), re.IGNORECASE), s)]
    if phrases:
        out += [(a, b, "phrase") for a, b in _spans(re.compile(_bounded(lang, alt(phrases)), re.IGNORECASE), s)]
    aux, base = words(lang, "ended_aux"), words(lang, "ended_base_verbs")
    if aux and base:
        out += [(a, b, "aux_neg") for a, b in _spans(re.compile(
            rf"\b(?:{alt(aux)})(?:\s+not|n['\u2019]t)\s+(?:{alt(base)})\b", re.IGNORECASE), s)]
    return sorted(set(out))


def clause_of(text: str, pos: int) -> tuple[int, int]:
    """The clause around ``pos``: between the nearest break characters."""
    lo = max((text.rfind(ch, 0, pos) for ch in _BREAKS), default=-1) + 1
    his = [text.find(ch, pos) for ch in _BREAKS]
    hi = min((h for h in his if h >= 0), default=len(text))
    return lo, hi


def negation_scope(text: str, span: tuple[int, int], lang: str) -> str:
    """The words a negation marker governs (``text`` is NFKC-normalised first, ``span`` is in that text): after the
    marker up to the clause break or a contrast connector; for a verb-final language whose lexicon says
    ``negation_scope: before`` (Japanese) the clause BEFORE it."""
    s = _nfkc(text)
    lo, hi = clause_of(s, span[0])
    direction = load(lang).get("negation_scope") or "after"
    after = s[span[1]:hi]
    conns = words(lang, "contrast_connectors")
    if conns and not is_cjk(lang):
        m = re.search(rf"(?<!\w)(?:{alt(conns)})(?!\w)", after, re.IGNORECASE)
        if m:
            after = after[:m.start()]
    before = s[lo:span[0]]
    if direction == "before":
        return before
    if direction == "both":
        return before + " " + after
    return after


def _has(lang: str, key: str, text: str) -> bool:
    rx = _cached_re(lang, key)
    return bool(rx and rx.search(_nfkc(text)))


def markers(text: str, lang: str) -> dict[str, bool]:
    """Which marker categories ``text`` carries in ``lang`` (booleans; an empty lexicon -> all False)."""
    keys = ("negation", "ended", "hedge", "hypothetical", "question", "past")
    lex = load(lang)
    if not lex or not text:
        return {k: False for k in keys}
    s = _nfkc(text)
    q = any(m in s for m in (lex.get("question_marks") or []))
    qa, qs = words(lang, "question_aux"), words(lang, "question_subjects")
    if not q and qa and qs:
        q = bool(re.match(rf"^\s*(?:{alt(qa)})\s+(?:{alt(qs)})\b", s, re.IGNORECASE))
    if not q:
        q = _has(lang, "question_words", s)
    hedge = _has(lang, "hedge_words", s)
    if not hedge:
        subj, verbs = words(lang, "hedge_subjects"), words(lang, "hedge_verbs")
        if verbs:
            body = alt(verbs)
            pat = (f"(?:{body})" if is_cjk(lang)
                   else rf"(?<!\w)(?:{alt(subj)})\s+(?:{body})(?!\w)" if subj
                   else rf"(?<!\w)(?:{body})(?!\w)")
            hedge = bool(re.search(pat, s, re.IGNORECASE))
    return {
        "negation": bool(negation_spans(s, lang)),
        "ended": bool(ended_spans(s, lang)),
        "hedge": hedge,
        "hypothetical": _has(lang, "hypothetical", s),
        "question": q,
        "past": _has(lang, "past_markers", s),
    }


def user_subject_lead(text: str, lang: str) -> Optional[bool]:
    """Does the (fact) sentence open with the speaker as subject ("User's ...", "El usuario ...")? None when the
    language has no ``user_subject`` entry (unknown, not False)."""
    subj = words(lang, "user_subject")
    if not subj:
        return None
    s = _nfkc(text).strip()
    rx = re.compile(f"^(?:{alt(subj)})" if is_cjk(lang) else rf"^(?:{alt(subj)})(?!\w)", re.IGNORECASE)
    return bool(rx.match(s))


def mentions_user(text: str, lang: str) -> Optional[bool]:
    """Does the (fact) sentence speak about the user ("User's ...", "El usuario ...")? None = no marker data."""
    markers_ = words(lang, "user_markers")
    if not markers_:
        return None
    s = _nfkc(text)
    return bool(re.search(_bounded(lang, alt(markers_)), s, re.IGNORECASE))


def kin_in(text: str, code: str, lang: str) -> Optional[bool]:
    """Does ``text`` contain a surface form of kin ``code`` in ``lang``? None when the language lists no forms."""
    forms = kin_surfaces(code, lang)
    if not forms:
        return None
    s = _nfkc(text).casefold()
    for f in forms:
        ff = _nfkc(f).casefold()
        if is_cjk(lang):
            if ff in s:
                return True
        elif re.search(rf"(?<!\w){re.escape(ff)}s?(?!\w)", s):
            return True
    return False


def any_kin_in(text: str, lang: str) -> Optional[bool]:
    """Does ``text`` name ANY family/partner (non-loose) kin in ``lang``? None when the language has no kin data."""
    kin = load(lang).get("kin_codes") or {}
    if not kin:
        return None
    return any(kin_in(text, code, lang) for code in kin if not loose_code(code))
