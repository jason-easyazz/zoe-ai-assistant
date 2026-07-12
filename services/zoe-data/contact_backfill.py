"""Backfill known-but-not-a-contact people into accept-able contact proposals.

Phase 2b of the contacts-from-known-people bridge
(``docs/adr/ADR-contacts-from-known-people.md``). Zoe knows people from natural
conversation only in the narrative layer — MemPalace ``person`` / ``fact`` /
``relationship`` memories AND the synthesized third-person ``user_portraits``
prose (e.g. *"his parents, Janice and Niel, and his sisters, Karen and
Julie"*) — but they were never turned into structured ``people`` rows. This
one-shot admin pass reads that person knowledge from BOTH sources, extracts
distinct ``name`` (+ ``relationship`` if present) with two complementary passes
— the deterministic regexes + precision guard the go-forward path uses, PLUS an
LLM extraction over the combined text for the narrative prose the regexes can't
parse — dedups against the user's existing contacts, and emits a batch of
``person_create`` **pending suggestions**.

It creates PROPOSALS the user accepts through the existing suggestions UI — never
a silent direct contact write. Flag-gated behind ``ZOE_CONTACT_BACKFILL_ENABLED``
(default OFF); a true no-op when off. Demo-user lab-proved before prod.
"""

from __future__ import annotations

import json
import logging
import os
import re

import httpx

from gemma_endpoint import gemma_base

logger = logging.getLogger(__name__)

# LLM extraction reuses the same local llama-server (Gemma) client idiom as
# ``user_portrait._call_llm_for_portrait`` / ``latent_intent_detector._complete``:
# one bare ``gemma_base()`` base + ``/v1/chat/completions``, non-streaming, low
# temperature, JSON-only system prompt. Same model default as the memory digest.
_EXTRACT_MODEL = os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")

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
# Wrap `_NAME` in a NAMED group so every call site reads `m.group("name")` —
# robust to `_NAME` ever gaining internal capturing groups (positional indices
# would silently shift otherwise). Only one `_NAME` appears per pattern below.
_NM = rf"(?P<name>{_NAME})"

# "my mother Janice" / "my friend Bob"
_MY_REL_RE = re.compile(rf"\b(?i:my)\s+(?P<rel>{_ROLE_ALT_CI})\s+{_NM}")
# "Janice is my mother" / "Janice, my mother"
_REL_MY_RE = re.compile(
    rf"{_NM}\s*,?\s+(?:(?i:is)\s+)?(?i:my)\s+(?P<rel>{_ROLE_ALT_CI})\b"
)
# "Jason's mother is Janice" / "Jason's sister, Karen"
_POSS_REL_RE = re.compile(
    rf"[A-Z][a-z]+'s\s+(?P<rel>{_ROLE_ALT_CI})\s+(?:(?i:is|are)|,)\s+{_NM}"
)
# "Janice (mother)" — relationship in parens; validated against the role set below.
_PAREN_REL_RE = re.compile(rf"{_NM}\s*\(\s*(?P<rel>[A-Za-z ]{{2,20}}?)\s*\)")

# Name-only signals (a person is clearly named, no relationship stated). These
# mirror person_extractor's fact patterns so a person mentioned only through a
# like/birthday/work/meeting fact still becomes a proposal. Keyword literals are
# case-insensitive; `_NAME` stays case-sensitive (see note above).
_NAME_ONLY_RES = (
    re.compile(rf"{_NM}\s+(?i:loves?|likes?|hates?|prefers?|enjoys?|dislikes?)\s"),
    re.compile(rf"{_NM}(?:'s)?\s+(?i:birthday)\b"),
    re.compile(rf"{_NM}\s+(?i:works?)\s+(?i:at|for)\b"),
    re.compile(rf"(?i:met|caught\s+up\s+with)\s+{_NM}\b"),
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
        _add(out, m.group("name"), _norm_rel(m.group("rel")))
    for m in _REL_MY_RE.finditer(text):
        _add(out, m.group("name"), _norm_rel(m.group("rel")))
    for m in _POSS_REL_RE.finditer(text):
        _add(out, m.group("name"), _norm_rel(m.group("rel")))
    for m in _PAREN_REL_RE.finditer(text):
        rel = _norm_rel(m.group("rel"))
        if rel:  # only trust parens when they hold a real role
            _add(out, m.group("name"), rel)

    # Name-only fills (do not overwrite a relationship already found above).
    for rx in _NAME_ONLY_RES:
        for m in rx.finditer(text):
            _add(out, m.group("name"), None)

    return list(out.values())


# Memory types whose text can name a person the user relates to. Broadened
# beyond ``person`` (Phase 2b.2): family/friends most often live in ``fact`` and
# ``relationship`` memories (e.g. "Karen loves tea", "Niel is Jason's father"),
# so those feed backfill too. The precision guard + LLM/regex extraction below
# still decide what counts as a proposable person, so the wider net is safe.
_PERSON_KNOWLEDGE_TYPES = frozenset({"person", "fact", "relationship"})


def _is_person_knowledge_memory(ref, user_id: str) -> bool:
    """True for MemPalace rows whose text may name a person the user knows.

    Accepts ``person`` / ``fact`` / ``relationship`` memory types plus any row
    carrying an explicit person entity-type or ``person`` tag.

    Ownership gate (Phase 2b.2 security): ``load_for_prompt`` also returns
    family-SHARED rows owned by OTHER users (``visibility == "family"``), so
    broadening to ``fact`` / ``relationship`` could otherwise leak another
    household member's known people as this user's contact candidates (e.g.
    "Bob is Andrew's colleague" surfacing under Jason). Only the caller's own
    person-knowledge feeds backfill:

    * a row whose metadata ``user_id`` names a DIFFERENT user → rejected;
    * an OWNERLESS row that is shared/family-visible → rejected too (an old
      shared row may lack an owner stamp, and family-visible rows can belong to
      another household user);
    * a truly private legacy row (no owner, not shared) → treated as the
      caller's, for back-compat with person rows that predate owner stamping.
    """
    md = getattr(ref, "metadata", None) or {}
    owner = str(md.get("user_id") or "").strip().lower()
    visibility = str(md.get("visibility") or "").strip().lower()
    caller = str(user_id or "").strip().lower()
    if owner != caller and (owner or visibility in {"family", "shared"}):
        return False
    if str(md.get("memory_type", "")).strip().lower() in _PERSON_KNOWLEDGE_TYPES:
        return True
    if str(md.get("entity_type", "")).strip().lower() in ("person", "person_pending"):
        return True
    tags = str(md.get("tags", "") or "").lower()
    return "person" in tags.split(",")


# ── Portrait source ───────────────────────────────────────────────────────────


async def _load_portrait_text(user_id: str, db) -> str:
    """Return the user's FULL synthesized portrait prose, or '' if none.

    A direct ``user_portraits`` read (see ``user_portrait.load_portrait`` for the
    same table/column). Unlike ``load_portrait``, this does NOT truncate to the
    per-turn inject budget — backfill wants every name the prose mentions. Best
    effort: any error (missing table, no db) yields '' so backfill still runs on
    the memory sources alone. Isolated so tests can monkeypatch it.
    """
    if db is None:
        return ""
    try:
        # Dual placeholder style, mirroring person_extractor._resolve_person_uuid:
        # asyncpg ($1) first, aiosqlite (?) on fallback.
        try:
            cur = await db.execute(
                "SELECT portrait_text FROM user_portraits WHERE user_id=$1", user_id
            )
        except Exception:
            cur = await db.execute(
                "SELECT portrait_text FROM user_portraits WHERE user_id=?", (user_id,)
            )
        row = await cur.fetchone()
        if row and row[0]:
            return str(row[0]).strip()
    except Exception as exc:
        logger.debug("contact_backfill: portrait read failed user=%s: %s", user_id, exc)
    return ""


# ── LLM extraction pass ───────────────────────────────────────────────────────

_LLM_EXTRACT_SYSTEM = "You extract people from text. Return ONLY a valid JSON array."

_LLM_EXTRACT_PROMPT = """\
From the text below, list every real person the user has a PERSONAL relationship \
with — family, partners, friends, colleagues they know personally.

Rules:
- Only people the user personally knows. Skip public figures, fictional \
characters, brands, and the user themselves.
- Use the person's given name only, no titles.
- Give a short relationship label from the user's point of view (e.g. "mother", \
"sister", "wife", "son", "friend", "colleague"). Use "" if the text doesn't say.

Return a JSON array of objects like [{{"name": "Janice", "relationship": "mother"}}]. \
Return [] if there are no such people.

TEXT:
{text}
"""


def _parse_llm_people(raw: str) -> list[tuple[str, str | None]]:
    """Parse the LLM's JSON array into ``(name, relationship_or_None)`` pairs.

    Tolerant of prose around the array and of malformed items — anything that
    isn't a clean ``{"name", "relationship"}`` object is skipped, and a
    non-JSON / non-list body yields ``[]`` (caller falls back to regex).
    """
    if not raw:
        return []
    try:
        start, end = raw.find("["), raw.rfind("]") + 1
        if start == -1 or end <= start:
            return []
        items = json.loads(raw[start:end])
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    out: list[tuple[str, str | None]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        rel = str(item.get("relationship") or "").strip().lower()
        # Keep the label short; drop obviously non-label sentences.
        if not rel or len(rel) > 40:
            rel = ""
        out.append((name, rel or None))
    return out


async def _llm_extract_people(text: str) -> list[tuple[str, str | None]]:
    """LLM pass: extract ``(name, relationship)`` people from combined prose.

    Reuses the local Gemma client idiom (see ``_EXTRACT_MODEL`` note above).
    Returns ``[]`` on any transport/decode failure so backfill degrades to the
    deterministic regex results instead of crashing.
    """
    text = (text or "").strip()
    if not text:
        return []
    payload = {
        "model": _EXTRACT_MODEL,
        "messages": [
            {"role": "system", "content": _LLM_EXTRACT_SYSTEM},
            {"role": "user", "content": _LLM_EXTRACT_PROMPT.format(text=text[:6000])},
        ],
        "max_tokens": 400,
        "temperature": 0.1,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(f"{gemma_base()}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.debug("contact_backfill: LLM extraction failed: %s", exc)
        return []
    return _parse_llm_people(raw)


# ── Self-identity guard ───────────────────────────────────────────────────────

# Alphabetic tokens (≥2 chars) of a user id, used to recognise the user's own
# name in extracted prose. Split on any non-letter so username-derived slugs and
# suffixed ids compare against name tokens: "jason" → {jason}; "jason_2" →
# {jason}; "jason.smith" → {jason, smith}. A pure-numeric suffix drops out.
_SELF_TOKEN_RE = re.compile(r"[^a-z]+")


def _name_tokens(raw: str) -> frozenset[str]:
    """Alphabetic (≥2-char) lowercase tokens of ``raw`` (a name or id)."""
    return frozenset(t for t in _SELF_TOKEN_RE.split(str(raw or "").lower()) if len(t) >= 2)


def _self_identity_tokens(user_id: str) -> frozenset[str]:
    """Comparable identity tokens for the user id (see note above)."""
    return _name_tokens(str(user_id or "").strip())


async def _canonical_name_tokens(user_id: str, db) -> frozenset[str]:
    """Identity tokens from the user's CANONICAL display name (``users.name``).

    The raw id can be a handle ("easyazz") that shares no token with the name
    the portrait/facts use ("Jason"), which would let the user's own name slip
    through the self-filter. Fold in their real name so "Jason is …" prose is
    still recognised as the user. Best effort: '' on any error (no users table,
    no db) so backfill still runs on the id tokens alone.
    """
    if db is None:
        return frozenset()
    try:
        try:
            cur = await db.execute("SELECT name FROM users WHERE id=$1", user_id)
        except Exception:
            cur = await db.execute("SELECT name FROM users WHERE id=?", (user_id,))
        row = await cur.fetchone()
        if row and row[0]:
            return _name_tokens(row[0])
    except Exception as exc:
        logger.debug("contact_backfill: canonical-name read failed user=%s: %s", user_id, exc)
    return frozenset()


# ── Backfill entry point ──────────────────────────────────────────────────────


async def backfill_contacts(
    user_id: str, *, session_id: str = "backfill", db=None
) -> dict:
    """Read the user's person knowledge and emit ``person_create`` proposals.

    Returns a summary: ``proposed`` (suggestions stored), ``skipped_existing``
    (candidates already a contact), ``candidates`` (distinct people found). A
    byte-for-byte no-op — no reads, no writes — when the flag is off.

    ``session_id`` is the session the proposals are stored under. The suggestions
    retrieval paths (``list_active`` / ``load_for_prompt``) filter by session, so
    pass the user's ACTIVE session to have them surface in a live chat; the
    ``"backfill"`` default just parks them for an out-of-band accept flow.
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

    # Person-knowledge memory texts: person / fact / relationship rows (Phase
    # 2b.2 broadened the net beyond `person` — family most often lives in facts).
    knowledge_texts = [
        (getattr(ref, "text", "") or "").strip()
        for ref in refs
        if _is_person_knowledge_memory(ref, user_id)
    ]
    knowledge_texts = [t for t in knowledge_texts if t]

    self_name = str(user_id).strip().lower()
    self_tokens = set(_self_identity_tokens(user_id))  # augmented w/ canonical name below
    people: dict[str, tuple[str, str | None]] = {}

    def _record(name: str, rel: str | None) -> None:
        """Merge one (name, rel) into `people`, keyed case-insensitively.

        Skips the user themselves and non-person junk. A relationship-bearing
        hit upgrades an earlier bare-name hit; it never downgrades one that
        already has a rel.
        """
        name = (name or "").strip()
        if not name or not _looks_like_person_name(name):
            return
        # Never propose the literal "User" or the assistant herself — both were
        # proposed from portrait/fact prose in live review (QA review F5d).
        from pending_suggestions import is_junk_contact_name
        if is_junk_contact_name(name):
            return
        key = name.lower()
        # Skip the user themselves: an exact full-name match OR the extracted
        # name's FIRST token matching one of the user id's identity tokens — so
        # user "jason" / "jason_2" / "jason.smith" all drop a "Jason" or "Jason
        # Smith" pulled from portrait/fact prose. A mere prefix ("Jan") is kept
        # (its token "jan" isn't an identity token of "jason").
        first = key.split()[0] if key.split() else key
        if key == self_name or first in self_tokens:
            return
        existing = people.get(key)
        if existing is None:
            people[key] = (name, rel or None)
        elif rel and not existing[1]:
            people[key] = (existing[0], rel)

    # 2) Open the DB early — needed for the portrait read AND the dedup below.
    from person_extractor import _ensure_db, _resolve_person_uuid

    _db, opened = await _ensure_db(db)
    if _db is None:
        return summary
    try:
        # 2a0) Fold the user's canonical display name into the self-filter, so a
        # handle id ("easyazz") still recognises "Jason is …" prose as the user.
        self_tokens |= await _canonical_name_tokens(user_id, _db)

        # 2a) Add the synthesized portrait prose as another person-knowledge
        # source (third-person narrative the go-forward extractor never sees).
        portrait = await _load_portrait_text(user_id, _db)
        if portrait:
            knowledge_texts.append(portrait)

        # 2b) Deterministic regex pass over every knowledge text.
        for text in knowledge_texts:
            for name, rel in _extract_people(text):
                _record(name, rel)

        # 2c) LLM pass over the combined text — catches narrative prose the
        # regexes can't parse. Failure (transport/decode) falls back to the
        # regex results already recorded above; it never crashes backfill.
        try:
            llm_people = await _llm_extract_people("\n\n".join(knowledge_texts))
        except Exception as exc:
            logger.debug("contact_backfill: LLM pass errored, regex-only: %s", exc)
            llm_people = []
        for name, rel in llm_people:
            _record(name, rel)

        # Drop a bare-first-name candidate when a full-name candidate with the
        # same first name exists in this batch — "Lindsay" + "Lindsay Cannon"
        # previously produced two duplicate proposals (QA review F5d). The
        # full-name entry inherits the bare entry's relationship if it has none.
        full_by_first: dict[str, list[str]] = {}
        for k in people:
            if len(k.split()) > 1:
                full_by_first.setdefault(k.split()[0], []).append(k)
        for bare_key in [k for k in people if len(k.split()) == 1 and k in full_by_first]:
            matches = full_by_first[bare_key]
            # Donate the bare hit's relationship ONLY when exactly one full-name
            # candidate shares the first token — with "Lindsay Cannon" AND
            # "Lindsay Smith" in the batch we can't know whose it is.
            bare_rel = people[bare_key][1]
            if bare_rel and len(matches) == 1 and not people[matches[0]][1]:
                people[matches[0]] = (people[matches[0]][0], bare_rel)
            del people[bare_key]

        summary["candidates"] = len(people)
        if not people:
            return summary

        # 3) Dedup against the user's existing non-deleted contacts.
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
