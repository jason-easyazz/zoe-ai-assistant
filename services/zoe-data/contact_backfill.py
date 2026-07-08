"""Backfill known-but-not-a-contact people into accept-able contact proposals.

Phase 2b of the contacts-from-known-people bridge
(``docs/adr/ADR-contacts-from-known-people.md``). Zoe knows people from natural
conversation only in the narrative layer — MemPalace ``person``-type memories +
the synthesized portrait — but they were never turned into structured ``people``
rows. This one-shot admin pass reads that person knowledge, extracts distinct
``name`` (+ ``relationship`` if present) with the same deterministic regexes and
precision guard the go-forward path uses, dedups against the user's existing
contacts, and emits a batch of ``person_create`` **pending suggestions**.

It creates PROPOSALS the user accepts through the existing suggestions UI — never
a silent direct contact write. Flag-gated behind ``ZOE_CONTACT_BACKFILL_ENABLED``
(default OFF); a true no-op when off. Demo-user lab-proved before prod.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def contact_backfill_enabled() -> bool:
    """Cheap per-call read of the backfill flag (default OFF).

    Gates the whole one-shot backfill pass so it stays dark until turned on. See
    ``docs/adr/ADR-contacts-from-known-people.md`` (Phase 2b).
    """
    return os.environ.get("ZOE_CONTACT_BACKFILL_ENABLED", "").strip().lower() in _TRUTHY


# ── Person-name / relationship extraction ─────────────────────────────────────
#
# Deterministic regex only — no LLM. Borrows the role vocabulary + name shape +
# pronoun guard from ``person_extractor`` so backfill and the live extractor
# agree on what counts as a person and a relationship.

from person_extractor import (  # noqa: E402 — imported after logger/flag on purpose
    _NAME,
    _REL_RE,
    _ROLE_TO_TYPE,
    _looks_like_person_name,
)

# Longest-first alternation of the known relationship words (e.g. "grandparent"
# before "parent") so the regex prefers the more specific role.
_ROLE_ALT = "|".join(sorted(_ROLE_TO_TYPE, key=len, reverse=True))
# Case-insensitive ONLY on the keyword literals — `_NAME` must stay
# case-sensitive so its `[A-Z][a-z]` name shape isn't neutralised by a global
# re.IGNORECASE (which would let it over-capture a trailing lowercase word, e.g.
# "Bob came" from "my friend Bob came over"). Python scoped inline flags do this.
_ROLE_ALT_CI = f"(?i:{_ROLE_ALT})"

# "my mother Janice" / "my friend Bob"
_MY_REL_RE = re.compile(rf"\b(?i:my)\s+(?P<rel>{_ROLE_ALT_CI})\s+{_NAME}")
# "Janice is my mother" / "Janice, my mother"
_REL_MY_RE = re.compile(
    rf"{_NAME}\s*,?\s+(?:(?i:is)\s+)?(?i:my)\s+(?P<rel>{_ROLE_ALT_CI})\b"
)
# "Jason's mother is Janice" / "Jason's sister, Karen"
_POSS_REL_RE = re.compile(
    rf"[A-Z][a-z]+'s\s+(?P<rel>{_ROLE_ALT_CI})\s+(?:(?i:is|are)|,)\s+{_NAME}"
)
# "Janice (mother)" — relationship in parens; validated against the role set below.
_PAREN_REL_RE = re.compile(rf"{_NAME}\s*\(\s*(?P<rel>[A-Za-z ]{{2,20}}?)\s*\)")

# Name-only signals (a person is clearly named, no relationship stated). These
# mirror person_extractor's fact patterns so a person mentioned only through a
# like/birthday/work/meeting fact still becomes a proposal. Keyword literals are
# case-insensitive; `_NAME` stays case-sensitive (see note above).
_NAME_ONLY_RES = (
    re.compile(rf"{_NAME}\s+(?i:loves?|likes?|hates?|prefers?|enjoys?|dislikes?)\s"),
    re.compile(rf"{_NAME}(?:'s)?\s+(?i:birthday)\b"),
    re.compile(rf"{_NAME}\s+(?i:works?)\s+(?i:at|for)\b"),
    re.compile(rf"(?i:met|caught\s+up\s+with)\s+{_NAME}\b"),
)


def _norm_rel(raw: str) -> str | None:
    """Return a clean relationship word if ``raw`` is a known role, else None."""
    key = (raw or "").strip().lower()
    if key in _ROLE_TO_TYPE:
        return key
    if key.rstrip("s") in _ROLE_TO_TYPE:  # tolerate a plural ("sisters")
        return key.rstrip("s")
    return None


def _add(out: dict[str, tuple[str, str | None]], name: str, rel: str | None) -> None:
    """Record a candidate, keyed case-insensitively. A relationship-bearing hit
    upgrades an earlier bare-name hit for the same person (never downgrades)."""
    name = (name or "").strip()
    if not name or not _looks_like_person_name(name):
        return
    key = name.lower()
    existing = out.get(key)
    if existing is None:
        out[key] = (name, rel)
    elif rel and not existing[1]:
        out[key] = (existing[0], rel)


def _extract_people(text: str) -> list[tuple[str, str | None]]:
    """Extract distinct ``(name, relationship_or_None)`` pairs from one memory.

    Relationship-bearing patterns run first so a person's role is captured;
    name-only patterns fill in people mentioned without a stated relationship.
    """
    out: dict[str, tuple[str, str | None]] = {}
    if not text:
        return []

    # "A is B's <role>" / "C and D are <role>s" (person_extractor's own regex).
    for m in _REL_RE.finditer(text):
        if m.group("role1"):
            _add(out, m.group("a"), _norm_rel(m.group("role1")))
        elif m.group("role2"):
            rel = _norm_rel(m.group("role2"))
            _add(out, m.group("c"), rel)
            _add(out, m.group("d"), rel)

    for m in _MY_REL_RE.finditer(text):
        _add(out, m.group(2), _norm_rel(m.group("rel")))
    for m in _REL_MY_RE.finditer(text):
        _add(out, m.group(1), _norm_rel(m.group("rel")))
    for m in _POSS_REL_RE.finditer(text):
        _add(out, m.group(2), _norm_rel(m.group("rel")))
    for m in _PAREN_REL_RE.finditer(text):
        rel = _norm_rel(m.group("rel"))
        if rel:  # only trust parens when they hold a real role
            _add(out, m.group(1), rel)

    # Name-only fills (do not overwrite a relationship already found above).
    for rx in _NAME_ONLY_RES:
        for m in rx.finditer(text):
            _add(out, m.group(1), None)

    return list(out.values())


def _is_person_memory(ref) -> bool:
    """True for MemPalace rows that are about a person (type/entity/tag signal)."""
    md = getattr(ref, "metadata", None) or {}
    if str(md.get("memory_type", "")).strip().lower() == "person":
        return True
    if str(md.get("entity_type", "")).strip().lower() in ("person", "person_pending"):
        return True
    tags = str(md.get("tags", "") or "").lower()
    return "person" in tags.split(",")


# ── Backfill entry point ──────────────────────────────────────────────────────


async def backfill_contacts(
    user_id: str, *, session_id: str = "backfill", db=None
) -> dict:
    """Read the user's person knowledge and emit ``person_create`` proposals.

    Returns a summary: ``proposed`` (suggestions stored), ``skipped_existing``
    (candidates already a contact), ``candidates`` (distinct people found). A
    byte-for-byte no-op — no reads, no writes — when the flag is off.
    """
    summary = {
        "enabled": False,
        "proposed": 0,
        "skipped_existing": 0,
        "candidates": 0,
    }
    if not contact_backfill_enabled():
        return summary
    summary["enabled"] = True

    from memory_service import get_memory_service, is_guest_memory_user

    if not user_id or is_guest_memory_user(user_id):
        return summary

    # 1) Pull the user's person knowledge from MemPalace (metadata read).
    try:
        refs = await get_memory_service().load_for_prompt(user_id, limit=200)
    except Exception as exc:
        logger.warning("contact_backfill: memory read failed user=%s: %s", user_id, exc)
        return summary

    # 2) Extract distinct people from person-related memories.
    people: dict[str, tuple[str, str | None]] = {}
    self_name = str(user_id).strip().lower()
    for ref in refs:
        if not _is_person_memory(ref):
            continue
        for name, rel in _extract_people(getattr(ref, "text", "") or ""):
            key = name.lower()
            # Never propose the user themselves as their own contact.
            if key == self_name or self_name.startswith(key):
                continue
            existing = people.get(key)
            if existing is None:
                people[key] = (name, rel)
            elif rel and not existing[1]:
                people[key] = (existing[0], rel)

    summary["candidates"] = len(people)
    if not people:
        return summary

    # 3) Dedup against the user's existing non-deleted contacts.
    from person_extractor import _ensure_db, _resolve_person_uuid

    _db, opened = await _ensure_db(db)
    if _db is None:
        return summary
    try:
        suggestions: list[dict] = []
        for name, rel in people.values():
            existing_uuid = await _resolve_person_uuid(name, user_id, _db)
            if existing_uuid:
                summary["skipped_existing"] += 1
                continue
            slots = {"name": name}
            if rel:
                slots["relationship"] = rel
            desc = f"Add {name}" + (f" ({rel})" if rel else "") + " to contacts"
            suggestions.append(
                {
                    "action_type": "person_create",
                    "description": desc,
                    "offer_phrase": f"Add {name} to your contacts?",
                    "pre_filled_slots": slots,
                }
            )
    finally:
        if opened and _db is not None:
            try:
                await _db.close()
            except Exception:
                pass

    # 4) Store the proposals. store_suggestions caps each call at 3, so chunk.
    from pending_suggestions import store_suggestions

    for i in range(0, len(suggestions), 3):
        summary["proposed"] += await store_suggestions(
            user_id, session_id, suggestions[i : i + 3]
        )

    logger.info(
        "contact_backfill: user=%s candidates=%d proposed=%d skipped_existing=%d",
        user_id, summary["candidates"], summary["proposed"], summary["skipped_existing"],
    )
    return summary


__all__ = ["backfill_contacts", "contact_backfill_enabled"]
