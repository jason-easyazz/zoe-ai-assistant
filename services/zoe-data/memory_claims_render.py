"""Memory claims, THE TEXT OF A CORRECTED ROW (memory redesign step 6): a pure splice, checked by the normalisers.

The validator (``memory_claims_validate``) plans a row edit as VALUES: this attribute of this row goes from one value_norm to
another. A memory row is free text, so the guarded writer needs the new sentence. ``splice`` builds it by replacing, inside the
row attribute's stored span only, the fewest words that carry the old value with the member's OWN words for the new one, and it
accepts a candidate only when ``memory_claims.normalise`` reads the planned value back out of the new span. Everything outside
the span is byte-identical. No candidate that reads back = ``None`` (the writer refuses; the row stays as it was).

No phrase list and no value grammar live here: what a date or an amount looks like is ``memory_claims``' business. Pure; never
raises.
"""
from __future__ import annotations

import re
from typing import Mapping, Optional

import memory_claims as mc

_PIECES_RE = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+|\s+|.", re.DOTALL)
MAX_PIECES = 24
_NUMERIC = frozenset({"amount", "measure", "age", "count"})


def _reads(attribute: str, text: str, value: Mapping, unit: str) -> bool:
    """``text`` reads back as exactly ``value``: numerically for the number-valued attributes, field for field otherwise (an
    hour with no am / pm is not "5pm")."""
    n = mc.normalise(attribute, text)
    if n is None or n.unit != unit or not mc.same_value(attribute, n.value, value):
        return False
    return attribute in _NUMERIC or dict(n.value) == dict(value)


def _names(attribute: str, text: str, stored: Mapping, whole: bool) -> bool:
    """Does ``text`` alone carry the stored value (``whole``), or a part of it (a day of a full date, an hour of "4pm")?"""
    n = mc.normalise(attribute, text)
    if n is None:
        return False
    return mc.same_value(attribute, n.value, stored) if whole else mc.old_matches(attribute, n.value, stored)


def trim(attribute: str, said: str) -> str:
    """The member's words for the value without the words that carry none of it ("the 19th" -> "19th"): leading, then trailing,
    words are dropped while the reading stays the same."""
    words = (said or "").split()
    want = mc.normalise(attribute, " ".join(words))
    if want is None:
        return " ".join(words)

    def same(ws: list) -> bool:
        n = mc.normalise(attribute, " ".join(ws)) if ws else None
        return n is not None and n.unit == want.unit and n.value == want.value

    while len(words) > 1 and same(words[1:]):
        words = words[1:]
    while len(words) > 1 and same(words[:-1]):
        words = words[:-1]
    return " ".join(words)


def _styled(new: str, replaced: str, span_text: str) -> str:
    """The row's capitalisation, kept: word for word when the counts match ("Thursday" -> "Wednesday", "12th of December" ->
    "12th of January"), else a word the row already has is written as the row writes it."""
    a, b = new.split(), replaced.split()
    known = {w.casefold(): w for w in span_text.split()}
    out = []
    for i, w in enumerate(a):
        if len(a) == len(b) and b[i][:1].isupper() and w[:1].islower():
            w = w[:1].upper() + w[1:]
        elif len(a) != len(b):
            w = known.get(w.casefold(), w)
        out.append(w)
    return " ".join(out)


def _runs(pieces: list, by_word: bool):
    """(start, end) piece runs, shortest first then leftmost, that begin and end on a non-space piece; ``by_word`` keeps only
    runs bounded by whitespace or the span's edge."""
    n = len(pieces)
    for size in range(1, n + 1):
        for i in range(0, n - size + 1):
            j = i + size
            if pieces[i].isspace() or pieces[j - 1].isspace():
                continue
            if by_word and not ((i == 0 or pieces[i - 1].isspace()) and (j == n or pieces[j].isspace())):
                continue
            yield i, j


def splice(attribute: str, row_text: str, span: object, stored: Mapping, planned: Mapping, unit: str,
           said_new: str) -> Optional[tuple]:
    """``(new row text, "start:end" of the new span)`` or ``None``. ``stored`` / ``planned``: the attribute's value_norm before
    and after (the validator's ``RowEdit.old`` / ``.new``); ``said_new``: the claim's new value exactly as the member said it."""
    try:
        ab = mc.parse_span(span)
        if ab is None or ab[1] > len(row_text or "") or not (said_new or "").strip():
            return None
        start, end = ab
        span_text = row_text[start:end]
        pieces = _PIECES_RE.findall(span_text)
        if not pieces or len(pieces) > MAX_PIECES or not _names(attribute, span_text, stored, True):
            return None
        new = trim(attribute, said_new)
        complete = _reads(attribute, new, planned, unit)      # the member's words alone say the whole new value
        for by_word in (True, False):
            for i, j in _runs(pieces, by_word):
                replaced = "".join(pieces[i:j])
                if not _names(attribute, replaced, stored, complete):
                    continue
                cand = "".join(pieces[:i]) + _styled(new, replaced, span_text) + "".join(pieces[j:])
                if cand != span_text and _reads(attribute, cand, planned, unit):
                    return row_text[:start] + cand + row_text[end:], f"{start}:{start + len(cand)}"
        return None
    except Exception:  # noqa: BLE001 - a text that cannot be built is not written
        return None


def shift_span(span: object, edit_start: int, edit_end: int, delta: int) -> Optional[str]:
    """Another attribute's span of the same row after the splice: unchanged before it, moved by ``delta`` after it, ``None``
    (re-read later) when it overlaps the replaced text."""
    ab = mc.parse_span(span)
    if ab is None:
        return None
    if ab[1] <= edit_start:
        return f"{ab[0]}:{ab[1]}"
    if ab[0] >= edit_end:
        return f"{ab[0] + delta}:{ab[1] + delta}"
    return None
