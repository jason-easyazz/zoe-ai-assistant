"""The language side of restraint: every WORD ``restraint.py`` decides by lives in ``lexicons_data/<lang>.json``
under the ``"restraint"`` key, and this module compiles it (docs/knowledge/restraint.md "Languages").

Why a module of its own: ``restraint.py`` used to hold the health / money / grief / conflict / kin / feeling word lists,
the stop words, the "what's up?" phrases and the spoken-mute grammar as English literals, so a Spanish row about a
diagnosis went out unclassed (the held-out set scored 20 of 32 even for English before the list was widened). The words
are data now; the logic that uses them (``classify_full``, ``is_pull``, ``parse_utterance``) is language-neutral.

The rules this module keeps:

* the language is read from the TEXT (``lexicons.detect``: kana -> ja, Han -> zh, else the Latin lexicon whose function
  words match most, else en). A language with no ``restraint`` entry contributes no lexical class (the structured signals
  - memory type, entity type, tags, captured affect - still decide); it is NEVER guessed from English.
* English is built byte for byte as before (``tests/test_restraint_lexicon.py`` pins every compiled pattern against the
  pre-move source). The other five languages are author-written and ``reviewed: false`` until a native reader signs
  them off; their labelled sentences are in the same test.
* a lexicon entry is a PRE-FILTER, like ``lexicons.py``: it can only WITHHOLD (class a row sensitive, parse a mute);
  it never authorises delivery of anything.

Stdlib only; ``lexicons`` is stdlib only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

import lexicons

KEY = "restraint"


def norm(text: Any) -> str:
    """Lowercase words only: apostrophes inside a word vanish (``don't`` -> ``dont``, ``Dana's`` -> ``danas``), every
    other mark is a space. Unicode-aware (``más`` stays ``más``); for ASCII text it is the old ``[^a-z0-9\\s]`` rule."""
    t = str(text or "").lower().replace("’", "'")
    t = re.sub(r"(?<=\w)'(?=\w)", "", t)
    t = re.sub(r"[^\w\s]|_", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass(frozen=True)
class Pack:
    lang: str = "en"
    cjk: bool = False
    present: bool = False
    health: Optional["re.Pattern[str]"] = None
    money: Optional["re.Pattern[str]"] = None
    grief: Optional["re.Pattern[str]"] = None
    family_conflict: Optional["re.Pattern[str]"] = None
    other_kin: Optional["re.Pattern[str]"] = None
    kin_poss: Optional["re.Pattern[str]"] = None
    name_poss: Optional["re.Pattern[str]"] = None
    affect: Optional["re.Pattern[str]"] = None
    safety: Optional["re.Pattern[str]"] = None       # health rows the user-model card keeps (allergy, medication ...)
    not_a_name: frozenset = frozenset()
    stop: frozenset = frozenset()
    kin_canon: dict = field(default_factory=dict)
    pull_leads: frozenset = frozenset()
    pull_phrases: frozenset = frozenset()
    pull_tail: frozenset = frozenset()
    obj_drop: frozenset = frozenset()
    person_obj: frozenset = frozenset()
    mute: dict = field(default_factory=dict)         # name -> compiled pattern
    ack: dict = field(default_factory=dict)          # ack_mute / ack_release / ack_release_none / ack_ask


def _alt(items: Any) -> str:
    return "|".join(items) if isinstance(items, list) else ""


def _bound(cjk: bool, body: str) -> str:
    return f"(?:{body})" if cjk else rf"\b(?:{body})\b"


def _rx(src: str, flags: int = 0) -> Optional["re.Pattern[str]"]:
    return re.compile(src, flags) if src else None


@lru_cache(maxsize=None)
def pack(lang: str) -> Pack:
    """The compiled restraint lexicon of ``lang`` (an empty ``Pack`` when the language has none). Cached."""
    code = (lang or "en").strip().lower().split("-")[0]
    lex = lexicons.load(code)
    r = lex.get(KEY) if isinstance(lex, dict) else None
    cjk = bool(lex.get("cjk")) if isinstance(lex, dict) else False
    if not isinstance(r, dict) or not r:
        return Pack(lang=code, cjk=cjk)
    sp = r"\s*" if cjk else r"\s+"

    def cls(key: str) -> Optional["re.Pattern[str]"]:
        body = _alt(r.get(key))
        return _rx(_bound(cjk, body)) if body else None

    conflict, kin = _alt(r.get("conflict")), _alt(r.get("kin"))
    fc_parts = []
    if conflict and kin:
        c, k = f"(?:{conflict})", f"(?:{kin})"
        if cjk:
            fc_parts += [rf"{c}.{{0,40}}{k}", rf"{k}.{{0,40}}{c}"]
        else:
            fc_parts += [rf"\b{c}\b.{{0,40}}\b{k}\b", rf"\b{k}\b.{{0,40}}\b{c}\b"]
    if r.get("family_other"):
        fc_parts.append(_bound(cjk, _alt(r["family_other"])))
    if r.get("conflict_standalone"):                    # "we had a terrible row": a conflict the speaker HAD, no kin word needed
        fc_parts.append(_bound(cjk, _alt(r["conflict_standalone"])))
    poss = _alt(r.get("possessive"))
    people = _alt(r.get("people"))                       # friend, boss, neighbour ...: another person, but not family
    kin_or_people = "|".join(x for x in (kin, people) if x)
    suffix = _alt(r.get("kin_possessive_suffix"))
    affect_src = ""
    if r.get("affect"):
        affect_src = _bound(cjk, _alt(r["affect"]))
        if r.get("feel_lead") and r.get("feel_low"):
            lead, low = _alt(r["feel_lead"]), _alt(r["feel_low"])
            affect_src += (rf"|(?:{lead})(?:\w+\s*){{0,2}}(?:{low})" if cjk
                           else rf"|\b(?:{lead})\s+(?:\w+\s+){{0,2}}(?:{low})\b")
    mute = {}
    for name, src in (r.get("mute_patterns") or {}).items():
        try:
            mute[name] = re.compile(src)
        except re.error:
            continue
    return Pack(
        lang=code, cjk=cjk, present=True,
        health=cls("health"), money=cls("money"), grief=cls("grief"),
        family_conflict=_rx("|".join(fc_parts)),
        other_kin=_rx("|".join(x for x in (
            _bound(cjk, f"(?:{poss}){sp}(?:{kin_or_people})") if poss and kin_or_people else "",
            _bound(cjk, _alt(r.get("kin_bare"))) if r.get("kin_bare") else "") if x)),
        kin_poss=_rx(_bound(cjk, f"(?:{kin_or_people})(?:{suffix})") if suffix and kin_or_people else ""),
        name_poss=_rx(str(r.get("name_possessive") or "")),
        affect=_rx(affect_src),
        safety=cls("health_safety"),
        not_a_name=frozenset(r.get("not_a_name") or ()),
        stop=frozenset(r.get("stop") or ()),
        kin_canon=dict(r.get("kin_canon") or {}),
        pull_leads=frozenset(r.get("pull_leads") or ()),
        pull_phrases=frozenset(r.get("pull_phrases") or ()),
        pull_tail=frozenset(r.get("pull_tail") or ()),
        obj_drop=frozenset(r.get("obj_drop") or ()),
        person_obj=frozenset(r.get("person_obj") or ()),
        mute=mute,
        ack={k: str(r[k]) for k in ("ack_mute", "ack_release", "ack_release_none", "ack_ask") if r.get(k)},
    )


def lang_of(text: Any) -> str:
    return lexicons.detect(str(text or ""))


def pack_for(text: Any) -> Pack:
    """The pack of the language ``text`` is written in."""
    return pack(lang_of(text))


def ack(key: str, text: Any = "") -> str:
    """A spoken acknowledgement in the language of ``text`` (English when that language has none)."""
    return pack_for(text).ack.get(key) or pack("en").ack.get(key, "")


def packs_for(text: Any) -> tuple[Pack, ...]:
    """The pack of the text's own language first, then every other language that has an entry (English first).
    For phrase matching only (a "what's up?" pull, a spoken mute): a short utterance carries too few function words to
    detect, and a closed phrase list has no false-friend problem the way the open class lists do (``pain``)."""
    first = pack_for(text)
    rest = [pack(code) for code in ("en",) + tuple(c for c in lexicons.LANGS if c != "en")]
    return (first,) + tuple(p for p in rest if p.present and p.lang != first.lang)
