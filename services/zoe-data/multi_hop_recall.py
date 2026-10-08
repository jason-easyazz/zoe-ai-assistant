"""multi_hop_recall - a bounded SECOND HOP for the questions one search cannot answer.

Why (ZMB axis l, measured on Zoe's real embedder 2026-10-07: 14 of 20 two-fact questions, dates 10/10, joins 4/10): a
question that needs TWO things the owner said ("is Dana's birthday before my dentist appointment", "who in my family
lives near Rowan's school") was answered by ONE vector search over the whole sentence. The first fact is found; the
second is buried under whatever else is nearest to the sentence's words, or - for a join - is not nearest to anything
the question says at all, because it is reachable only through a place the FIRST fact names.

Two bounded steps, both over the SAME owner-scoped ``search`` the packet already uses (nothing here reads a store):

  * subjects  a comparison ("X before Y", "older than", "compared to") names two subjects: each clause is searched on its
              own, so each subject gets its own top rows instead of sharing one ranking;
  * bridge    a relational question ("near", "next to", "the same school") follows the entities the first hop's rows
              about the question's own names mention (a place, an organisation, a person), one search per entity.

Never raises, never slows a turn by more than ``BUDGET_S`` (a slow store costs the second hop, not the answer), only on
a question of one of those two shapes (every other turn is byte-for-byte unchanged), and only adds rows that are the
owner's own and that actually mention what was searched for.

``ZOE_MULTI_HOP_RECALL`` = on (default) | off.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Awaitable, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

ENV = "ZOE_MULTI_HOP_RECALL"
BUDGET_S = 1.2            # the whole second hop, however many searches it makes
SUBJECT_ROWS = 3          # rows kept per subject clause
BRIDGE_ENTITIES = 2       # entities followed in the bridge step
BRIDGE_ROWS = 4           # rows searched per entity
BRIDGE_SEED_ROWS = 4      # first-hop rows the bridge entities are read from
MAX_EXTRA = 8             # rows the second hop may add at most

#: where a question compares two things: each side is a subject of its own
_COMPARE_RE = re.compile(
    r"\b(?:before|after|earlier than|later than|sooner than|compared (?:to|with)|versus|vs\.?|older than|younger than|"
    r"bigger than|taller than|more than|less than|same as)\b", re.IGNORECASE)
#: where a question joins two facts through a shared place / thing
_RELATIONAL_RE = re.compile(
    r"\b(?:near(?:by)?|close to|next to|next door|around the corner|in the same (?:town|city|suburb|street|area|place|school)|"
    r"same (?:town|city|suburb|street|area|place|school)|in common|from the same|who(?: else)? lives? (?:in|at|there))\b",
    re.IGNORECASE)
_LEAD_RE = re.compile(r"^\s*(?:is|are|was|were|do|does|did|will|would|can|could|when|what|who|which|how|tell me|remind me)\b\s*",
                      re.IGNORECASE)
_STOP = frozenset("""a an the is are was were be do does did of to in on at for from with about and or any some me my our your
their his her its i we you he she they it what whats when where who whom which how tell remind please also still now""".split())


def enabled() -> bool:
    return os.environ.get(ENV, "on").strip().lower() not in ("0", "false", "no", "off")


def _content(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(t) > 2 and t not in _STOP]


def subject_clauses(question: str) -> list[str]:
    """The subjects a COMPARISON question names, one clause each (``[]`` for any other question): the question cut at its
    comparison word, each side stripped of its question lead and kept only if it names something."""
    q = (question or "").strip().rstrip("?.! ")
    m = _COMPARE_RE.search(q)
    if not m:
        return []
    sides = [_LEAD_RE.sub("", q[:m.start()]).strip(" ,"), _LEAD_RE.sub("", q[m.end():]).strip(" ,")]
    out = [s for s in sides if _content(s)]
    return out if len(out) == 2 and out[0].casefold() != out[1].casefold() else []


def is_relational(question: str) -> bool:
    return bool(_RELATIONAL_RE.search(question or ""))


def _mentions(text: str, name: str) -> bool:
    low = " " + re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()) + " "
    low = re.sub(r"'s\b", "", low)
    n = re.sub(r"[^a-z0-9' ]+", " ", (name or "").lower()).strip()
    return bool(n) and re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", low) is not None


def bridge_entities(question: str, rows: Sequence[Any]) -> list[str]:
    """The entities to follow: those the first hop's rows ABOUT THE QUESTION'S OWN NAMES mention besides those names
    (a school's town, an employer, a person), in order of first appearance, at most ``BRIDGE_ENTITIES``. A question
    that names nothing has no bridge (it would follow whatever the nearest rows happen to say)."""
    try:
        from memory_gate import person_candidate_names
    except Exception:  # noqa: BLE001
        return []
    own = [n for n in person_candidate_names(question) if n.casefold() not in ("user", "you")]
    if not own:
        return []
    out: list[str] = []
    for r in list(rows)[:BRIDGE_SEED_ROWS]:
        text = getattr(r, "text", "") or ""
        if not any(_mentions(text, n) for n in own):
            continue
        for e in person_candidate_names(text):
            if e.casefold() in ("user", "you") or any(_mentions(e, n) or _mentions(n, e) for n in own):
                continue
            if e not in out:
                out.append(e)
    return out[:BRIDGE_ENTITIES]


Search = Callable[..., Awaitable[Any]]


async def _rows(search: Search, query: str, limit: int, must_mention: str = "") -> list[Any]:
    try:
        got = await search(query, limit=limit)
    except Exception as exc:  # noqa: BLE001 - a failed hop is no hop
        logger.debug("multi_hop_recall: search failed (%s)", type(exc).__name__)
        return []
    out = list(got or [])
    return [r for r in out if _mentions(getattr(r, "text", ""), must_mention)] if must_mention else out


async def _expand(search: Search, question: str, first: Sequence[Any]) -> list[Any]:
    clauses = subject_clauses(question)
    extras: list[Any] = []
    if clauses:
        for rows in await asyncio.gather(*[_rows(search, c, SUBJECT_ROWS) for c in clauses]):
            extras.extend(rows)
    if is_relational(question):
        ents = bridge_entities(question, first)
        if ents:
            for ent, rows in zip(ents, await asyncio.gather(*[_rows(search, e, BRIDGE_ROWS, must_mention=e) for e in ents])):
                extras.extend(rows)
    return extras


def merge(first: Sequence[Any], extras: Sequence[Any], limit: int) -> list[Any]:
    """``first`` and ``extras`` as one list of at most ``limit`` rows without repeats: the first rows lead (at least
    half of the room is theirs) and the second hop's rows follow them, ahead of the first hop's tail."""
    seen: set[str] = set()
    extra_rows: list[Any] = []
    for r in extras:
        rid = str(getattr(r, "id", ""))
        if rid and rid not in seen and not any(str(getattr(f, "id", "")) == rid for f in first):
            seen.add(rid)
            extra_rows.append(r)
    extra_rows = extra_rows[:MAX_EXTRA]
    if not extra_rows:
        return list(first)[:limit]
    head = max(limit - len(extra_rows), (limit + 1) // 2)
    head = min(head, len(first))
    return (list(first)[:head] + extra_rows + list(first)[head:])[:limit]


async def expand(search: Search, question: str, first: Sequence[Any], *, limit: int) -> list[Any]:
    """The first hop's rows plus the second hop's, at most ``limit``. ``search(query, limit=n)`` is the owner-scoped,
    status-filtered search (``MemoryService.search`` bound to a user). Returns ``first`` unchanged when the flag is off,
    the question is neither a comparison nor relational, or anything goes wrong."""
    first = list(first or [])
    if not enabled() or not (question or "").strip() or not (subject_clauses(question) or is_relational(question)):
        return first[:limit] if limit else first
    try:
        extras = await asyncio.wait_for(_expand(search, question, first), timeout=BUDGET_S)
    except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
        logger.debug("multi_hop_recall: second hop skipped (%s)", type(exc).__name__)
        return first[:limit]
    merged = merge(first, extras, limit)
    if extras:
        first_ids = {str(getattr(f, "id", "")) for f in first}
        logger.info("MULTI_HOP_RECALL added=%d subjects=%d relational=%d",
                    sum(1 for m in merged if str(getattr(m, "id", "")) not in first_ids),
                    len(subject_clauses(question)), int(is_relational(question)))
    return merged
