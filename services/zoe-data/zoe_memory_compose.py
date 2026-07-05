"""Compose relational Postgres facts into the /api/memories/for-prompt packet.

Increment 2b of the Samantha memory build plan. The for-prompt packet already
carries vector recall (MemPalace facts + semantic hits, see
``routers/memories._build_memory_prompt_packet``). This module ADDS the
*relational* half — the caller's people / relationships / important dates and
their ``user_portraits`` narrative from PostgreSQL — folded in as a compact,
**cited** block so the Pi soul (and tests) can see provenance.

Everything here is gated behind ``ZOE_MEMORY_COMPOSE_ENABLED`` (env, default
**OFF**). OFF is a byte-for-byte no-op: ``compose_relational_block`` returns
``None`` before touching the environment's DB and the caller emits the exact
pre-2b packet.

Design constraints (hot path, see build plan §2/§3):
- **No LLM, no embedder** — pure relational reads + string assembly.
- **Router-gated** — relational data is only pulled when the query needs it
  (``needs_relational``); otherwise the packet stays vector-only. This keeps the
  prompt small on the common (non-relational) turn.
- **No N+1** — the people / relationships / dates reads are three bounded
  batch queries, not per-person round-trips.
- **Scoped** — every read is per-user and honours the same
  ``visibility='family' OR user_id=?`` rule the people router enforces, so there
  is no cross-user leakage. Soft-deleted people are excluded.
- **Bounded** — the block is capped (row + char budgets) so it can't bloat the
  prompt.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Flag (default OFF) ─────────────────────────────────────────────────────

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def compose_enabled() -> bool:
    """Cheap per-call read of the 2b compose flag (default OFF).

    Same idiom as the other ``os.environ.get`` flags in this service. OFF must be
    a true no-op, so this is a plain truthiness check with no side effects.
    """
    return os.environ.get("ZOE_MEMORY_COMPOSE_ENABLED", "").strip().lower() in _TRUTHY


# ── Router gate: does this query want relational facts? ────────────────────
#
# Cheap keyword/pattern classifier — NO LLM. Attaching all relational data on
# every turn would bloat the prompt, so relational facts are only folded in when
# the message looks like it is about a *person / relationship / date*. The rule
# set is deliberately narrow and personal-relationship-flavoured (distinct from
# ``zoe_memory_router.RELATIONAL_TERMS``, which is about graph/causality/approval
# for the evolution harness, not "who is my brother").
#
# A query needs relational facts when it contains any of:
#   - a relationship noun (dad, mum, brother, wife, friend, colleague, boss, …)
#   - a "who is / tell me about <someone>" style person question
#   - a date/occasion word (birthday, anniversary, when is …)
# Documented so the gate's behaviour is auditable and testable.

# Relationship / people vocabulary. Whole-word matched (see ``_contains_word``).
_RELATIONAL_WORDS = frozenset(
    {
        # family
        "dad", "father", "mum", "mom", "mother", "parent", "parents",
        "brother", "sister", "sibling", "siblings", "son", "daughter",
        "child", "children", "kid", "kids", "grandma", "grandpa",
        "grandmother", "grandfather", "grandparent", "aunt", "uncle",
        "cousin", "niece", "nephew", "in-law", "family", "relatives",
        # partners
        "wife", "husband", "spouse", "partner", "girlfriend", "boyfriend",
        "fiance", "fiancee", "ex",
        # social / work
        "friend", "friends", "bestie", "colleague", "coworker", "co-worker",
        "boss", "manager", "mentor", "client", "neighbour", "neighbor",
        # generic people
        "person", "people", "someone", "contact", "contacts", "relationship",
        "relationships", "married", "dating",
        # date / occasion
        "birthday", "birthdays", "anniversary", "anniversaries",
    }
)

# "who is X", "tell me about X", "how is X doing" style person questions.
_PERSON_QUESTION_RE = re.compile(
    r"\b(who\s+(is|are|was|were)|tell\s+me\s+about|remind\s+me\s+about|"
    r"how\s+(is|are|old\s+is)|when\s+is|when'?s|what\s+is\s+.*\bname\b)\b"
)

_WORD_RE = re.compile(r"[a-z][a-z'\-]*")


def _contains_word(tokens: set[str], words: frozenset[str]) -> bool:
    return not tokens.isdisjoint(words)


def needs_relational(message: str) -> bool:
    """Router gate: True when the query is about a person / relationship / date.

    Cheap, deterministic, zero-LLM. Returns False for empty / non-relational
    messages so the packet stays vector-only on the common turn.
    """
    if not message:
        return False
    text = message.lower()
    tokens = set(_WORD_RE.findall(text))
    if _contains_word(tokens, _RELATIONAL_WORDS):
        return True
    if _PERSON_QUESTION_RE.search(text):
        return True
    return False


# ── Budgets ────────────────────────────────────────────────────────────────

_MAX_PEOPLE = 8
_MAX_RELATIONSHIPS = 8
_MAX_DATES = 8
_MAX_LINE_CHARS = 200
_PORTRAIT_MAX_CHARS = 600

# Citation tags — kept in one place so the soul + tests share the vocabulary.
CITE_PEOPLE = "[people]"
CITE_RELATIONSHIP = "[relationship]"
CITE_DATE = "[date]"
CITE_PORTRAIT = "[portrait]"

_MONTHS = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _clip(text: str, limit: int = _MAX_LINE_CHARS) -> str:
    text = " ".join((text or "").split())
    return text[:limit]


def _fmt_date(month: Optional[int], day: Optional[int], year: Optional[int]) -> str:
    parts: list[str] = []
    if month and 1 <= int(month) <= 12:
        parts.append(_MONTHS[int(month)])
    if day:
        parts.append(str(int(day)))
    label = " ".join(parts)
    if year:
        label = f"{label} {int(year)}".strip()
    return label.strip()


async def _fetch_relational(db, user_id: str) -> dict[str, list[dict[str, Any]]]:
    """Three bounded batch reads (people / relationships / dates). No N+1.

    Visibility mirrors the people router: a row is visible when it is
    ``visibility='family'`` OR owned by the caller. Soft-deleted people are
    excluded. Relationship + date rows are scoped by ``user_id`` (they are
    always owner-scoped in the schema).
    """
    out: dict[str, list[dict[str, Any]]] = {"people": [], "relationships": [], "dates": []}

    # People — visibility-scoped, non-deleted, non-partial preferred first.
    async with db.execute(
        """SELECT id, name, relationship, circle, context, notes
             FROM people
            WHERE deleted = 0
              AND (visibility = 'family' OR user_id = ?)
              AND (is_partial = 0 OR is_partial IS NULL)
            ORDER BY (last_contacted_at IS NULL), last_contacted_at DESC, name
            LIMIT ?""",
        (user_id, _MAX_PEOPLE),
    ) as cur:
        rows = await cur.fetchall()
    out["people"] = [dict(r) for r in rows]

    # Relationships — owner-scoped; resolve the two endpoint names in one join so
    # this stays a single query (no per-edge name lookup).
    async with db.execute(
        """SELECT pr.rel_a_to_b AS label, pa.name AS name_a, pb.name AS name_b, pr.notes
             FROM person_relationships pr
             JOIN people pa ON pa.id = pr.person_a_id AND pa.deleted = 0
             JOIN people pb ON pb.id = pr.person_b_id AND pb.deleted = 0
            WHERE pr.user_id = ?
              AND pr.valid_to IS NULL
            ORDER BY pr.updated_at DESC
            LIMIT ?""",
        (user_id, _MAX_RELATIONSHIPS),
    ) as cur:
        rows = await cur.fetchall()
    out["relationships"] = [dict(r) for r in rows]

    # Important dates — owner-scoped; join the person name in one query.
    async with db.execute(
        """SELECT pid.label, pid.date_type, pid.month, pid.day, pid.year, p.name
             FROM person_important_dates pid
             JOIN people p ON p.id = pid.person_id AND p.deleted = 0
            WHERE pid.user_id = ?
            ORDER BY pid.month, pid.day
            LIMIT ?""",
        (user_id, _MAX_DATES),
    ) as cur:
        rows = await cur.fetchall()
    out["dates"] = [dict(r) for r in rows]

    return out


async def _load_portrait(db, user_id: str) -> str:
    async with db.execute(
        "SELECT portrait_text FROM user_portraits WHERE user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return ""
    text = (row[0] if not isinstance(row, dict) else row.get("portrait_text")) or ""
    text = text.strip()
    if len(text) > _PORTRAIT_MAX_CHARS:
        text = text[:_PORTRAIT_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    return text


def _build_lines(data: dict[str, list[dict[str, Any]]], portrait: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Turn the batched rows into cited packet lines + a structured ref list."""
    lines: list[str] = []
    refs: list[dict[str, Any]] = []

    for p in data.get("people", []):
        name = (p.get("name") or "").strip()
        if not name:
            continue
        rel = (p.get("relationship") or "").strip()
        notes = (p.get("notes") or "").strip()
        desc = name
        if rel:
            desc += f" ({rel})"
        if notes:
            desc += f" — {notes}"
        lines.append(f"- {_clip(desc)} {CITE_PEOPLE}")
        refs.append({"source": "people", "name": name, "relationship": rel or None})

    for r in data.get("relationships", []):
        label = (r.get("label") or "").strip()
        name_a = (r.get("name_a") or "").strip()
        name_b = (r.get("name_b") or "").strip()
        if not (name_a and name_b):
            continue
        rel_word = label.lower() or "linked to"
        notes = (r.get("notes") or "").strip()
        desc = f"{name_a}'s {rel_word} is {name_b}"
        if notes:
            desc += f" — {notes}"
        lines.append(f"- {_clip(desc)} {CITE_RELATIONSHIP}")
        refs.append({"source": "relationship", "name_a": name_a, "name_b": name_b, "label": label or None})

    for d in data.get("dates", []):
        who = (d.get("name") or "").strip()
        label = (d.get("label") or d.get("date_type") or "date").strip()
        when = _fmt_date(d.get("month"), d.get("day"), d.get("year"))
        if not who or not when:
            continue
        desc = f"{who}'s {label}: {when}"
        lines.append(f"- {_clip(desc)} {CITE_DATE}")
        refs.append({"source": "date", "name": who, "label": label, "when": when})

    if portrait:
        lines.append(f"- {portrait} {CITE_PORTRAIT}")
        refs.append({"source": "portrait"})

    return lines, refs


async def compose_relational_block(user_id: str, message: str, db) -> Optional[dict[str, Any]]:
    """Build the cited relational block, or None when it should be skipped.

    Returns None (a true no-op for the caller) when:
      * the compose flag is OFF, or
      * the router gate says the query is not relational, or
      * there is nothing relational to add.

    Otherwise returns ``{"lines": [...], "refs": [...]}`` where each line is a
    cited string (``[people]`` / ``[relationship]`` / ``[date]`` / ``[portrait]``)
    ready to fold under the vector packet.

    Best-effort: any read failure logs and returns None so the packet degrades to
    vector-only rather than breaking a turn.
    """
    if not compose_enabled():
        return None
    if not needs_relational(message):
        return None
    try:
        data = await _fetch_relational(db, user_id)
        portrait = await _load_portrait(db, user_id)
    except Exception:
        logger.exception("memory compose: relational read failed (user=%s)", user_id)
        return None

    lines, refs = _build_lines(data, portrait)
    if not lines:
        return None
    return {"lines": lines, "refs": refs}


async def compose_packet(user_id: str, message: str) -> Optional[dict[str, Any]]:
    """Gate + open a DB context + build the cited relational block, or None.

    The single shared entry point for the composed relational half, called by
    BOTH ``routers/memories.py`` (chat's ``/for-prompt`` packet) and
    ``routers/voice_tts.py`` (the voice recall packet) so the compose/gate logic
    lives in one place and can't drift between the two paths.

    Returns None (a true no-op for the caller) when the flag is OFF, the router
    gate says the query is not relational, or there is nothing relational to add
    — the caller then emits its exact pre-compose output. Otherwise returns
    ``{"lines": [...], "refs": [...]}``.

    Cheap-gates BEFORE touching the DB pool: ``compose_enabled()`` and
    ``needs_relational(message)`` are pure/zero-cost, so the common
    (flag-OFF / non-relational) turn never opens a connection — preserving the
    hot path. Best-effort: any failure logs and returns None so the packet
    degrades to vector-only rather than breaking a turn.
    """
    if not compose_enabled():
        return None
    if not (message and message.strip()):
        return None
    if not needs_relational(message):
        return None
    try:
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            return await compose_relational_block(user_id, message, db)
    except Exception:
        logger.exception("memory compose: packet build failed (user=%s)", user_id)
        return None


__all__ = [
    "CITE_DATE",
    "CITE_PEOPLE",
    "CITE_PORTRAIT",
    "CITE_RELATIONSHIP",
    "compose_enabled",
    "compose_packet",
    "compose_relational_block",
    "needs_relational",
]
