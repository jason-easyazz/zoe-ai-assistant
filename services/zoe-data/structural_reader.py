"""The post-turn CLAIM-ROW reader for ``ZOE_STRUCTURAL_CLAIMS=shadow``: a separate, bounded 4B call AFTER the facts are stored.

Why it exists (live regression 2026-10-09, #1943): ``shadow`` used to ask the per-turn extractor for the claim row INSIDE the
extraction call (a longer prompt, ``max_tokens`` 256 -> 640). That is not "byte-identical" - a 4B asked for a different JSON shape
returns different FACTS and words them differently: "My friend Dana has two kids, Mika and Biscuit" began to store a "Dana
Whitfield has a child named Biscuit" row the pet correction then lost a race to (bar S21), and "I live in Hobart now" came back
worded so the regex fragment beside it out-deduped it (bar S24). The research PR never measured the extractor's own accuracy
with the claim rows asked for.

So in ``shadow`` the extraction is the legacy call, byte for byte, and THIS module reads the claim row afterwards, from the
owner's words and the fact sentences the extraction already stored. It cannot change a stored fact, a class or a status: its output
is a measurement (``STRUCTURAL_FLOOR`` lines, the would-retire log, the off-path verifier's queue). ``enforce`` - where the claim
row decides at write time - still asks in the extraction call (``structural_claims.inline``).

Bounded, like ``structural_verifier``: at most ``MAX_FACTS`` facts per turn, ONE call per digest, a short timeout, ``MAX_PER_HOUR``
per process, serial, and a circuit breaker after ``BREAKER_AFTER`` consecutive failures. The brain has ONE slot, so it is never
asked on a turn that stored nothing.

``ZOE_STRUCTURAL_READER`` = ``shadow`` (default; only while ``ZOE_STRUCTURAL_CLAIMS`` is ``shadow``) | ``off``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import Counter, deque
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

ENV = "ZOE_STRUCTURAL_READER"
MAX_FACTS = 4
MAX_TOKENS = 360              # ~70 tokens a row at MAX_FACTS rows, ~6 s at the brain's measured decode rate
TIMEOUT_S = 12.0
MAX_PER_HOUR = 120
BREAKER_AFTER = 3
BREAKER_S = 300.0
_SAID_MAX = 600
_FACT_MAX = 200

STATS: Counter = Counter()
_calls: deque = deque()
_fail = 0
_open_until = 0.0
_lock: Optional[asyncio.Lock] = None

SYSTEM = "You are a precise fact reader. Return ONLY valid JSON."


def mode() -> str:
    """``shadow`` (default) | ``off``. Per-call env read; only a falsy word turns it off."""
    from typed_env import env_str

    raw = (env_str("ZOE_STRUCTURAL_READER", "shadow") or "shadow").strip().lower()
    return "off" if raw in ("0", "false", "no", "off", "disabled") else "shadow"


def enabled() -> bool:
    import structural_claims as sc

    return sc.post_read() and mode() != "off"


def reset() -> None:
    """Test hook: forget the rate window, the breaker and the counters."""
    global _fail, _open_until
    _calls.clear()
    _fail = 0
    _open_until = 0.0
    STATS.clear()


def build_prompt(said: str, facts: Sequence[str]) -> str:
    """The reader's user message: the owner's words, the numbered stored facts, the shared field definitions."""
    import structural_claims as sc

    numbered = "\n".join(f"{i}. {(f or '')[:_FACT_MAX]}" for i, f in enumerate(facts, 1))
    return (
        f'A person said (exact words):\n"{(said or "")[:_SAID_MAX]}"\n\n'
        f"Facts a note-taker stored from it:\n{numbered}\n\n"
        "For EACH numbered fact, read the same fact as a structured row, using ONLY what the person said:\n"
        + sc.CLAIM_FIELD_RULES
        + 'If the person did not actually state a fact (the note-taker guessed it), give that fact "quote": "".\n\n'
        'Return ONLY a JSON array, one object per fact, in order. Each object has "i" (the fact number) and the fields above.\n'
    )


def parse_reply(raw: str, n: int) -> list:
    """``n`` claims (a ``structural_claims.Claim`` or None each) from the model's reply. Tolerant of prose around the array and of a
    reply cut mid-array (every COMPLETE object counts); an object lands on the fact its ``i`` names, else on its position."""
    import structural_claims as sc

    items: list = []
    text = raw or ""
    start, end = text.find("["), text.rfind("]") + 1
    if start != -1 and end > start:
        try:
            loaded = json.loads(text[start:end])
            items = [x for x in loaded if isinstance(x, dict)] if isinstance(loaded, list) else []
        except (json.JSONDecodeError, ValueError):
            items = []
    if not items and start != -1:
        from memory_digest import _salvage_items

        items = _salvage_items(text[start:])
    out: list = [None] * n
    for pos, item in enumerate(items):
        try:
            idx = int(item.get("i")) - 1
        except (TypeError, ValueError):
            idx = pos
        if not 0 <= idx < n or out[idx] is not None:
            continue
        claim, _why = sc.parse_claim(item)
        out[idx] = claim
    return out


def _budget_ok(now: float) -> bool:
    while _calls and now - _calls[0] > 3600:
        _calls.popleft()
    return len(_calls) < MAX_PER_HOUR


def breaker_open(now: Optional[float] = None) -> bool:
    return (now if now is not None else time.monotonic()) < _open_until


async def _call(said: str, facts: Sequence[str]) -> str:
    import httpx
    from memory_digest import _GEMMA_URL

    payload = {
        "model": os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": build_prompt(said, facts)}],
        "max_tokens": MAX_TOKENS, "temperature": 0.1, "stream": False, "cache_prompt": True,
    }
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        resp = await client.post(f"{_GEMMA_URL}/v1/chat/completions", json=payload)
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"] or "").strip()


async def read_claims(said: str, facts: Sequence[str], *, lane: str = "turn_digest") -> list:
    """The claim row of each of the first ``MAX_FACTS`` ``facts`` the owner's turn ``said`` produced: ``[Claim | None]`` aligned with
    ``facts`` (everything past ``MAX_FACTS`` is None). ONE bounded call; ``[None] * len(facts)`` when off, over budget, the breaker
    is open or the call fails. Never raises."""
    global _fail, _open_until, _lock
    blank = [None] * len(facts)
    try:
        if not enabled() or not facts or not (said or "").strip():
            return blank
        from date_locale import normalize_numeric_dates

        use = list(facts[:MAX_FACTS])
        if _lock is None:
            _lock = asyncio.Lock()
        async with _lock:
            now = time.monotonic()
            if breaker_open(now):
                STATS["skipped_breaker"] += 1
                return blank
            if not _budget_ok(now):
                STATS["skipped_budget"] += 1
                return blank
            _calls.append(now)
            t0 = time.monotonic()
            try:
                raw = await _call(normalize_numeric_dates(said), use)
                _fail = 0
            except Exception as exc:  # noqa: BLE001
                _fail += 1
                STATS["error"] += 1
                if _fail >= BREAKER_AFTER:
                    _open_until = time.monotonic() + BREAKER_S
                    logger.warning("STRUCTURAL_READ breaker open for %ds after %d failures (%s)", int(BREAKER_S), _fail,
                                   type(exc).__name__)
                    _fail = 0
                return blank
        claims = parse_reply(raw, len(use))
        STATS["ok"] += 1
        logger.info("STRUCTURAL_READ lane=%s facts=%d with_claim=%d ms=%d max_tokens=%d", lane, len(use),
                    sum(1 for c in claims if c is not None), int((time.monotonic() - t0) * 1000), MAX_TOKENS)
        return claims + [None] * (len(facts) - len(claims))
    except Exception as exc:  # noqa: BLE001 - a measurement never breaks the digest
        logger.debug("structural reader skipped: %s", type(exc).__name__)
        return blank
