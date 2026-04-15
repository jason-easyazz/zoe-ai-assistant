"""
LLM-driven nightly memory digest.

Reads today's chat messages for a user, prompts Bonsai to extract personal
facts as structured JSON, deduplicates against existing MemPalace records,
and writes new facts to both MemPalace (single source of truth) and
memory_items (for UI display via /api/memories).

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

_BONSAI_URL = os.environ.get("BONSAI_URL", "http://127.0.0.1:11435")

_EXTRACTION_PROMPT = """\
You are extracting personal facts from a chat transcript. Only extract facts the user explicitly stated about themselves, their family, preferences, or life. Do NOT infer, assume, or add anything not stated directly.

Return ONLY a JSON array (no preamble, no explanation). Each item has:
  "type": one of "profile" | "preference" | "habit" | "event" | "relationship" | "health"
  "fact": a single concise sentence (max 150 chars) in third-person (e.g. "User is 44 years old")

If nothing personal was stated, return: []

Chat messages (user turns only):
{chat_text}
"""


async def run_memory_digest(user_id: str, db=None) -> dict:
    """Extract facts from today's chat history and write to MemPalace + memory_items.

    Args:
        user_id: The user to run the digest for.
        db:      aiosqlite database connection (optional — opens its own if None).

    Returns:
        dict with keys: user_id, extracted, new, skipped_duplicates, error (if any).
    """
    result: dict = {"user_id": user_id, "extracted": 0, "new": 0, "skipped_duplicates": 0}
    try:
        chat_text = await _load_todays_messages(user_id, db)
        if not chat_text or len(chat_text.split()) < 20:
            logger.info("memory_digest: skipping %s — not enough chat activity today", user_id)
            result["skipped_reason"] = "insufficient_activity"
            return result

        facts = await _extract_facts_with_bonsai(chat_text)
        result["extracted"] = len(facts)
        if not facts:
            return result

        # Load existing MemPalace facts for this user (for dedup)
        from pi_agent import _mempalace_load_user_facts, _mempalace_add  # type: ignore[import]
        existing_text = await _mempalace_load_user_facts(user_id, limit=100)
        existing_lower = existing_text.lower()

        for item in facts:
            fact = (item.get("fact") or "").strip()
            if not fact or len(fact) < 10:
                continue
            # Dedup: simple word-overlap check against existing facts
            fact_words = set(fact.lower().split())
            overlap_score = sum(1 for w in fact_words if w in existing_lower) / max(len(fact_words), 1)
            if overlap_score > 0.7:
                logger.debug("memory_digest: dedup skip (%.0f%% overlap): %s", overlap_score * 100, fact[:60])
                result["skipped_duplicates"] += 1
                continue

            # Write to MemPalace (single source of truth)
            tags = ["digest", item.get("type", "unknown")]
            ok = await _mempalace_add(fact, user_id=user_id, tags=tags, added_by="memory_digest")
            if ok:
                result["new"] += 1
                logger.info("memory_digest: stored for %s: %s", user_id, fact[:80])
                # Also write to memory_items for UI display
                await _write_to_memory_items(user_id, fact, item.get("type", "fact"), db)

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


async def _extract_facts_with_bonsai(chat_text: str) -> list[dict]:
    """Send chat transcript to Bonsai and parse the JSON fact list."""
    prompt = _EXTRACTION_PROMPT.format(chat_text=chat_text[:3000])
    payload = {
        "model": "bonsai-8b",
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
            resp = await client.post(f"{_BONSAI_URL}/v1/chat/completions", json=payload)
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


async def _write_to_memory_items(user_id: str, fact: str, memory_type: str, db=None) -> None:
    """Write an extracted fact to memory_items (SQLite) for UI display."""
    try:
        from database import get_db  # type: ignore[import]
        if db is None:
            async for db in get_db():
                break
        await db.execute(
            """INSERT OR IGNORE INTO memory_items
               (id, user_id, memory_type, title, content, entity_type, entity_id, confidence,
                source_type, source_id, source_excerpt, visibility, status)
               VALUES (?, ?, ?, ?, ?, 'chat', 'digest', 0.8, 'digest', 'nightly', ?, 'personal', 'approved')""",
            (
                str(uuid.uuid4()),
                user_id,
                memory_type,
                "Digest: " + fact[:80],
                fact,
                fact[:200],
            ),
        )
        await db.commit()
    except Exception as exc:
        logger.warning("memory_digest: memory_items write failed: %s", exc)


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
