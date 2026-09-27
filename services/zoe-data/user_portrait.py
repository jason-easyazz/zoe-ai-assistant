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

# Token budget for the assembled synthesis prompt (instructions + facts + insights +
# journal). This call goes STRAIGHT to llama-server (gemma_base(), not the Flue
# client), so nothing else windows it: the prompt plus max_tokens (600) plus the
# system line must fit the brain's per-slot context (--ctx-size 8192 since B6.6)
# or llama-server refuses the request ("exceeds the available context size") and
# the portrait silently never regenerates. 5500 + 600 + chat-template overhead
# leaves ~2k tokens of headroom. Counted EXACTLY with llama-server's /tokenize;
# chars/4 (the repo convention) is only the fallback when that endpoint is down.
PORTRAIT_PROMPT_BUDGET_TOKENS = int(os.environ.get("PORTRAIT_PROMPT_BUDGET_TOKENS", "5500"))
_PORTRAIT_MAX_FACTS = 120
_PORTRAIT_MAX_INSIGHTS = 30

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


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


_TOKENIZE_TIMEOUT_S = 5.0
_tokenize_warned = False


async def _tokenize_count(text: str) -> int | None:
    """Exact token count from llama-server's own tokenizer (POST /tokenize).

    Returns None when the endpoint is unreachable or answers badly; callers then
    fall back to the chars/4 estimate. The failure is logged at WARNING once per
    process (a down brain would otherwise log on every portrait).
    """
    global _tokenize_warned
    try:
        async with httpx.AsyncClient(timeout=_TOKENIZE_TIMEOUT_S) as client:
            resp = await client.post(f"{gemma_base()}/tokenize", json={"content": text})
            resp.raise_for_status()
            return len(resp.json()["tokens"])
    except Exception as exc:
        if not _tokenize_warned:
            _tokenize_warned = True
            logger.warning(
                "portrait: /tokenize unavailable (%s) — budgeting with the chars/4 estimate", type(exc).__name__
            )
        return None


def _render_portrait_prompt(facts: list[str], insights: list[str], journal: list[str]) -> str:
    return PORTRAIT_SYNTHESIS_PROMPT.format(
        memory_facts="\n".join(facts) if facts else "(none yet)",
        insights="\n".join(insights) if insights else "(none yet)",
        journal_entries="\n\n".join(journal) if journal else "(none)",
    )


def _fit_by_estimate(facts: list[str], insights: list[str], journal: list[str], est_budget: int) -> tuple[str, int]:
    """Trim the lists IN PLACE until chars/4 of the prompt is <= ``est_budget``.

    Inputs are best-first (facts/insights in load_for_prompt() importance x
    recency order, journal newest-first), so every drop is from the TAIL. Order
    of sacrifice: journal to 3, facts to 20, insights to 5, then journal to 0,
    facts to 1, insights to 0; a single item still too big is finally clipped.
    The instruction block is never touched. Returns (prompt, items_clipped).
    """
    prompt = _render_portrait_prompt(facts, insights, journal)
    for section, floor in ((journal, 3), (facts, 20), (insights, 5), (journal, 0), (facts, 1), (insights, 0)):
        while len(section) > floor and _estimate_tokens(prompt) > est_budget:
            section.pop()
            prompt = _render_portrait_prompt(facts, insights, journal)
    clipped = 0
    if _estimate_tokens(prompt) > est_budget:
        fixed = _estimate_tokens(_render_portrait_prompt([], [], [])) * 4
        items = [*facts, *insights, *journal]
        share = max(40, (est_budget * 4 - fixed) // max(1, len(items)) - 2)
        for section in (facts, insights, journal):
            for i, line in enumerate(section):
                if len(line) > share:
                    section[i] = line[: share - 1] + "…"
                    clipped += 1
        prompt = _render_portrait_prompt(facts, insights, journal)
    return prompt, clipped


async def build_portrait_prompt(
    fact_lines: list[str],
    insight_lines: list[str],
    journal_entries: list[str],
    *,
    user_id: str = "",
    budget_tokens: int | None = None,
    count_tokens=None,
) -> str:
    """Assemble the synthesis prompt so its REAL token count is <= the budget.

    The prompt is counted with llama-server's tokenizer (``count_tokens``,
    default :func:`_tokenize_count`). chars/4 undercounts token-dense text (CJK,
    emoji, identifiers) several-fold, so it is only a trimming heuristic: the
    real count calibrates it (effective estimate budget = budget x est/real),
    and the trimmed prompt is re-counted, up to 4 rounds. If the tokenizer is
    unavailable the chars/4 estimate is used as-is. Under budget the output is
    byte-identical to the pre-budget prompt.
    """
    budget = PORTRAIT_PROMPT_BUDGET_TOKENS if budget_tokens is None else budget_tokens
    counter = count_tokens or _tokenize_count
    facts = list(fact_lines[:_PORTRAIT_MAX_FACTS])
    insights = list(insight_lines[:_PORTRAIT_MAX_INSIGHTS])
    journal = list(journal_entries)
    before = (len(facts), len(insights), len(journal))

    prompt = _render_portrait_prompt(facts, insights, journal)
    real = await counter(prompt)
    source = "tokenizer" if real is not None else "chars/4"
    count_before = real if real is not None else _estimate_tokens(prompt)
    if count_before <= budget:
        return prompt

    clipped = 0
    if real is None:
        prompt, clipped = _fit_by_estimate(facts, insights, journal, budget)
        count_after = _estimate_tokens(prompt)
    else:
        count_after = real
        for _ in range(4):
            # tokens per chars/4-unit, measured on the text actually being sent
            ratio = count_after / max(1, _estimate_tokens(prompt))
            prompt, c = _fit_by_estimate(facts, insights, journal, int(budget / max(ratio, 1e-6)))
            clipped += c
            recount = await counter(prompt)
            if recount is None:  # tokenizer died mid-trim: stay on the calibrated result
                count_after = int(_estimate_tokens(prompt) * ratio)
                break
            count_after = recount
            if count_after <= budget:
                break

    logger.info(
        "portrait: prompt trimmed to budget user=%s counter=%s tokens=%d->%d budget=%d "
        "facts=%d->%d insights=%d->%d journal=%d->%d clipped=%d",
        user_id, source, count_before, count_after, budget,
        before[0], len(facts), before[1], len(insights), before[2], len(journal), clipped,
    )
    return prompt


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

        # Load recent journal entries (newest first)
        journal_entries: list[str] = []
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
                journal_entries = entries
        except Exception as je:
            logger.debug("portrait: journal load failed (non-fatal): %s", je)

        prompt = await build_portrait_prompt(fact_lines, insight_lines, journal_entries, user_id=user_id)

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
