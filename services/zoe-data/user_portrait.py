"""
User Portrait: synthesized narrative understanding of each person.

A portrait is a 300-500 word flowing paragraph document — not a fact list —
that captures who the user is, how they communicate, their emotional patterns,
their current life context, and their relationship with Zoe. It is regenerated
weekly by run_portrait_synthesis() during the Sunday dreaming cycle.

At runtime, load_portrait() does a direct SQLite key-lookup (no vector search)
and the result is injected into every conversation turn via _build_prompt().

Design principle: personal data stays in this runtime layer — portraits live in
SQLite and are injected into the context window. They never enter model weights.
"""
import asyncio
import json
import logging
import os
import time

import httpx

from gemma_endpoint import gemma_base

logger = logging.getLogger(__name__)
_PORTRAIT_MODEL = os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")

# Maximum chars of portrait text injected into the context window each turn.
# ~600 chars ≈ ~150 tokens — meaningful but within budget for Gemma on Jetson.
PORTRAIT_MAX_INJECT_CHARS = int(os.environ.get("PORTRAIT_MAX_INJECT_CHARS", "600"))

# Portrait generation only runs when the user has at least this many approved memories.
_MIN_MEMORIES_FOR_PORTRAIT = int(os.environ.get("PORTRAIT_MIN_MEMORIES", "5"))

PORTRAIT_SYNTHESIS_PROMPT = """\
You are building a deep, warm understanding of a person based on everything they \
have shared with their AI companion Zoe over time.

Write a portrait of this person that will help Zoe engage with them as a genuine \
companion — not just recall facts. Focus on understanding, not enumeration.

Cover:
- Who they are: personality, values, what they genuinely care about
- Their world right now: current life context, recurring concerns and hopes
- How they communicate: do they want direct answers or gentle exploration? Brief \
or thorough? Practical help or just to be heard?
- Emotional patterns: what brings them anxiety, what brings joy, how they handle \
stress, what they are proud of
- Their relationship with Zoe: what topics they come to Zoe for, what they seem \
to need most from this relationship
- What they are working toward: goals, things they are trying to get better at

Write 250-400 words as flowing paragraphs. Be specific, warm, and honest. \
Write as if you are briefing a dear friend who is about to have a meaningful \
conversation with this person. Do not list raw facts back — synthesize them \
into real understanding.

[MEMORY FACTS — extracted from their conversations]:
{memory_facts}

[SYNTHESIZED INSIGHTS — patterns noticed over time]:
{insights}

[RECENT JOURNAL ENTRIES — if available]:
{journal_entries}
"""


async def run_portrait_synthesis(user_id: str, db=None) -> dict:
    """Synthesize a fresh user portrait from MemPalace memories and journal entries.

    Called weekly (Sunday) as Phase 4 of run_dreaming_cycle().
    Also callable manually via POST /api/portrait/{user_id}/regenerate.

    Returns a result dict with keys: user_id, status, chars, memory_count, error.
    """
    result: dict = {"user_id": user_id, "status": "skipped", "chars": 0, "memory_count": 0}
    try:
        from memory_service import get_memory_service  # type: ignore[import]
        svc = get_memory_service()
        refs = await svc.load_for_prompt(user_id, limit=200)
        approved = [r for r in refs if getattr(r, "text", None)]
        result["memory_count"] = len(approved)

        if len(approved) < _MIN_MEMORIES_FOR_PORTRAIT:
            result["status"] = "too_few_memories"
            logger.info("portrait: skip user=%s (only %d approved facts)", user_id, len(approved))
            return result

        # Separate insights (synthesis/dreaming) from regular facts
        fact_lines = []
        insight_lines = []
        for r in approved:
            text = (r.text or "").strip()
            if not text:
                continue
            src = (r.metadata or {}).get("source", "") or ""
            mt = (r.metadata or {}).get("memory_type", "") or ""
            if src == "synthesis" or mt == "insight":
                insight_lines.append(f"- {text}")
            else:
                fact_lines.append(f"- {text}")

        memory_facts = "\n".join(fact_lines[:120]) if fact_lines else "(none yet)"
        insights = "\n".join(insight_lines[:30]) if insight_lines else "(none yet)"

        # Load recent journal entries
        journal_text = "(none)"
        try:
            from db_pool import get_db_ctx  # type: ignore[import]
            jsql = """SELECT title, content, mood, created_at
                   FROM journal_entries
                   WHERE user_id = ? AND deleted = 0
                   ORDER BY created_at DESC LIMIT 10"""
            if db is not None:
                rows = await (await db.execute(jsql, (user_id,))).fetchall()
            else:
                # Short-lived pooled acquire — the bare `async for db in
                # get_db(): break` form leaves the generator suspended at the
                # yield, closing the connection mid-query.
                async with get_db_ctx() as _db:
                    rows = await (await _db.execute(jsql, (user_id,))).fetchall()
            if rows:
                entries = []
                for row in rows:
                    title = row[0] or "Untitled"
                    content = (row[1] or "")[:300]
                    mood = f" [{row[2]}]" if row[2] else ""
                    date = (row[3] or "")[:10]
                    entries.append(f"[{date}{mood}] {title}: {content}")
                journal_text = "\n\n".join(entries)
        except Exception as je:
            logger.debug("portrait: journal load failed (non-fatal): %s", je)

        prompt = PORTRAIT_SYNTHESIS_PROMPT.format(
            memory_facts=memory_facts,
            insights=insights,
            journal_entries=journal_text,
        )

        portrait_text = await _call_llm_for_portrait(prompt)
        if not portrait_text:
            result["status"] = "llm_empty"
            return result

        # Store in SQLite user_portraits
        await _save_portrait(user_id, portrait_text, len(approved), db=db)
        result["status"] = "ok"
        result["chars"] = len(portrait_text)
        logger.info("portrait: generated user=%s chars=%d memories=%d", user_id, len(portrait_text), len(approved))
        return result

    except Exception as exc:
        logger.error("portrait: synthesis failed user=%s: %s", user_id, exc)
        result["status"] = "error"
        result["error"] = str(exc)
        return result


async def _call_llm_for_portrait(prompt: str) -> str:
    """Call the local LLM to generate a portrait. Returns the portrait text or ''."""
    url = f"{gemma_base()}/v1/chat/completions"
    payload = {
        "model": _PORTRAIT_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a perceptive, empathetic writer. "
                    "Return ONLY the portrait text — no preamble, no explanation, no JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 600,
        "temperature": 0.7,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
    except Exception as exc:
        logger.error("portrait: LLM call failed: %s", exc)
        return ""


async def _save_portrait(user_id: str, portrait_text: str, memory_count: int, db=None) -> None:
    """Upsert the portrait into the user_portraits table."""
    sql = """INSERT INTO user_portraits (user_id, portrait_text, portrait_version,
                   generated_from_memory_count, last_generated)
               VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                   portrait_text = excluded.portrait_text,
                   portrait_version = user_portraits.portrait_version + 1,
                   generated_from_memory_count = excluded.generated_from_memory_count,
                   last_generated = CURRENT_TIMESTAMP"""
    params = (user_id, portrait_text, memory_count)
    try:
        from db_pool import get_db_ctx  # type: ignore[import]
        if db is not None:
            await db.execute(sql, params)
            await db.commit()
        else:
            # Short-lived pooled acquire for the write — the bare
            # `async for db in get_db(): break` form leaves the generator
            # suspended at the yield, closing the connection mid-write.
            async with get_db_ctx() as _db:
                await _db.execute(sql, params)
                await _db.commit()
    except Exception as exc:
        logger.error("portrait: save failed user=%s: %s", user_id, exc)


async def load_portrait(user_id: str, db=None) -> str:
    """Load portrait text for a user. Returns '' if none exists yet.

    Fast direct SQLite lookup — no vector search, no embedding overhead.
    Called on every chat turn.
    """
    sql = "SELECT portrait_text FROM user_portraits WHERE user_id = ?"
    try:
        from db_pool import get_db_ctx  # type: ignore[import]
        if db is not None:
            row = await (await db.execute(sql, (user_id,))).fetchone()
        else:
            # Short-lived pooled acquire — the bare `async for db in get_db():
            # break` form leaves the generator suspended at the yield, closing
            # the connection mid-query.
            async with get_db_ctx() as _db:
                row = await (await _db.execute(sql, (user_id,))).fetchone()
        if row and row[0]:
            text = row[0].strip()
            # Truncate at PORTRAIT_MAX_INJECT_CHARS to stay within token budget
            if len(text) > PORTRAIT_MAX_INJECT_CHARS:
                text = text[:PORTRAIT_MAX_INJECT_CHARS].rsplit(" ", 1)[0] + "…"
            return text
        return ""
    except Exception as exc:
        logger.debug("portrait: load failed (non-fatal) user=%s: %s", user_id, exc)
        return ""


async def run_portrait_synthesis_for_all(db=None) -> list[dict]:
    """Run portrait synthesis for all users who have approved memories.

    Called as part of the Sunday weekly dreaming cycle.
    """
    from memory_service import get_memory_service  # type: ignore[import]
    svc = get_memory_service()
    try:
        user_ids = await svc.list_users()
    except AttributeError:
        try:
            from db_pool import get_db_ctx  # type: ignore[import]
            sql = "SELECT DISTINCT user_id FROM chat_sessions"
            if db is not None:
                rows = await (await db.execute(sql)).fetchall()
            else:
                # Short-lived pooled acquire for the listing only — the bare
                # `async for db in get_db(): break` form leaves the generator
                # suspended at the yield, closing the connection mid-query.
                async with get_db_ctx() as _db:
                    rows = await (await _db.execute(sql)).fetchall()
            user_ids = [r[0] for r in rows if r[0]]
        except Exception as exc:
            logger.error("portrait: could not list users: %s", exc)
            return []

    results = []
    for uid in user_ids:
        r = await run_portrait_synthesis(uid, db=db)
        results.append(r)
        logger.info("portrait: synthesis result: %s", r)
    return results
