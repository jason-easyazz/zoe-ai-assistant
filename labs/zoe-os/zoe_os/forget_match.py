"""Matching for a forget by ENTITY ("forget Dana") or by TOPIC ("...about my mum's heart").

Pure functions, no I/O. The shapes follow production (read, not imported:
``services/zoe-data/memory_forgotten.py`` and ``forget_topic.py``):

* an ENTITY is a name of 1..``MAX_KEY_TOKENS`` words. A text names it when the name's words
  appear as whole words, in order, next to each other; case, accents-by-NFKC and whatever
  punctuation sits between the words do not matter;
* a TOPIC is the content words of a phrase (stop words dropped, a plural or possessive
  folded, a few kin words matched with their synonyms). A text is about the topic only when
  it holds EVERY topic word. Never a fuzzy "mostly".

Not ported from production, on purpose and stated in the README: near spellings (edit
distance), the question "did you also mean ...?", and parsing the forget sentence itself.
"""
from __future__ import annotations

import re
import unicodedata

MAX_KEY_TOKENS = 6
_WORD = re.compile(r"[^\W_]+", re.UNICODE)     # an underscore separates words, like any punctuation

_STOP = frozenset(
    "my our your his her their the a an of and or to in on at with for about that this these those "
    "thing things stuff i me you we it its is are was were be been am what everything anything all "
    "told said mentioned just really some any".split())
_SYNONYMS = (
    frozenset({"mum", "mom", "mother", "mummy", "mama"}),
    frozenset({"dad", "father", "daddy", "papa"}),
    frozenset({"kid", "child", "children"}),
    frozenset({"doctor", "gp"}),
)


def words(text: str) -> list[str]:
    """Whole words, NFKC and case folded."""
    return _WORD.findall(unicodedata.normalize("NFKC", str(text or "")).casefold())


def entity_key(name: str) -> str:
    """The canonical key of a name: its folded words joined by one space ('' = no words)."""
    return " ".join(words(name)[:MAX_KEY_TOKENS])


def runs(tokens: list[str]):
    """Every contiguous run of 1..MAX_KEY_TOKENS words, as a key. What a hashed ledger can match."""
    for i in range(len(tokens)):
        for n in range(1, min(MAX_KEY_TOKENS, len(tokens) - i) + 1):
            yield " ".join(tokens[i:i + n])


def names(text: str, key: str) -> bool:
    return bool(key) and key in runs(words(text))


def _stem(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token


def _stems(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKC", str(text or "")).casefold().replace("’", "'")
    return [_stem(w.removesuffix("'s")) for w in re.findall(r"[^\W_]+(?:'s)?", folded)]


def topic_groups(topic: str) -> list[frozenset[str]]:
    """The topic as word GROUPS (a word and its synonyms). [] = no content word: matches nothing."""
    groups: list[frozenset[str]] = []
    for w in _stems(topic):
        if w in _STOP or len(w) < 2:
            continue
        syn = next((s for s in _SYNONYMS if w in s), None)
        group = frozenset(_stem(x) for x in syn) if syn else frozenset({w})
        if group not in groups:
            groups.append(group)
    return groups


def about(text: str, groups: list[frozenset[str]]) -> bool:
    """Does ``text`` hold every topic word (or a synonym)? An empty topic matches nothing."""
    have = set(_stems(text))
    return bool(groups) and all(g & have for g in groups)
