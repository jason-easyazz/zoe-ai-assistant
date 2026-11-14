"""The web-lookup ANSWER CONTRACT: what Zoe may say after a lookup, and what a page may never do.

Three rules, enforced in code:

  1. Every web-derived claim carries its SOURCE: the page title and the site
     (``Claim``). A row with no title, no public site or no text is not a source
     and is withheld.
  2. The reply SAYS it looked the thing up - or says, as plainly, that it tried
     and could not, or that it did not send the question at all. A failed or
     refused lookup is never worded as "nothing says that".
  3. Page content is UNTRUSTED DATA, never instructions. The reply is composed
     HERE from fixed sentences (``lexicons_data/web_lookup_<lang>.json``); page
     text only ever fills the quoted part, neutralised and cut. A row that
     addresses the assistant, claims a role, carries chat-template or tool-call
     markup, or asks for secrets is withheld whole (``is_instruction``): never
     obeyed, never spoken, never passed on to the brain (``screen_rows``).

``build_answer`` returns text and sources only. It has no way to express an
action, a tool call or a second search, so nothing a page says can cause one.
``check_reply`` judges a reply written by anything else (the brain) against
rules 1 and 2. Pure functions; no I/O beyond the cached lexicon read.
Operator doc: docs/knowledge/web-lookup.md.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping
from urllib.parse import urlsplit

from web_lookup_provider import public_link
from web_lookup_scrub import load_lexicon

STATUS_WITHHELD = "withheld"  # rows came back, none usable as a source
MAX_CLAIMS = 2
MAX_TITLE_CHARS = 80
MAX_CLAIM_CHARS = 240
# Statuses where a query actually left the house (or was attempted).
_ATTEMPTED = frozenset({"results", "no_results", "blocked", "error", STATUS_WITHHELD})

_INVISIBLE_RE = re.compile(r"[\u0000-\u001f\u007f-\u009f­​-‏ -‮⁠-⁩﻿]")
_ENTITY_RE = re.compile(r"(?i)&(?:lt|gt|#0*6[02]|#x0*3[ce]);?")
_LT_ENTITY_RE = re.compile(r"(?i)&(?:lt|#0*60|#x0*3c);?")
_GT_ENTITY_RE = re.compile(r"(?i)&(?:gt|#0*62|#x0*3e);?")
_TAG_RE = re.compile(r"<[^<>]{0,200}>")
_INST_RE = re.compile(r"(?i)\[/?INST\]")
_ROLE_RE = re.compile(r"(?i)\b(system|assistant|user|developer|tool|model|human)\s*:")
# Markup no honest search snippet carries: chat-template / tool-call tokens, fence markers, role lines.
_MARKUP_RE = re.compile(
    r"(?i)<\|[^>]{0,40}\|?>|<[^>]{0,40}\|>|<</?sys>>|\[/?inst\]|<<<|>>>|</?(?:system|assistant|tool_call|start_of_turn|end_of_turn|script)\b"
    r"|\b(?:system|assistant|developer)\s*:"
)


@dataclass(frozen=True, slots=True)
class Claim:
    text: str
    source_title: str
    source_site: str


@dataclass(frozen=True, slots=True)
class LookupAnswer:
    status: str
    looked_up: bool  # a query left the house (or was attempted)
    text: str  # the whole reply, composed by code
    claims: tuple[Claim, ...] = ()
    withheld: int = 0  # rows dropped: instruction-shaped, or not a usable source


def neutralise(raw: object, limit: int) -> str:
    """One field of page text made safe to quote: no markup, no control or invisible characters, no role
    markers, one line, cut at ``limit``. Mirrors the sidecar's ``neutraliseUntrustedText``."""
    s = unicodedata.normalize("NFKC", str(raw or ""))
    s = _INVISIBLE_RE.sub(" ", s)
    s = _ENTITY_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = re.sub(r"[<>]", " ", s)
    s = _INST_RE.sub(" ", s)
    s = _ROLE_RE.sub(r"\1 -", s)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def site_of(url: object) -> str:
    """The site a public link belongs to (host without ``www.``), or ""."""
    link = public_link(url)
    host = (urlsplit(link).hostname or "").rstrip(".").lower() if link else ""
    return host[4:] if host.startswith("www.") else host


@lru_cache(maxsize=8)
def _compiled(patterns: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p) for p in patterns)


def _reading_form(text: object) -> str:
    """What a reader would see: NFKC, invisible characters removed, lowercased, one line."""
    s = _INVISIBLE_RE.sub("", unicodedata.normalize("NFKC", str(text or "")))
    return " ".join(s.casefold().split())


def is_instruction(text: object, lexicon: Mapping | None = None) -> bool:
    """True when page text reads as an instruction aimed at the assistant, or carries model/tool markup."""
    lex = lexicon or load_lexicon()
    raw = unicodedata.normalize("NFKC", str(text or ""))
    unescaped = _GT_ENTITY_RE.sub(">", _LT_ENTITY_RE.sub("<", raw))
    if _MARKUP_RE.search(raw) or _MARKUP_RE.search(unescaped):
        return True
    reading = _reading_form(raw)
    return any(rx.search(reading) for rx in _compiled(tuple(lex.get("instruction_cues", ()))))


def screen_rows(rows: object, lexicon: Mapping | None = None) -> tuple[list[dict[str, str]], int]:
    """(rows that may go on to the brain, how many were withheld as instruction-shaped). Rows stay DATA."""
    kept: list[dict[str, str]] = []
    withheld = 0
    for row in rows if isinstance(rows, (list, tuple)) else ():
        if not isinstance(row, Mapping):
            continue
        if any(is_instruction(row.get(k), lexicon) for k in ("title", "snippet")):
            withheld += 1
            continue
        kept.append({k: str(row.get(k, "") or "") for k in ("title", "url", "snippet")})
    return kept, withheld


def _sentences(lexicon: Mapping, lang: str) -> Mapping[str, str]:
    answers = lexicon.get("answer", {})
    return answers.get(lang) or next(iter(answers.values()), {})


def build_answer(status: str, rows: object = (), *, lexicon: Mapping | None = None, lang: str = "en") -> LookupAnswer:
    """The reply for a lookup that ended ``status`` with ``rows``. Never raises; never returns page text unquoted."""
    lex = lexicon or load_lexicon()
    say = _sentences(lex, lang)
    if status != "results":
        status = status if isinstance(status, str) and status in say else "error"
        return LookupAnswer(status, status in _ATTEMPTED, say.get(status, ""))
    kept, withheld = screen_rows(rows, lex)
    claims: list[Claim] = []
    for row in kept:
        title, site, text = neutralise(row["title"], MAX_TITLE_CHARS), site_of(row["url"]), neutralise(row["snippet"], MAX_CLAIM_CHARS)
        if not (title and site and text):
            withheld += 1
        elif len(claims) < MAX_CLAIMS:
            claims.append(Claim(text, title, site))
    if not claims:
        return LookupAnswer(STATUS_WITHHELD, True, say.get(STATUS_WITHHELD, ""), (), withheld)
    parts = [say.get("looked_up", "")] + [
        say.get("claim", "{title} ({site}): {text}").format(title=c.source_title, site=c.source_site, text=c.text) for c in claims
    ]
    return LookupAnswer("results", True, " ".join(p for p in parts if p), tuple(claims), withheld)


def check_reply(reply: object, answer: LookupAnswer, lexicon: Mapping | None = None) -> list[str]:
    """Violations of the contract in a reply written from ``answer``: ``no_disclosure`` (it does not say a lookup
    happened, or failed, or was not sent) and ``no_source`` (it gives a web-derived claim without naming a
    source title or site). An empty list means the reply keeps the contract."""
    lex = lexicon or load_lexicon()
    reading = _reading_form(reply)
    problems = []
    if not any(rx.search(reading) for rx in _compiled(tuple(lex.get("disclosure_cues", ())))):
        problems.append("no_disclosure")
    if answer.claims and not any(
        _reading_form(c.source_site) in reading or _reading_form(c.source_title) in reading for c in answer.claims
    ):
        problems.append("no_source")
    return problems
