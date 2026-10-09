"""Script-agnostic matching for the forget ledger: exact spans, near-spelling probes, redaction. Pure: no I/O, no secrets.

THE PROBLEM. The ledger (``memory_forgotten``) holds only salted hashes, so "forget Marisol" could not catch "Marisal" (an STT
misspelling), "Mari sol" (a split spelling) or "Marisól"; and a name inside a run of Chinese or Japanese text (no spaces) was
never a whole word to ``\\w+``. This module is the matching half; the hashing and storage stay in ``memory_forgotten``.

NEAR PROBES (edit distance <= 2 on hashes alone). The ledger cannot hold the name, so it holds, for the name ``a`` with ``k``
allowed edits, one hash per way of removing up to ``k`` codepoints: the key is ``(a with those codepoints removed, the gaps
they left)``. A candidate ``b`` generates the keys of every alignment that costs at most ``k`` (remove up to q of its own
codepoints, then free gaps for what ``a`` would have removed, a substitution being a removal at the SAME gap on both sides).
A shared key means an alignment of cost <= k exists, so it is exact Levenshtein <= k - no verification against ``a`` is
needed (a plain deletion-neighbourhood over-matched 3x on a 73,604-word dictionary: "percival" matched "special"). Distances
count Unicode codepoints, never bytes and never Latin letters; accents and marks are folded away first (not for Han, kana or
Hangul, where a mark such as a dakuten changes the character).

THE RULE (the MemPalace pilot, ``docs/research/mempalace-deep-dive-2026-10-06.md`` 4.8; the edit-distance idea is
MemPalace's ``fact_checker._edit_distance``, MIT): 7+ codepoints -> 2 edits, 5-6 -> 1, 4 or fewer -> none ("Dan" is not "Dana");
no spaces in the script (Han, kana, Hangul, Thai ...) -> 1 edit from 3 codepoints (an unmeasured policy: a 2-edit radius round a
4-character name rewrites half of it). A match is a WHOLE token, never a substring (Latin) - or, in a script without spaces, a
window of the run. A SPLIT spelling (2-3 adjacent tokens, each 2+ letters) joined must be within one edit LESS.
"""
from __future__ import annotations

import itertools
import re
import unicodedata
from functools import lru_cache
from typing import Callable, Iterable, Iterator

MARKER = "[forgotten]"
WORD_RE = re.compile(r"\w+", re.UNICODE)
MAX_WINDOW = 12            # longest name (codepoints) matched inside a run of a script without spaces
MAX_RUN = 3                # a split spelling is at most this many adjacent tokens
NEAR_DOMAINS = (2, 1, 0)   # the edit budgets a ledger name can carry (0 = accents only); the hash domain is "near<k>"

#: scripts written without spaces between words (a name is a window of the run, not a word)
_UNSPACED = ((0x0E00, 0x0EFF), (0x1000, 0x109F), (0x1100, 0x11FF), (0x1780, 0x17FF), (0x3040, 0x30FF), (0x31F0, 0x31FF),
             (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xAC00, 0xD7AF), (0xF900, 0xFAFF), (0xFF66, 0xFF9F), (0x20000, 0x2FFFF))


def is_unspaced(ch: str) -> bool:
    o = ord(ch)
    return any(lo <= o <= hi for lo, hi in _UNSPACED)


def fold(text: str) -> str:
    """NFKC, accents and combining marks dropped (not on Han / kana / Hangul), case-folded: what distances are measured on."""
    out = []
    for ch in unicodedata.normalize("NFKC", str(text or "")):
        if is_unspaced(ch):
            out.append(ch)
        else:
            out.append("".join(c for c in unicodedata.normalize("NFD", ch) if unicodedata.category(c) != "Mn"))
    return unicodedata.normalize("NFC", "".join(out)).casefold()


def max_edits(joined: str) -> int:
    """The edits allowed for a name of these codepoints (see the module docstring)."""
    n = len(joined)
    if n and all(is_unspaced(c) for c in joined):
        return 1 if n >= 3 else 0
    return 2 if n >= 7 else (1 if n >= 5 else 0)


def joined_key(name: str) -> str:
    """The folded name with every non-word character removed: 'Mari sol', 'Mari-sol' and 'Marisól' all give 'marisol'."""
    return "".join(WORD_RE.findall(fold(name)))


# ── the near probes ──────────────────────────────────────────────────────────

def _key(v: str, gaps: Iterable[int]) -> str:
    return v + "|" + ",".join(map(str, gaps))


def probe_keys(joined: str, k: int) -> Iterator[str]:
    """The keys the ledger stores for a name: ``(name minus P, the gaps of P)`` for every position set P of size <= k."""
    n = len(joined)
    for size in range(0, k + 1):
        for pos in itertools.combinations(range(n), size):
            drop = set(pos)
            yield _key("".join(c for i, c in enumerate(joined) if i not in drop), [p - i for i, p in enumerate(pos)])


@lru_cache(maxsize=4096)
def candidate_keys(token: str, budget: int) -> "tuple[tuple[str, int], ...]":
    """``(key, cost)`` for every alignment of ``token`` that costs at most ``budget`` edits (cost = its removals + free gaps)."""
    out: set[tuple[str, int]] = set()
    for q in range(0, budget + 1):
        for pos in itertools.combinations(range(len(token)), q):
            drop = set(pos)
            v = "".join(c for i, c in enumerate(token) if i not in drop)
            gq = [p - i for i, p in enumerate(pos)]
            for j in range(0, q + 1):
                for paired in set(itertools.combinations(gq, j)):
                    for f in range(0, budget - q + 1):
                        for free in itertools.combinations_with_replacement(range(len(v) + 1), f):
                            out.add((_key(v, sorted(paired + free)), q + f))
    return tuple(sorted(out))


def near_hit(token: str, shift: int, hash_of: Callable[[str], str], wanted: "frozenset[str] | set[str]") -> bool:
    """Is ``token`` within the ledger's edit rule of some forgotten name? ``shift`` = 0 for a whole token, 1 for a joined split
    spelling (one edit less). ``hash_of`` hashes a domain-tagged key (``near<k>|<key>``)."""
    if len(token) < 2:
        return False
    for key, cost in candidate_keys(token, max(NEAR_DOMAINS[0] - shift, 0)):
        for k in NEAR_DOMAINS:
            if cost <= k - shift and hash_of(f"near{k}|{key}") in wanted:
                return True
    return False


# ── tokens and spans ─────────────────────────────────────────────────────────

class Tok:
    __slots__ = ("start", "end", "text", "uns")

    def __init__(self, start: int, end: int, text: str, uns: bool):
        self.start, self.end, self.text, self.uns = start, end, text, uns


def tokens(text: str) -> "list[Tok]":
    """The word pieces of ``text`` with their offsets in the ORIGINAL string; ``text`` is folded; ``uns`` = written without spaces."""
    return [Tok(m.start(), m.end(), fold(m.group()), any(is_unspaced(c) for c in m.group()))
            for m in WORD_RE.finditer(text or "")]


def near_spans(text: str, hash_of: Callable[[str], str], wanted: "frozenset[str] | set[str]") -> "list[tuple[int, int]]":
    """Spans of ``text`` within the edit rule of a forgotten name: whole tokens, split spellings (2-3 adjacent tokens, joined,
    one edit less) and, in a script without spaces, windows of the run."""
    toks = tokens(text)
    spans: list[tuple[int, int]] = []
    seen: dict[tuple[str, int], bool] = {}

    def hit(s: str, shift: int) -> bool:
        if (s, shift) not in seen:
            seen[(s, shift)] = near_hit(s, shift, hash_of, wanted)
        return seen[(s, shift)]

    for i, t in enumerate(toks):
        if t.uns and len(t.text) == t.end - t.start:
            for a in range(len(t.text)):
                for ln in range(2, min(MAX_WINDOW, len(t.text) - a) + 1):
                    w = t.text[a:a + ln]
                    if all(is_unspaced(c) for c in w) and hit(w, 0):
                        spans.append((t.start + a, t.start + a + ln))
            continue
        for n in range(MAX_RUN, 1, -1):          # the longest split spelling first: "Maris ol" is one span
            run = toks[i:i + n]
            if len(run) < n or any(len(r.text) < 2 or r.uns or not r.text.isalpha() for r in run):
                continue
            if hit("".join(r.text for r in run), 1):
                spans.append((run[0].start, run[-1].end))
                break
        if hit(t.text, 0):
            spans.append((t.start, t.end))
    return spans


def exact_spans(text: str, hash_of: Callable[[str], str], wanted: "frozenset[str] | set[str]",
                *, max_tokens: int = 6) -> "list[tuple[int, int]]":
    """Spans of ``text`` whose words (whole words, 1..max_tokens in a row; windows inside a script without spaces) hash into
    ``wanted``. The keys are NFKC + casefold words joined by one space - the ledger's own."""
    spans: list[tuple[int, int]] = []
    pieces = [(m.start(), m.end(), unicodedata.normalize("NFKC", m.group()).casefold()) for m in WORD_RE.finditer(text or "")]
    for i, (s0, _e0, _w0) in enumerate(pieces):
        for n in range(1, max_tokens + 1):
            if i + n > len(pieces):
                break
            if hash_of(" ".join(p[2] for p in pieces[i:i + n])) in wanted:
                spans.append((s0, pieces[i + n - 1][1]))
    for s0, e0, w in pieces:
        if len(w) == e0 - s0 and any(is_unspaced(c) for c in w):
            for a in range(len(w)):
                for ln in range(1, min(MAX_WINDOW, len(w) - a) + 1):
                    if hash_of(w[a:a + ln]) in wanted:
                        spans.append((s0 + a, s0 + a + ln))
    return spans


def merge(spans: "Iterable[tuple[int, int]]") -> "list[tuple[int, int]]":
    out: list[list[int]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def redact(text: str, spans: "Iterable[tuple[int, int]]", marker: str = MARKER) -> "tuple[str, int]":
    """``text`` with each (merged) span replaced by ``marker`` (the surrounding words stay), and how many spans there were."""
    merged = merge(spans)
    if not merged:
        return text, 0
    out, pos = [], 0
    for s, e in merged:
        out.append(text[pos:s])
        out.append(marker)
        pos = e
    out.append(text[pos:])
    return "".join(out), len(merged)
