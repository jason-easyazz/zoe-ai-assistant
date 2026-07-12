"""
LLM-driven nightly memory digest.

Reads today's chat messages for a user, prompts Gemma to extract personal
facts as structured JSON, deduplicates against existing MemPalace records,
and writes new facts through MemoryService (the sole memory writer).

Usage:
    result = await run_memory_digest(user_id="jason", db=db_session)
    # {"user_id": "jason", "extracted": 5, "new": 3, "skipped_duplicates": 2}

Scheduled by routers/system.py at 3am daily.
Manual trigger: POST /api/memories/digest?user_id=jason
"""
import asyncio
import json
import logging
import os
import re
import uuid

import httpx
from routers.journal import CREATED_AT_VALID_TIMESTAMP_SQL

logger = logging.getLogger(__name__)


def _normalize_gemma_base(raw: str) -> str:
    """Base URL for the local llama-server, WITHOUT a trailing /v1.

    Call sites here append `/v1/chat/completions`. But `GEMMA_SERVER_URL` is shared
    with other modules (e.g. zoe_agent) whose convention INCLUDES `/v1` — and the
    live systemd unit sets it that way. Without this strip, this module produces
    `/v1/v1/chat/completions` → 404 and silently breaks extraction/consolidation.
    Normalize so the appends below are correct regardless of whether the env value
    ends in /v1.
    """
    base = (raw or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")
    return base or "http://127.0.0.1:11434"


_GEMMA_URL = _normalize_gemma_base(os.environ.get("GEMMA_SERVER_URL", "http://127.0.0.1:11434"))
_ZOE_TIMEZONE = os.environ.get("ZOE_TIMEZONE", "Australia/Perth")
_GUEST_USERS = ("guest", "anonymous", "voice-guest", "voice-daemon", "")

_LINK_RESOLVER_TRUTHY = frozenset({"1", "true", "yes", "on"})


def memory_link_resolver_enabled() -> bool:
    """Cheap per-call read of the idle person-link resolver flag (default OFF).

    When ON, the dream cycle re-links ``person_pending`` facts to a real
    ``people.id`` once the contact exists (see ``_resolve_pending_person_links``).
    A true no-op while OFF — the resolver returns before any store/DB access.
    """
    return (
        os.environ.get("ZOE_MEMORY_LINK_RESOLVER_ENABLED", "").strip().lower()
        in _LINK_RESOLVER_TRUTHY
    )


def _name_from_pending_slug(entity_id: str) -> str:
    """Human name from a ``slug:<body>`` entity_id (person_extractor convention).

    ``"slug:mary_jane"`` → ``"mary jane"``. A bare value (no ``slug:`` prefix) is
    de-underscored as-is so a legacy pending id still resolves.
    """
    s = (entity_id or "").strip()
    if s.startswith("slug:"):
        s = s[len("slug:"):]
    return s.replace("_", " ").strip()


async def _resolve_pending_person_links(user_id: str, db=None) -> dict:
    """Idle pass: re-link ``person_pending`` facts to a real ``people.id``.

    Scans this user's ``person_pending`` memories, resolves the slug'd name via
    ``person_extractor._resolve_person_uuid``, and — when a contact now exists —
    rewrites ``entity_id`` → the ``people.id`` and flips ``entity_type``
    ``person_pending`` → ``person``. The rewrite is a **metadata-only**
    ``col.update`` (no document → Chroma does NOT re-embed), the same path
    ``tick_access`` uses.

    Default-OFF: a true no-op (no DB open, no store scan) unless
    ``ZOE_MEMORY_LINK_RESOLVER_ENABLED`` is set. Runs only in the idle dream
    cycle, so it never touches the turn path.
    """
    result = {"user_id": user_id, "scanned": 0, "relinked": 0}
    if not memory_link_resolver_enabled():
        return result

    from person_extractor import _ensure_db
    from memory_extractor import _resolve_unique_person_uuid
    from memory_service import get_memory_service, is_guest_memory_user

    if not user_id or is_guest_memory_user(user_id):
        return result

    _db, opened = await _ensure_db(db)
    if _db is None:
        return result
    try:
        svc = get_memory_service()
        col = svc._collection()
        # Owner + status scoped scan: only THIS user's still-pending person facts.
        results = col.get(
            where={"$and": [
                {"user_id": {"$eq": user_id}},
                {"entity_type": {"$eq": "person_pending"}},
            ]},
            include=["metadatas"],
        )
        ids = results.get("ids") or []
        metas = results.get("metadatas") or []
        for mem_id, meta in zip(ids, metas):
            meta = dict(meta) if meta else {}
            result["scanned"] += 1
            name = _name_from_pending_slug(str(meta.get("entity_id") or ""))
            if not name:
                continue
            # Unambiguous match only — never guess "Sam" onto "Samantha" and
            # permanently rewrite the fact to the wrong person.
            person_uuid = await _resolve_unique_person_uuid(name, user_id, _db)
            if not person_uuid:
                continue
            # Relink through the memory service so the metadata-only rewrite runs
            # under the SAME per-user lock as tick_access (no lost-update race).
            if await svc.relink_entity(user_id, mem_id, "person", str(person_uuid)):
                result["relinked"] += 1
                logger.info(
                    "link_resolver: relinked %s -> person %s user=%s",
                    mem_id, person_uuid, user_id,
                )
    except Exception as exc:
        logger.warning("link_resolver: scan failed user=%s: %s", user_id, exc)
    finally:
        if opened and _db is not None:
            try:
                await _db.close()
            except Exception:
                pass
    return result


def _passes_quality_gate(text: str) -> bool:
    """Quality gate for the digest/synthesis LLM passes, which occasionally emit
    non-facts ("The provided facts illustrate…", transcript echoes) that then
    pollute recall. Mirrors the gate the conversational writers already apply.
    Degrades to accept if the gate is unavailable so we never silently drop a
    real fact."""
    try:
        from memory_quality import is_storable_fact
        ok, _reason = is_storable_fact(text)
        return ok
    except Exception:
        return True


def _message_owner_expr() -> str:
    guests = ", ".join("'" + user.replace("'", "''") + "'" for user in _GUEST_USERS)
    # chat_messages.metadata is TEXT and legacy rows may contain non-JSON.
    # Extract the simple {"user_id": "..."} field without a jsonb cast so one
    # malformed row cannot fail discovery for every user.
    metadata_user = (
        "CASE WHEN cm.metadata ~ '^\\s*\\{' "
        "THEN substring(cm.metadata from '\"user_id\"\\s*:\\s*\"([^\"]+)\"') "
        "ELSE NULL END"
    )
    return (
        "CASE "
        f"WHEN COALESCE({metadata_user}, '') NOT IN ({guests}) "
        f"THEN {metadata_user} "
        f"WHEN COALESCE(cs.user_id, '') NOT IN ({guests}) "
        "THEN cs.user_id "
        "ELSE NULL END"
    )


def _message_owner_users_sql(*, today_only: bool) -> str:
    owner_expr = _message_owner_expr()
    date_clause = ""
    if today_only:
        # Casts are load-bearing. Through the asyncpg positional-compat layer, an
        # uncast timezone placeholder binds as "unknown" and the timestamp operand
        # collapses to text, so overload resolution fails ("function
        # pg_catalog.timezone(unknown, text) does not exist") and the whole
        # discovery query errors — silently zeroing out active-user detection.
        # The ::text zone cast + now()::timestamptz pin the timezone(text,
        # timestamptz) overload. (Keep this SQL free of literal question marks,
        # including in comments — the compat layer counts them as placeholders.)
        date_clause = """
          AND (cm.created_at::timestamptz AT TIME ZONE ?::text)::date =
              (now()::timestamptz AT TIME ZONE ?::text)::date
        """
    return f"""
        SELECT DISTINCT owner.user_id
        FROM (
            SELECT {owner_expr} AS user_id
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cm.role = 'user'
            {date_clause}
        ) owner
        WHERE owner.user_id IS NOT NULL
        """

_EXTRACTION_PROMPT = """\
You are extracting personal facts from a chat transcript. Only extract facts the user explicitly stated about themselves, their family, preferences, or life. Do NOT infer, assume, or add anything not stated directly.

Return ONLY a JSON array (no preamble, no explanation). Each item has:
  "type": one of "profile" | "preference" | "habit" | "event" | "relationship" | "health"
  "fact": a single concise sentence (max 150 chars) in third-person (e.g. "User is 44 years old")

If nothing personal was stated, return: []

Chat messages (user turns only):
{chat_text}
"""


# For contradiction checks we want a single-token yes/no so the decision is
# cheap and unambiguous. The schema lets us also capture *which* existing fact
# is contradicted when multiple candidates are evaluated at once.
# Temperature=0 removes jitter.
_CONTRADICTION_PROMPT = """\
You are judging whether a NEW fact contradicts an EXISTING fact about the same person.

Two facts CONTRADICT only if they cannot both be true at the same time about the same subject
(e.g. "User lives in Sydney" vs "User lives in Melbourne" — contradiction;
 "User likes coffee" vs "User likes tea" — NOT a contradiction, both can be true).

NEW fact:
{new_fact}

EXISTING fact:
{existing_fact}

Return ONLY one JSON object, nothing else:
  {{"contradicts": true|false, "reason": "<=15 words"}}
"""


# ── Emotional memory extraction ───────────────────────────────────────────────
# Identifies emotionally significant moments from conversations. Stored with
# memory_type="emotional_moment" so the agent can surface them as relationship
# context — the emotional arc of the user's life, not just facts about it.
_EMOTIONAL_EXTRACTION_PROMPT = """\
You are identifying emotionally significant moments from a conversation.

Look for: strong emotions the person expressed, important personal news they shared, \
moments where the interaction carried real emotional weight.

Return ONLY a JSON array (or [] if nothing qualifies):
  "moment": what happened, written in third-person, max 150 chars
  "emotion": one of joy | excitement | anxiety | sadness | frustration | pride | relief | love | grief | other
  "significance": integer 1-3  (1=minor, 2=notable, 3=major life moment)

Only include moments with significance >= 2. If nothing qualifies, return [].

Conversation (user turns only):
{chat_text}
"""


_TURN_EXTRACTION_PROMPT = """\
You are extracting personal facts from a single chat exchange. Only extract facts the user explicitly stated about themselves, their family, pets, preferences, or life. Do NOT infer or assume anything not stated directly.

Return ONLY a JSON array (no preamble). Each item:
  "type": one of "profile" | "preference" | "habit" | "event" | "relationship" | "health" | "pet"
  "fact": a single concise sentence in third-person (max 120 chars, e.g. "User's dog is named Teddy")

If nothing personal was stated, return: []

User said: {user_message}
"""


async def run_turn_digest(
    user_id: str,
    user_message: str,
    assistant_response: str = "",
    *,
    session_id: str | None = None,
    source: str = "turn_digest",
) -> dict:
    """LLM fact extraction on a single conversation exchange.

    Runs in the background after every chat/voice turn. Catches nuanced facts
    that regex patterns miss without waiting for the nightly batch digest.

    Returns a summary dict: {"new": N, "skipped_duplicates": N, "error": ...}
    """
    result: dict = {"user_id": user_id, "new": 0, "skipped_duplicates": 0, "skipped_low_quality": 0}

    if not user_message or len(user_message.split()) < 4:
        return result
    # Skip purely procedural messages that can't contain personal facts.
    _skip_starts = ("what is", "what are", "how do", "explain", "tell me about",
                    "what time", "what's the", "search for", "play ", "set a timer",
                    "set timer", "remind me to", "add to my", "what's")
    msg_lower = user_message.lower().strip()
    if any(msg_lower.startswith(s) for s in _skip_starts):
        return result
    # Third-person pronoun subject ("she is allergic to nuts", "he's a doctor"):
    # this single-turn prompt has no antecedent context, so the LLM can only guess
    # who the fact is about — observed misattributing a friend's allergy to THE
    # USER ("The user is allergic to nuts"). The deterministic coreference path
    # (memory_extractor._pronoun_fact_candidates + session-history anchoring) owns
    # these turns; skip the context-free LLM digest rather than let it guess.
    # Possessive starts included ("her birthday is actually…" produced a guessed
    # "User's birthday is March 25." — QA review F2 evidence).
    if re.match(r"^(?:and\s+|oh[,\s]+|btw[,\s]+)?(?:she|he|they|her|his|their)\b", msg_lower):
        result["skipped_reason"] = "pronoun_subject_no_context"
        return result

    try:
        from memory_service import get_memory_service, MemoryServiceError  # type: ignore[import]
        svc = get_memory_service()

        prompt = _TURN_EXTRACTION_PROMPT.format(user_message=user_message[:600])
        payload = {
            "model": os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
            "messages": [
                {"role": "system", "content": "You are a precise fact extractor. Return ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 256,
            "temperature": 0.1,
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(f"{_GEMMA_URL}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                raw = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.debug("turn_digest: LLM call failed for %s: %s", user_id, exc)
            return result

        # Parse JSON array from response
        try:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start == -1 or end == 0:
                return result
            facts = json.loads(raw[start:end])
            if not isinstance(facts, list):
                return result
        except (json.JSONDecodeError, ValueError):
            return result

        if not facts:
            return result

        # Light dedup: load existing facts as a text blob for word-overlap check
        try:
            from zoe_agent import _mempalace_load_user_facts  # type: ignore[import]
            existing_text = await _mempalace_load_user_facts(user_id, limit=50)
            existing_lower = existing_text.lower()
        except Exception:
            existing_lower = ""

        import hashlib as _hashlib
        base_turn_id = _hashlib.sha1(user_message.encode("utf-8", "ignore")).hexdigest()[:16]

        for idx, item in enumerate(facts):
            fact = (item.get("fact") or "").strip()
            if not fact or len(fact) < 8:
                continue
            # Word-overlap dedup (same as nightly digest)
            fact_words = set(fact.lower().split())
            overlap = sum(1 for w in fact_words if w in existing_lower) / max(len(fact_words), 1)
            if overlap > 0.7:
                result["skipped_duplicates"] += 1
                continue
            if not _passes_quality_gate(fact):
                result["skipped_low_quality"] += 1
                logger.debug("run_turn_digest: dropped non-fact: %r", fact[:60])
                continue
            # Anchor validation: this single-turn LLM guesses "the user" as the
            # relationship anchor when the text doesn't say ("Emily is the wife"
            # → "Emily is the user's wife", live 2026-07-12). Only accept a
            # user-anchored relationship the turn supports ("my <role>").
            try:
                from memory_quality import user_relationship_claim_unsupported
                if user_relationship_claim_unsupported(fact, user_message):
                    result["skipped_low_quality"] += 1
                    logger.info("run_turn_digest: dropped unsupported user-anchored relationship: %r", fact[:70])
                    continue
            except Exception:
                pass
            try:
                ref = await svc.ingest(
                    fact,
                    user_id=user_id,
                    source=source,
                    session_id=session_id,
                    user_turn_id=f"{base_turn_id}-td{idx}",
                    memory_type=item.get("type", "fact"),
                    confidence=0.82,
                    status="approved",
                    tags=["turn_digest", "auto_extract"],
                )
                if ref is not None:
                    result["new"] += 1
                    logger.info("turn_digest: stored for %s: %s", user_id, fact[:80])
            except MemoryServiceError as exc:
                logger.debug("turn_digest: ingest failed for %s: %s", user_id, exc)

    except Exception as exc:
        logger.warning("turn_digest: unexpected error for %s: %s", user_id, exc)
        result["error"] = str(exc)

    if result.get("new", 0) > 0:
        try:
            from zoe_agent import _invalidate_user_facts_cache
            _invalidate_user_facts_cache(user_id)
        except Exception:
            pass

    return result


async def run_memory_digest(user_id: str, db=None) -> dict:
    """Extract facts from today's chat history and write to MemPalace + memory_items.

    Args:
        user_id: The user to run the digest for.
        db:      asyncpg database connection (optional — opens its own if None).

    Returns:
        dict with keys: user_id, extracted, new, skipped_duplicates, error (if any).
    """
    result: dict = {
        "user_id": user_id,
        "extracted": 0,
        "new": 0,
        "skipped_duplicates": 0,
        "superseded": 0,
    }
    try:
        chat_text = await _load_todays_messages(user_id, db)
        if not chat_text or len(chat_text.split()) < 20:
            logger.info("memory_digest: skipping %s — not enough chat activity today", user_id)
            result["skipped_reason"] = "insufficient_activity"
            return result

        from zoe_agent import _mempalace_load_user_facts  # type: ignore[import]
        from memory_service import MemoryServiceError, get_memory_service
        svc = get_memory_service()

        facts = await _extract_facts_with_gemma(chat_text)
        result["extracted"] = len(facts)

        if facts:
            existing_text = await _mempalace_load_user_facts(user_id, limit=100)
            existing_lower = existing_text.lower()

        for item in facts:
            fact = (item.get("fact") or "").strip()
            if not fact or len(fact) < 10:
                continue
            fact_words = set(fact.lower().split())
            overlap_score = sum(1 for w in fact_words if w in existing_lower) / max(len(fact_words), 1)
            if overlap_score > 0.7:
                logger.debug("memory_digest: dedup skip (%.0f%% overlap): %s", overlap_score * 100, fact[:60])
                result["skipped_duplicates"] += 1
                continue

            # Anchor validation BEFORE the contradiction check: that branch can
            # WRITE via review(decision="edit") and would bypass a later gate. A
            # day-level transcript has no turn provenance, so drop EVERY
            # user-anchored relationship fact here — the per-turn digest (which
            # validates against the actual source turn) owns those.
            try:
                from memory_quality import user_relationship_claim_unsupported
                if user_relationship_claim_unsupported(fact, ""):
                    logger.info("memory_digest: dropped user-anchored relationship (no turn provenance in nightly batch): %r", fact[:70])
                    continue
            except Exception:
                pass

            # ── Contradiction check ──────────────────────────────────────
            # Pull the top-3 semantically similar existing facts and ask
            # the LLM whether any of them contradict the new one. If yes,
            # supersede the old memory via review(decision="edit"), which
            # writes the new fact and links it to the old row via
            # supersedes_id / superseded_by_id.
            superseded_any = False
            try:
                related = await svc.search(fact, user_id=user_id, limit=3, timeout_s=1.5)
            except Exception as exc:
                logger.debug("memory_digest: contradiction-search failed: %s", exc)
                related = []
            for candidate in related:
                existing_fact = (candidate.text or "").strip()
                if not existing_fact or existing_fact.lower() == fact.lower():
                    continue
                if not await _is_contradiction(fact, existing_fact):
                    continue
                try:
                    new_ref = await svc.review(
                        candidate.id,
                        decision="edit",
                        edits=fact,
                        actor="digest",
                        note="digest contradiction: superseded by newer turn",
                    )
                except MemoryServiceError as exc:
                    logger.warning(
                        "memory_digest: supersede failed for %s: %s", user_id, exc
                    )
                    continue
                if new_ref is not None:
                    superseded_any = True
                    result["superseded"] += 1
                    logger.info(
                        "memory_digest: superseded %s -> %s user=%s",
                        candidate.id, new_ref.id, user_id,
                    )
                    # A single supersede handles the new fact — skip the
                    # plain ingest below so we don't double-write.
                    break
            if superseded_any:
                continue

            tags = ["digest", item.get("type", "unknown")]
            if not _passes_quality_gate(fact):
                continue
            try:
                ref = await svc.ingest(
                    fact,
                    user_id=user_id,
                    source="digest",
                    memory_type=item.get("type", "fact"),
                    confidence=0.8,
                    status="approved",
                    tags=tags,
                )
            except MemoryServiceError as exc:
                logger.warning("memory_digest: ingest failed for %s: %s", user_id, exc)
                continue
            if ref is not None:
                result["new"] += 1
                logger.info("memory_digest: stored for %s: %s", user_id, fact[:80])

        # ── Emotional memory pass ──────────────────────────────────────────
        # Runs after fact extraction — separate LLM call that looks for
        # emotionally significant moments rather than neutral facts.
        try:
            emotional_new = await _emotional_memory_pass(user_id, chat_text, svc)
            result["emotional_new"] = emotional_new
        except Exception as exc:
            logger.debug("memory_digest: emotional pass failed (non-fatal) user=%s: %s", user_id, exc)

    except Exception as exc:
        logger.error("memory_digest: failed for %s: %s", user_id, exc, exc_info=True)
        result["error"] = str(exc)
    return result


async def _emotional_memory_pass(user_id: str, chat_text: str, svc) -> int:
    """Extract emotionally significant moments from today's chat and store them.

    Returns the number of new emotional memories stored.
    """
    from memory_service import MemoryServiceError  # type: ignore[import]

    prompt = _EMOTIONAL_EXTRACTION_PROMPT.format(chat_text=chat_text[:3000])
    payload = {
        "model": os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
        "messages": [
            {"role": "system", "content": "You are an empathetic listener. Return ONLY valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 256,
        "temperature": 0.2,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{_GEMMA_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            raw = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "[]").strip()
    except Exception as exc:
        logger.debug("emotional_pass: LLM call failed: %s", exc)
        return 0

    # Parse JSON
    try:
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start == -1 or end == 0:
            return 0
        moments = json.loads(raw[start:end])
        if not isinstance(moments, list):
            return 0
    except (json.JSONDecodeError, ValueError):
        return 0

    stored = 0
    for item in moments:
        moment = (item.get("moment") or "").strip()
        emotion = (item.get("emotion") or "other").strip().lower()
        significance = int(item.get("significance", 1))
        if not moment or significance < 2:
            continue
        # Same no-turn-provenance rule as the nightly fact loop: a day-level
        # moment like "User's wife was excited about the trip" can misattribute
        # a relationship the transcript never anchored — drop user-anchored
        # relationship phrasings here too.
        try:
            from memory_quality import user_relationship_claim_unsupported
            if user_relationship_claim_unsupported(moment, ""):
                logger.info("memory_digest: dropped user-anchored relationship in emotional moment: %r", moment[:70])
                continue
        except Exception:
            pass
        try:
            ref = await svc.ingest(
                moment,
                user_id=user_id,
                source="digest",
                memory_type="emotional_moment",
                confidence=0.9,
                status="approved",
                tags=["emotional", emotion],
            )
            if ref is not None:
                stored += 1
                logger.info("emotional_pass: stored user=%s [%s] %s", user_id, emotion, moment[:60])
        except MemoryServiceError as exc:
            logger.debug("emotional_pass: ingest failed: %s", exc)
    return stored


async def _load_todays_messages(user_id: str, db=None) -> str:
    """Load today's user-turn messages using per-message metadata ownership."""
    owner_expr = _message_owner_expr()
    sql = """
            SELECT cm.content
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE """ + owner_expr + """ = ?
              AND cm.role = 'user'
              -- The ::text / ::timestamptz casts are required so the asyncpg
              -- positional-compat layer resolves timezone(text, timestamptz);
              -- without them the query errors and silently drops every message.
              -- (No literal question marks in this SQL — the compat layer would
              -- miscount them as bind placeholders.)
              AND (cm.created_at::timestamptz AT TIME ZONE ?::text)::date =
                  (now()::timestamptz AT TIME ZONE ?::text)::date
            ORDER BY cm.created_at ASC
            LIMIT 200
            """
    params = (user_id, _ZOE_TIMEZONE, _ZOE_TIMEZONE)
    try:
        from db_pool import get_db_ctx  # type: ignore[import]
        if db is not None:
            rows = await (await db.execute(sql, params)).fetchall()
        else:
            # Self-acquire via the context manager when no connection is passed.
            # The bare `async for db in get_db(): break` form leaves the generator
            # suspended, so the connection is closed mid-query — which would make
            # every per-user digest skip after listing (Greptile P1 on #860).
            async with get_db_ctx() as _db:
                rows = await (await _db.execute(sql, params)).fetchall()
        if not rows:
            return ""
        lines = [row[0] for row in rows if row[0]]
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("memory_digest: could not load messages for %s: %s", user_id, exc)
        return ""


async def _extract_facts_with_gemma(chat_text: str) -> list[dict]:
    """Send chat transcript to the LLM and parse the JSON fact list."""
    if len(chat_text) > 3000:
        logger.warning(
            "memory_digest: transcript truncated to 3000 chars for fact "
            "extraction; dropped %d tail chars (may lose late-conversation facts)",
            len(chat_text) - 3000,
        )
    prompt = _EXTRACTION_PROMPT.format(chat_text=chat_text[:3000])
    payload = {
        "model": os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
        "messages": [
            {"role": "system", "content": "You are a precise fact extractor. Return ONLY valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 512,
        "temperature": 0.1,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(f"{_GEMMA_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            raw = resp.json()
            text = raw["choices"][0]["message"]["content"].strip()
            # Extract JSON array (model may add preamble despite instructions)
            start = text.find("[")
            end = text.rfind("]") + 1
            if start == -1 or end == 0:
                logger.warning("memory_digest: LLM returned no JSON array: %s", text[:200])
                return []
            return json.loads(text[start:end])
    except json.JSONDecodeError as je:
        logger.warning("memory_digest: JSON parse error: %s", je)
        return []
    except Exception as exc:
        logger.warning("memory_digest: LLM call failed: %s", exc)
        return []


async def _is_contradiction(new_fact: str, existing_fact: str) -> bool:
    """Ask the LLM whether a new fact contradicts an existing one.

    Fails **closed** (returns False) on any error — we prefer a
    duplicate over losing a real fact to a flaky LLM call.
    """
    if not new_fact or not existing_fact:
        return False
    prompt = _CONTRADICTION_PROMPT.format(
        new_fact=new_fact.strip(),
        existing_fact=existing_fact.strip(),
    )
    payload = {
        "model": os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
        "messages": [
            {"role": "system", "content": "You are a strict fact-contradiction judge. Return ONLY the JSON object."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 80,
        "temperature": 0.0,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{_GEMMA_URL}/v1/chat/completions", json=payload)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return False
        parsed = json.loads(text[start:end])
        return bool(parsed.get("contradicts"))
    except Exception as exc:
        logger.debug("memory_digest: contradiction judge failed: %s", exc)
        return False


# ── Weekly consolidation ─────────────────────────────────────────────────────
#
# Runs once per week (default: Sunday 04:00). Goals:
#   1. **Merge near-duplicates**: memories whose text overlap ≥ 0.85
#      collapse into one — keep the highest-confidence row, mark the rest
#      as `superseded_by_id=keeper`. No LLM call needed for this step;
#      pure word-overlap is cheap and safe because anything this close
#      already lost information at capture time.
#   2. **Resolve contradictions**: for each pair in the top-K most similar
#      approved rows, ask the LLM if they contradict; if yes, keep the
#      newest and supersede the other.
#   3. **Soft-archive low-score stale rows** via
#      `MemoryService.sweep_soft_archive()`.
#
# The pass is idempotent: running it twice in a row is a no-op because
# merged / superseded rows already have ``status != 'approved'`` and are
# excluded from subsequent scans.


def _text_overlap(a: str, b: str) -> float:
    """Containment overlap — symmetric inter/min(|A|,|B|).

    Jaccard penalises one-sided paraphrases ("user loves italian cuisine"
    vs "the user really loves italian cuisine" is only 0.67) even when
    the shorter sentence is fully covered by the longer one. For
    duplicate-detection we want the stronger "is one a subset of the
    other" signal, so we divide by min(|A|,|B|). Filler words (len ≤ 2)
    are ignored to keep stopwords from inflating similarity.
    """
    wa = {w for w in a.lower().split() if len(w) > 2}
    wb = {w for w in b.lower().split() if len(w) > 2}
    if not wa or not wb:
        return 0.0
    inter = wa & wb
    return len(inter) / max(min(len(wa), len(wb)), 1)


async def _merge_near_duplicates(svc, user_id: str) -> int:
    """Collapse near-duplicate approved rows. Returns merge count."""
    approved = await svc.list_by_status(
        user_id=user_id, status="approved", limit=10_000
    )
    if len(approved) < 2:
        return 0
    # Pin the freshest/highest-confidence row of each cluster as the keeper.
    approved.sort(
        key=lambda r: (
            -float(r.metadata.get("confidence", 0.7) or 0.7),
            r.metadata.get("added_at", ""),
        ),
        reverse=False,
    )
    keepers: list = []
    merged = 0
    for ref in approved:
        text = (ref.text or "").strip()
        if not text:
            continue
        matched = False
        for keeper in keepers:
            if _text_overlap(text, keeper.text) >= 0.85:
                # Supersede the weaker row with the keeper's existing id.
                try:
                    await svc.review(
                        ref.id,
                        decision="edit",
                        edits=keeper.text,
                        actor="consolidation",
                        note="weekly: merged near-duplicate",
                    )
                    merged += 1
                except Exception as exc:
                    logger.debug(
                        "consolidation: merge skipped id=%s: %s", ref.id, exc
                    )
                matched = True
                break
        if not matched:
            keepers.append(ref)
    return merged


async def _resolve_contradictions(svc, user_id: str, max_pairs: int = 50) -> int:
    """Walk pairs of high-similarity approved rows; supersede older if contradicted."""
    approved = await svc.list_by_status(
        user_id=user_id, status="approved", limit=200
    )
    if len(approved) < 2:
        return 0
    resolved = 0
    pairs_checked = 0
    # Sort newest-first so that on contradiction we can always supersede
    # the older row and keep the newer one.
    approved.sort(key=lambda r: r.metadata.get("added_at", ""), reverse=True)
    for i, newer in enumerate(approved):
        # Re-read newer's status in case an earlier iteration superseded
        # it already.
        refreshed = await svc.get(newer.id)
        if refreshed is None or refreshed.metadata.get("status") != "approved":
            continue
        # Only compare against older rows (higher indices) that share
        # meaningful lexical overlap — cheap filter to avoid N² LLM calls.
        for older in approved[i + 1 :]:
            if pairs_checked >= max_pairs:
                return resolved
            if _text_overlap(newer.text, older.text) < 0.25:
                continue
            older_current = await svc.get(older.id)
            if older_current is None or older_current.metadata.get("status") != "approved":
                continue
            pairs_checked += 1
            if not await _is_contradiction(newer.text, older.text):
                continue
            try:
                await svc.review(
                    older.id,
                    decision="edit",
                    edits=newer.text,
                    actor="consolidation",
                    note="weekly: contradicted by newer fact",
                )
                resolved += 1
            except Exception as exc:
                logger.debug(
                    "consolidation: supersede skipped id=%s: %s", older.id, exc
                )
    return resolved


async def run_weekly_consolidation(user_id: str) -> dict:
    """Per-user Sunday pass: merge duplicates, resolve contradictions, soft-archive.

    Returns a summary dict safe to log or surface via the admin UI.
    Never raises: each step is wrapped so one failure doesn't abort
    downstream work.
    """
    from memory_service import get_memory_service
    svc = get_memory_service()
    summary = {
        "user_id": user_id,
        "merged": 0,
        "resolved_contradictions": 0,
        "archived": 0,
    }
    try:
        summary["merged"] = await _merge_near_duplicates(svc, user_id)
    except Exception as exc:
        logger.warning("consolidation: merge failed user=%s: %s", user_id, exc)
    try:
        summary["resolved_contradictions"] = await _resolve_contradictions(svc, user_id)
    except Exception as exc:
        logger.warning("consolidation: contradiction pass failed user=%s: %s", user_id, exc)
    try:
        archived_ids = await svc.sweep_soft_archive(user_id=user_id, actor="consolidation")
        summary["archived"] = len(archived_ids)
    except Exception as exc:
        logger.warning("consolidation: sweep failed user=%s: %s", user_id, exc)
    logger.info("consolidation: %s", summary)
    return summary


async def _list_user_ids(sql: str, params: tuple = (), *, db=None) -> list[str]:
    """List user ids for a batch pass (`*_for_all` listing step).

    Uses the supplied connection when given, else a short-lived pooled acquire
    via ``get_db_ctx()`` for the listing only. Never use the bare
    ``async for db in get_db(): break`` form — it leaves the generator
    suspended at the yield, so the connection is closed out from under the
    query ("connection was closed in the middle of operation"). Materialize
    the rows before release.
    """
    from db_pool import get_db_ctx  # type: ignore[import]
    if db is not None:
        rows = await (await db.execute(sql, params)).fetchall()
    else:
        async with get_db_ctx() as _db:
            rows = await (await _db.execute(sql, params)).fetchall()
    return [row[0] for row in rows if row[0]]


async def run_weekly_consolidation_for_all(db=None) -> list[dict]:
    """Run weekly consolidation for every user who has any approved memory."""
    from memory_service import get_memory_service
    svc = get_memory_service()
    try:
        user_ids = await svc.list_users()  # if this helper exists
    except AttributeError:
        # Fall back to chat-sessions table so we never silently process
        # zero users when MemoryService hasn't exposed a list helper.
        try:
            user_ids = await _list_user_ids(
                _message_owner_users_sql(today_only=False), db=db
            )
        except Exception as exc:
            logger.error("consolidation: could not list users: %s", exc)
            return []
    results = []
    for uid in user_ids:
        results.append(await run_weekly_consolidation(uid))
    return results


async def run_digest_for_all_active_users(db=None) -> list[dict]:
    """Run memory digest for all users who had chat activity today."""
    results = []
    try:
        user_ids = await _list_user_ids(
            _message_owner_users_sql(today_only=True),
            (_ZOE_TIMEZONE, _ZOE_TIMEZONE),
            db=db,
        )
    except Exception as exc:
        logger.error("memory_digest: could not list active users: %s", exc)
        return []

    for uid in user_ids:
        result = await run_memory_digest(uid, db=db)
        results.append(result)
        logger.info("memory_digest: %s", result)
    return results


# ═══════════════════════════════════════════════════════════════════════════
# DREAMING MEMORY — arXiv:2604.20943
# Three-phase nightly/weekly memory reinforcement system:
#   Phase 1 (REM Reinforce)  — run nightly after fact extraction
#   Phase 2 (Deep Sleep)     — run weekly, promotes/archives pending memories
#   Phase 3 (Synthesis)      — run weekly, clusters and synthesizes patterns
# ═══════════════════════════════════════════════════════════════════════════

_CONCEPT_EXTRACTION_PROMPT = """\
Given the following memory fact, extract 1-5 concept tags (entity types or topics).
Return ONLY a JSON array of short lowercase strings, e.g. ["food", "preference", "location"].
Fact: {fact}
"""


async def _extract_concept_tags(fact: str) -> list[str]:
    """Use Gemma to extract concept tags from a fact. Returns [] on failure."""
    prompt = _CONCEPT_EXTRACTION_PROMPT.format(fact=fact[:300])
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_GEMMA_URL}/v1/chat/completions",
                json={
                    "model": os.environ.get("ZOE_LLM_MODEL", "gemma"),
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 60,
                    "temperature": 0.1,
                },
                timeout=10.0,
            )
        text = resp.json()["choices"][0]["message"]["content"].strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            tags = json.loads(text[start:end])
            return [str(t).lower().strip()[:30] for t in tags if t][:5]
    except Exception as exc:
        logger.debug("concept extraction failed: %s", exc)
    return []


async def _rem_reinforce_pass(user_id: str) -> dict:
    """REM pass: for each new memory ingested tonight, strengthen related existing memories.

    Algorithm:
    1. Fetch tonight's new memories (added_at = today, consolidation_count = 0)
    2. For each, semantic search for top-5 neighbours
    3. Bump access_count on neighbours (new fact reinforces existing knowledge)
    4. Write related_ids on both the new memory and its neighbours
    5. Extract and store concept_tags if not already set
    """
    from memory_service import get_memory_service
    import datetime, hashlib

    svc = get_memory_service()
    today = datetime.datetime.utcnow().date().isoformat()
    linked = 0
    tagged = 0

    try:
        col = svc._collection()
        # ChromaDB $gte only supports int/float — filter by user_id only, then
        # post-filter by added_at date in Python (ISO strings compare correctly
        # lexicographically for same-length prefix matching).
        results = col.get(
            where={"user_id": {"$eq": user_id}},
            include=["documents", "metadatas"],
        )
        # Keep only memories added today (today = "YYYY-MM-DD")
        raw_ids   = results.get("ids")   or []
        raw_docs  = results.get("documents") or []
        raw_metas = results.get("metadatas") or []
        today_ids, today_docs, today_metas = [], [], []
        for _id, _doc, _meta in zip(raw_ids, raw_docs, raw_metas):
            at = (_meta or {}).get("added_at", "") or ""
            if at.startswith(today):
                today_ids.append(_id)
                today_docs.append(_doc)
                today_metas.append(_meta)
        ids   = today_ids
        docs  = today_docs
        metas = today_metas

        for mem_id, doc, meta in zip(ids, docs, metas):
            meta = dict(meta) if meta else {}

            # Skip if already processed
            if int(meta.get("consolidation_count", 0) or 0) > 0:
                continue

            # Extract concept tags if missing
            if not meta.get("concept_tags"):
                tags = await _extract_concept_tags(doc)
                if tags:
                    meta["concept_tags"] = ",".join(tags)
                    tagged += 1

            # Semantic search for neighbours
            neighbours = await svc.search(doc, user_id=user_id, limit=6)
            # Exclude self
            neighbour_ids = [n.id for n in neighbours if n.id != mem_id][:5]

            if neighbour_ids:
                # Bump access on neighbours (reinforcement)
                await svc.tick_access(user_id, neighbour_ids)

                # Write related_ids on new memory
                existing_related = set((meta.get("related_ids") or "").split(","))
                existing_related.update(neighbour_ids)
                meta["related_ids"] = ",".join(i for i in existing_related if i)
                linked += 1

                # Write related_ids on neighbours (bidirectional)
                nb_result = col.get(ids=neighbour_ids, include=["metadatas", "documents"])
                nb_ids = nb_result.get("ids") or []
                nb_docs = nb_result.get("documents") or []
                nb_metas = nb_result.get("metadatas") or []
                new_nb_metas = []
                for nm in nb_metas:
                    nm = dict(nm) if nm else {}
                    nb_related = set((nm.get("related_ids") or "").split(","))
                    nb_related.add(mem_id)
                    nm["related_ids"] = ",".join(i for i in nb_related if i)
                    new_nb_metas.append(nm)
                if nb_ids:
                    col.upsert(ids=nb_ids, documents=nb_docs, metadatas=new_nb_metas)

            # Mark as REM-processed
            meta["consolidation_count"] = int(meta.get("consolidation_count", 0) or 0) + 1
            col.upsert(ids=[mem_id], documents=[doc], metadatas=[meta])

    except Exception as exc:
        logger.warning("REM reinforce pass failed user=%s: %s", user_id, exc)

    summary = {"user_id": user_id, "linked": linked, "tagged": tagged}
    logger.info("dreaming/rem: %s", summary)
    return summary


def _promotion_score(meta: dict) -> float:
    """6-signal weighted promotion score for deep sleep gate.

    Returns a float in [0, 1]. Score >= 0.8 AND unique_query_count >= 3 → promote.
    """
    import datetime, math

    # Relevance proxy: confidence (0.30 weight)
    relevance = float(meta.get("confidence", 0.5) or 0.5)

    # Frequency: normalise access_count (0.24 weight) — cap at 50 for normalisation
    freq_raw = int(meta.get("access_count", 0) or 0)
    frequency = min(freq_raw / 50.0, 1.0)

    # Query diversity (0.15 weight) — cap at 10
    uqc = int(meta.get("unique_query_count", 0) or 0)
    diversity = min(uqc / 10.0, 1.0)

    # Recency: decay from last_accessed (0.15 weight)
    try:
        last = meta.get("last_accessed") or meta.get("added_at") or ""
        dt = datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))
        days_ago = (datetime.datetime.now(datetime.timezone.utc) - dt).days
        recency = math.exp(-days_ago / 30.0)  # e-folding 30 days
    except Exception:
        recency = 0.5

    # Consolidation depth (0.10 weight) — cap at 5
    consol = int(meta.get("consolidation_count", 0) or 0)
    consolidation = min(consol / 5.0, 1.0)

    # Conceptual richness (0.06 weight)
    tags = [t for t in (meta.get("concept_tags") or "").split(",") if t]
    richness = min(len(tags) / 5.0, 1.0)

    score = (
        0.30 * relevance
        + 0.24 * frequency
        + 0.15 * diversity
        + 0.15 * recency
        + 0.10 * consolidation
        + 0.06 * richness
    )
    return round(score, 4)


async def _deep_sleep_pass(user_id: str) -> dict:
    """Deep sleep pass: promote high-signal pending memories; archive stale ones.

    Runs once per week (Sunday nightly). Replaces the blunt auto-approve in
    run_weekly_consolidation with a 6-signal gate.

    Gate: score >= 0.8 AND unique_query_count >= 3 → pending → approved
    Stale: pending for 14+ days without qualifying → archived
    """
    from memory_service import get_memory_service
    import datetime

    svc = get_memory_service()
    col = svc._collection()
    promoted = 0
    archived = 0
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=14)).isoformat() + "Z"

    try:
        results = col.get(
            where={"$and": [{"user_id": {"$eq": user_id}}, {"status": {"$eq": "pending"}}]},
            include=["documents", "metadatas"],
        )
        ids = results.get("ids") or []
        docs = results.get("documents") or []
        metas = results.get("metadatas") or []

        for mem_id, doc, meta in zip(ids, docs, metas):
            meta = dict(meta) if meta else {}
            score = _promotion_score(meta)
            uqc = int(meta.get("unique_query_count", 0) or 0)
            added_at = meta.get("added_at") or ""

            if score >= 0.8 and uqc >= 3:
                meta["status"] = "approved"
                meta["consolidation_count"] = int(meta.get("consolidation_count", 0) or 0) + 1
                col.upsert(ids=[mem_id], documents=[doc], metadatas=[meta])
                promoted += 1
            elif added_at and added_at < cutoff:
                meta["status"] = "archived"
                meta["consolidation_count"] = int(meta.get("consolidation_count", 0) or 0) + 1
                col.upsert(ids=[mem_id], documents=[doc], metadatas=[meta])
                archived += 1

    except Exception as exc:
        logger.warning("deep sleep pass failed user=%s: %s", user_id, exc)

    summary = {"user_id": user_id, "promoted": promoted, "archived": archived}
    logger.info("dreaming/deep_sleep: %s", summary)
    return summary


_SYNTHESIS_PROMPT = """\
The following {n} memory facts all share the topic "{tag}".
Synthesize a single higher-order pattern or insight from them in one clear sentence.
Do NOT use names or personal identifiers. Output ONLY the synthesized fact, nothing else.

Facts:
{facts}
"""


async def _synthesis_pass(user_id: str) -> dict:
    """Synthesis pass: cluster approved memories by concept tag; synthesize patterns.

    For clusters of 5+ memories sharing the same top concept tag, prompt Gemma
    to produce one higher-order insight. Stored with source="synthesis".
    """
    from memory_service import get_memory_service, MemoryServiceError

    svc = get_memory_service()
    col = svc._collection()
    synthesized = 0

    try:
        results = col.get(
            where={"$and": [
                {"user_id": {"$eq": user_id}},
                {"status": {"$eq": "approved"}},
                {"source": {"$ne": "synthesis"}},
            ]},
            include=["documents", "metadatas"],
        )
        ids = results.get("ids") or []
        docs = results.get("documents") or []
        metas = results.get("metadatas") or []

        # Build clusters by top concept tag
        from collections import defaultdict
        clusters: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for mem_id, doc, meta in zip(ids, docs, metas):
            meta = dict(meta) if meta else {}
            tags = [t.strip() for t in (meta.get("concept_tags") or "").split(",") if t.strip()]
            if tags:
                clusters[tags[0]].append((mem_id, doc))

        for tag, members in clusters.items():
            if len(members) < 5:
                continue
            # Take the 10 most relevant
            sample = members[:10]
            facts_text = "\n".join(f"- {doc}" for _, doc in sample)
            prompt = _SYNTHESIS_PROMPT.format(n=len(sample), tag=tag, facts=facts_text)

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(
                        f"{_GEMMA_URL}/v1/chat/completions",
                        json={
                            "model": os.environ.get("ZOE_LLM_MODEL", "gemma"),
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": 120,
                            "temperature": 0.3,
                        },
                    )
                synthesis_text = resp.json()["choices"][0]["message"]["content"].strip()
                if len(synthesis_text) < 10:
                    continue
                # The synthesis LLM often returns meta-commentary ("The provided
                # facts illustrate…") instead of a stored-shaped insight — gate it.
                if not _passes_quality_gate(synthesis_text):
                    logger.debug("synthesis: dropped non-fact insight: %r", synthesis_text[:60])
                    continue

                ref = await svc.ingest(
                    synthesis_text,
                    user_id=user_id,
                    source="synthesis",
                    memory_type="insight",
                    confidence=0.85,
                    status="approved",
                    tags=[tag, "synthesis"],
                )
                if ref:
                    # Link synthesized memory back to source cluster
                    source_ids = [mid for mid, _ in sample]
                    col_result = col.get(ids=[ref.id], include=["metadatas", "documents"])
                    if col_result.get("ids"):
                        sm = dict((col_result["metadatas"] or [{}])[0])
                        sm["related_ids"] = ",".join(source_ids)
                        sm["concept_tags"] = tag
                        col.upsert(ids=[ref.id], documents=[synthesis_text], metadatas=[sm])
                    synthesized += 1
            except Exception as exc:
                logger.warning("synthesis failed for tag=%s user=%s: %s", tag, user_id, exc)

    except Exception as exc:
        logger.warning("synthesis pass failed user=%s: %s", user_id, exc)

    summary = {"user_id": user_id, "synthesized": synthesized}
    logger.info("dreaming/synthesis: %s", summary)
    return summary


async def _extract_open_loops(user_id: str, db=None) -> dict:
    """Extract open loops from recent messages and store in open_loops table.

    An open loop is an unresolved thread: a worry, a plan, something the user
    mentioned they're waiting on, or something emotionally significant that
    deserves a follow-up. Runs as part of the nightly dreaming cycle.
    """
    from db_compat import get_compat_db as _get_compat_db
    created_at_valid_sql = CREATED_AT_VALID_TIMESTAMP_SQL.replace("created_at", "m.created_at")

    # Load last 48h of messages for this user
    try:
        async with _get_compat_db() as _db:
            async with _db.execute(
                f"""SELECT m.content, m.role FROM chat_messages m
                   JOIN chat_sessions s ON m.session_id = s.id
                   WHERE s.user_id = ? AND m.role = 'user'
                     AND CASE
                           WHEN {created_at_valid_sql}
                           THEN m.created_at::timestamptz
                           ELSE NULL
                         END > CURRENT_TIMESTAMP - INTERVAL '2 days'
                   ORDER BY m.created_at DESC LIMIT 50""",
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
    except Exception as exc:
        logger.warning("open_loops: message load failed user=%s: %s", user_id, exc)
        return {"user_id": user_id, "extracted": 0}

    if not rows:
        return {"user_id": user_id, "extracted": 0}

    messages_text = "\n".join(f"User: {r['content']}" for r in rows[:30])
    prompt = f"""Identify open loops in these messages — unresolved threads, worries, plans, waiting situations, or emotionally significant mentions that deserve a follow-up.

Messages:
{messages_text}

Return a JSON array (max 5 items) where each item has:
- "loop_text": brief description of the open loop (1-2 sentences)
- "follow_up_hint": what Zoe should ask/say when following up
- "emotional_weight": 1 (low) to 5 (high)
- "follow_up_after": ISO-8601 datetime (when to follow up, e.g. tomorrow or in 3 days)

Only include genuine open loops, not resolved topics. Return [] if none found."""

    try:
        import json as _json
        from zoe_agent import _llm_chat  # type: ignore[import]
        response = await _llm_chat([
            {"role": "system", "content": "You extract open loops from conversations. Return only valid JSON arrays."},
            {"role": "user", "content": prompt},
        ], max_tokens=500)
        loops = _json.loads(response.strip())
        if not isinstance(loops, list):
            loops = []
    except Exception as exc:
        logger.warning("open_loops: LLM extraction failed user=%s: %s", user_id, exc)
        return {"user_id": user_id, "extracted": 0}

    stored = 0
    try:
        async with _get_compat_db() as _db:
            for loop in loops[:5]:
                if not isinstance(loop, dict) or not loop.get("loop_text"):
                    continue
                await _db.execute(
                    """INSERT INTO open_loops
                       (user_id, loop_text, follow_up_hint, emotional_weight, follow_up_after)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        loop.get("loop_text", ""),
                        loop.get("follow_up_hint", ""),
                        loop.get("emotional_weight", 1),
                        loop.get("follow_up_after"),
                    ),
                )
                stored += 1
            await _db.commit()
    except Exception as exc:
        logger.warning("open_loops: DB insert failed user=%s: %s", user_id, exc)

    return {"user_id": user_id, "extracted": stored}


async def run_dreaming_cycle(user_id: str, db=None, run_agent_sync_phase: bool = True) -> dict:
    """Run the full dreaming cycle for a user.

    Called by nightly-training-cycle.sh after run_memory_digest.
    Phase 1 (REM)         — runs nightly: reinforce recent memories
    Phase 1.5 (Open Loops)— runs nightly: extract unresolved threads same night they're mentioned
    Phase 2 (Deep Sleep)  — runs weekly (Sunday): consolidation
    Phase 3 (Synthesis)   — runs weekly (Sunday): long-term synthesis
    Phase 4 (Portrait)    — runs weekly (Sunday): synthesizes user portrait in SQLite
    Phase 5 (Agent Sync)  — runs weekly (Sunday): regenerate ZOE_SELF.md
    """
    import datetime

    is_sunday = datetime.datetime.utcnow().weekday() == 6
    result: dict = {"user_id": user_id}

    rem = await _rem_reinforce_pass(user_id)
    result["rem"] = rem

    # Idle person-link resolver (default OFF via ZOE_MEMORY_LINK_RESOLVER_ENABLED):
    # re-link person_pending facts to a real people.id once the contact exists.
    # A true no-op while the flag is off — no store scan, no DB open.
    try:
        link_result = await _resolve_pending_person_links(user_id, db=db)
        if link_result.get("scanned") or link_result.get("relinked"):
            result["link_resolver"] = link_result
    except Exception as exc:
        logger.warning("dreaming: link resolver failed user=%s: %s", user_id, exc)

    # Phase 1.5: Open loops extraction — runs nightly so loops are detected the same
    # day they're mentioned, not deferred until Sunday.
    try:
        loops_result = await _extract_open_loops(user_id, db=db)
        result["open_loops"] = loops_result
    except Exception as exc:
        logger.warning("dreaming: open_loops extraction failed user=%s: %s", user_id, exc)
        result["open_loops"] = {"status": "error", "error": str(exc)}

    if is_sunday:
        deep = await _deep_sleep_pass(user_id)
        result["deep_sleep"] = deep
        synth = await _synthesis_pass(user_id)
        result["synthesis"] = synth

        # Phase 4: Portrait synthesis — LLM-written narrative understanding of the user.
        # Stored in SQLite user_portraits and injected into every chat turn.
        try:
            from user_portrait import run_portrait_synthesis  # type: ignore[import]
            portrait = await run_portrait_synthesis(user_id, db=db)
            result["portrait"] = portrait
        except Exception as exc:
            logger.warning("dreaming: portrait synthesis failed user=%s: %s", user_id, exc)
            result["portrait"] = {"status": "error", "error": str(exc)}

        # Phase 5: Agent sync is system-wide, not per-user.  Callers that
        # iterate users should run it once for the first user only.
        if run_agent_sync_phase:
            try:
                from agent_sync import run_agent_sync  # type: ignore[import]
                sync_result = await run_agent_sync()
                result["agent_sync"] = sync_result
            except Exception as exc:
                logger.warning("dreaming: agent_sync failed: %s", exc)
                result["agent_sync"] = {"status": "error", "error": str(exc)}

    # Opt-in, report-only Lint pass (default OFF via ZOE_MEMORY_LINT_IN_DREAMING).
    # Lint never mutates stored memory; it only emits a structured report of
    # contradictions / stale / orphan / duplicate rows for human review.
    try:
        from memory_lint import dreaming_lint_enabled, lint_user

        if dreaming_lint_enabled():
            report = await lint_user(user_id)
            result["lint"] = report.to_dict()
            logger.info(
                "dreaming: lint report user=%s scanned=%d findings=%d",
                user_id, report.scanned, report.total,
            )
    except Exception as exc:
        logger.warning("dreaming: lint pass failed user=%s: %s", user_id, exc)
        result["lint"] = {"status": "error", "error": str(exc)}

    logger.info("dreaming cycle complete: %s", result)
    return result


async def run_dreaming_for_all(db=None) -> list[dict]:
    """Run dreaming cycle for all users who have approved memories."""
    from memory_service import get_memory_service

    svc = get_memory_service()
    try:
        user_ids = await svc.list_users()
    except AttributeError:
        try:
            user_ids = await _list_user_ids(
                _message_owner_users_sql(today_only=False), db=db
            )
        except Exception as exc:
            logger.error("dreaming: could not list users: %s", exc)
            return []

    results = []
    for idx, uid in enumerate(user_ids):
        r = await run_dreaming_cycle(uid, db=db, run_agent_sync_phase=(idx == 0))
        results.append(r)
    return results


# ═══════════════════════════════════════════════════════════════════════════
# MUSIC TASTE DIGEST
# Nightly pass: reads raw music_listening_events from the Postgres pool, scores artists
# and genres by play/skip behaviour, and writes preference facts to MemPalace.
# ═══════════════════════════════════════════════════════════════════════════

async def run_music_taste_digest(user_id: str) -> dict:
    """Consolidate 30 days of music events into MemPalace preference memories.

    Scoring per entity (artist or genre):
        play       +2
        now_playing +1
        skip        -3
        skip_fast   -5
        repeat      +4
    Entities scoring > 3 → preference fact ingested into MemPalace.
    Entities scoring < -3 → avoidance fact ingested.

    Returns {"user_id": ..., "facts_ingested": N, "artists_tracked": M}.
    """
    import time as _time
    from collections import defaultdict

    result: dict = {"user_id": user_id, "facts_ingested": 0, "artists_tracked": 0}
    SIGNAL_WEIGHTS = {
        "complete":      +2,
        "repeat":        +3,
        "partial":       +1,
        "skip":          -2,
        "now_playing":   +1,
        "play":          +1,   # legacy fallback for old events
        "pause":          0,
        "volume_change":  0,
    }
    _SCORE_MAP = SIGNAL_WEIGHTS

    try:
        from db_pool import get_db_ctx  # type: ignore[import]
        cutoff_ts = _time.time() - 30 * 86400

        # Short-lived pooled acquire for the read only. The bare
        # `async for db in get_db(): break` form leaves the generator
        # suspended at its yield, so the pooled connection is held for the
        # entire (substantial) scoring + MemPalace ingest work below instead
        # of being released after the query. Materialize the rows, then let
        # get_db_ctx release the connection before any scoring/ingest.
        async with get_db_ctx() as _db:
            events = await (
                await _db.execute(
                    """SELECT event_type, track_title, artist, genre
                       FROM music_listening_events
                       WHERE user_id = ? AND ts >= ?
                       ORDER BY ts ASC""",
                    (user_id, cutoff_ts),
                )
            ).fetchall()
    except Exception as exc:
        logger.warning("music_taste_digest: could not load events for %s: %s", user_id, exc)
        result["error"] = str(exc)
        return result

    if not events:
        logger.info("music_taste_digest: no events in last 30d for %s", user_id)
        result["skipped_reason"] = "no_events"
        return result

    # Accumulate scores per artist and genre
    artist_scores: dict[str, float] = defaultdict(float)
    artist_plays: dict[str, int] = defaultdict(int)
    artist_skips: dict[str, int] = defaultdict(int)
    genre_scores: dict[str, float] = defaultdict(float)
    genre_plays: dict[str, int] = defaultdict(int)
    genre_skips: dict[str, int] = defaultdict(int)

    for row in events:
        evt_type = row[0] or ""
        artist = (row[2] or "").strip()
        genre = (row[3] or "").strip()
        delta = _SCORE_MAP.get(evt_type, 0)

        if artist:
            artist_scores[artist] += delta
            if delta > 0:
                artist_plays[artist] += 1
            elif delta < 0:
                artist_skips[artist] += 1

        if genre:
            genre_scores[genre] += delta
            if delta > 0:
                genre_plays[genre] += 1
            elif delta < 0:
                genre_skips[genre] += 1

    result["artists_tracked"] = len(artist_scores) + len(genre_scores)

    # Build preference facts
    facts: list[tuple[str, str]] = []  # (fact_text, memory_type_hint)
    for artist, score in artist_scores.items():
        plays = artist_plays.get(artist, 0)
        skips = artist_skips.get(artist, 0)
        if score > 3:
            facts.append((
                f"User frequently plays {artist}: {plays} play(s), {skips} skip(s) in last 30 days.",
                "preference",
            ))
        elif score < -3:
            facts.append((
                f"User avoids {artist}: consistently skipped ({skips} skips, {plays} plays).",
                "preference",
            ))

    for genre, score in genre_scores.items():
        plays = genre_plays.get(genre, 0)
        skips = genre_skips.get(genre, 0)
        if score > 3:
            facts.append((
                f"User frequently listens to {genre} music: {plays} play(s), {skips} skip(s) in last 30 days.",
                "preference",
            ))
        elif score < -3:
            facts.append((
                f"User avoids {genre} music: consistently skipped ({skips} skips, {plays} plays).",
                "preference",
            ))

    if not facts:
        logger.info("music_taste_digest: no strong preferences found for %s", user_id)
        return result

    try:
        from memory_service import get_memory_service, MemoryServiceError  # type: ignore[import]
        svc = get_memory_service()
    except Exception as exc:
        logger.warning("music_taste_digest: memory service unavailable: %s", exc)
        result["error"] = str(exc)
        return result

    for fact_text, mem_type in facts:
        try:
            ref = await svc.ingest(
                fact_text,
                user_id=user_id,
                source="music_digest",
                memory_type="preference",
                confidence=0.85,
                status="approved",
                tags=["music", "taste", "auto"],
            )
            if ref is not None:
                result["facts_ingested"] += 1
                logger.info("music_taste_digest: stored for %s: %s", user_id, fact_text[:80])
        except Exception as exc:
            logger.warning("music_taste_digest: ingest failed for %s: %s", user_id, exc)

    logger.info("music_taste_digest: %s", result)
    return result


async def run_music_taste_digest_for_all(db=None) -> list[dict]:
    """Run music taste digest for all users who have any music events."""
    results = []
    try:
        user_ids = await _list_user_ids(
            "SELECT DISTINCT user_id FROM music_listening_events", db=db
        )
    except Exception as exc:
        logger.error("music_taste_digest: could not list users: %s", exc)
        return []

    for uid in user_ids:
        r = await run_music_taste_digest(uid)
        results.append(r)
        logger.info("music_taste_digest: %s", r)
    return results
