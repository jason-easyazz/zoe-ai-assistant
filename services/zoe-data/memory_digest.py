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
import uuid

import httpx

logger = logging.getLogger(__name__)

_GEMMA_URL = os.environ.get("GEMMA_SERVER_URL", "http://127.0.0.1:11434")

_EXTRACTION_PROMPT = """\
You are extracting personal facts from a chat transcript. Only extract facts the user explicitly stated about themselves, their family, preferences, or life. Do NOT infer, assume, or add anything not stated directly.

Return ONLY a JSON array (no preamble, no explanation). Each item has:
  "type": one of "profile" | "preference" | "habit" | "event" | "relationship" | "health"
  "fact": a single concise sentence (max 150 chars) in third-person (e.g. "User is 44 years old")

If nothing personal was stated, return: []

Chat messages (user turns only):
{chat_text}
"""


# When Bonsai judges contradictions, we want a single-token yes/no so the
# decision is cheap and unambiguous. The schema lets us also capture *which*
# existing fact is contradicted when multiple candidates are evaluated at
# once. Temperature=0 removes jitter.
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


async def run_memory_digest(user_id: str, db=None) -> dict:
    """Extract facts from today's chat history and write to MemPalace + memory_items.

    Args:
        user_id: The user to run the digest for.
        db:      aiosqlite database connection (optional — opens its own if None).

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

        facts = await _extract_facts_with_gemma(chat_text)
        result["extracted"] = len(facts)
        if not facts:
            return result

        from pi_agent import _mempalace_load_user_facts  # type: ignore[import]
        from memory_service import MemoryServiceError, get_memory_service
        existing_text = await _mempalace_load_user_facts(user_id, limit=100)
        existing_lower = existing_text.lower()
        svc = get_memory_service()

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

            # ── Contradiction check ──────────────────────────────────────
            # Pull the top-3 semantically similar existing facts and ask
            # Bonsai whether any of them contradict the new one. If yes,
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

    except Exception as exc:
        logger.error("memory_digest: failed for %s: %s", user_id, exc, exc_info=True)
        result["error"] = str(exc)
    return result


async def _load_todays_messages(user_id: str, db=None) -> str:
    """Load today's user-turn messages from chat_messages, joined to chat_sessions."""
    try:
        from database import get_db  # type: ignore[import]
        if db is None:
            async for db in get_db():
                break

        rows = await db.execute(
            """
            SELECT cm.content
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cs.user_id = ?
              AND cm.role = 'user'
              AND DATE(cm.created_at) = DATE('now', 'localtime')
            ORDER BY cm.created_at ASC
            LIMIT 200
            """,
            (user_id,),
        )
        rows = await rows.fetchall()
        if not rows:
            return ""
        lines = [row[0] for row in rows if row[0]]
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("memory_digest: could not load messages for %s: %s", user_id, exc)
        return ""


async def _extract_facts_with_gemma(chat_text: str) -> list[dict]:
    """Send chat transcript to Bonsai and parse the JSON fact list."""
    prompt = _EXTRACTION_PROMPT.format(chat_text=chat_text[:3000])
    payload = {
        "model": "gemma-4-E2B-it-Q4_K_M.gguf",
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
                logger.warning("memory_digest: Bonsai returned no JSON array: %s", text[:200])
                return []
            return json.loads(text[start:end])
    except json.JSONDecodeError as je:
        logger.warning("memory_digest: JSON parse error: %s", je)
        return []
    except Exception as exc:
        logger.warning("memory_digest: Bonsai call failed: %s", exc)
        return []


async def _is_contradiction(new_fact: str, existing_fact: str) -> bool:
    """Ask Bonsai whether a new fact contradicts an existing one.

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
        "model": "gemma-4-E2B-it-Q4_K_M.gguf",
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
#      approved rows, ask Bonsai if they contradict; if yes, keep the
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
            from database import get_db  # type: ignore[import]
            if db is None:
                async for db in get_db():
                    break
            rows = await db.execute("SELECT DISTINCT user_id FROM chat_sessions")
            rows = await rows.fetchall()
            user_ids = [row[0] for row in rows if row[0]]
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
        from database import get_db  # type: ignore[import]
        if db is None:
            async for db in get_db():
                break
        rows = await db.execute(
            """
            SELECT DISTINCT cs.user_id
            FROM chat_messages cm
            JOIN chat_sessions cs ON cm.session_id = cs.id
            WHERE cm.role = 'user'
              AND DATE(cm.created_at) = DATE('now', 'localtime')
            """,
        )
        rows = await rows.fetchall()
        user_ids = [row[0] for row in rows if row[0]]
    except Exception as exc:
        logger.error("memory_digest: could not list active users: %s", exc)
        return []

    for uid in user_ids:
        result = await run_memory_digest(uid, db=db)
        results.append(result)
        logger.info("memory_digest: %s", result)
    return results
