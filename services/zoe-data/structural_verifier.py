"""The OFF-PATH yes/no verifier for the ambiguous residue of the structural floors (ZOE_STRUCTURAL_VERIFIER, shadow only).

What it is: after a turn has been answered, spoken and stored, the digest hands the facts whose two floors disagreed (or
whose claim row tripped a lexicon pre-filter) to the live 4B as a CONSTRAINED yes/no judge - ``grammar: root ::= "yes" |
"no"``, two output tokens, ``temperature 0`` - and LOGS the verdict beside both floors' decisions. That is all it does.

* It never decides anything in this PR: no row's class, status or retirement reads the verdict. It is the measurement that
  says how often the structural floor and an independent reader disagree, per language, so the next step (the verifier as an
  additional promoter where the lexical floor is silent - M4 in the research note) can be taken on evidence.
* It is never on the voice hot path: it is awaited only from the post-turn digest (``memory_digest._structural_post``), which
  already runs after the reply. The brain has ONE slot (``--parallel 1``); a 4B judge call is ~280 ms p50 and holds it, so the
  caller is bounded here: at most ``MAX_PER_TURN`` calls per digest, ``MAX_PER_HOUR`` per process, a short timeout, serial,
  and a circuit breaker that stops asking for ``BREAKER_S`` seconds after ``BREAKER_AFTER`` consecutive failures.
* Measured by the research (2026-10-09, n=137 on the labelled set): balanced accuracy 0.88 / 0.89 / 0.82 on ledger / held-out
  English / es+zh+ja, 280 ms p50, 4 false promotions in 86 negatives.

``ZOE_STRUCTURAL_VERIFIER`` = ``shadow`` (default; only while ``ZOE_STRUCTURAL_CLAIMS`` is not ``off``) | ``off``.
Labels and counts only are logged - never the fact or the owner's words.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_STRUCTURAL_VERIFIER"
GRAMMAR = 'root ::= "yes" | "no"'
MAX_PER_TURN = 2
MAX_PER_HOUR = 60
TIMEOUT_S = 3.0
BREAKER_AFTER = 3
BREAKER_S = 300.0
_SAID_MAX = 400

SYSTEM = "You judge whether a person's own words state a fact about them. Answer yes or no."
USER = ('Person said: "{said}"\nStored fact: "{fact}"\n'
        'Do the person\'s words, taken at face value, plainly state this fact about the person themselves '
        '(same subject, same polarity - not a denial, a wish, a guess, a question, a past state or someone else\'s fact)? '
        "Answer yes or no.")

STATS: Counter = Counter()
_calls: deque = deque()
_fail = 0
_open_until = 0.0
_lock: Optional[asyncio.Lock] = None


@dataclass(frozen=True)
class Item:
    fact: str
    quote: str
    lexical: str
    structural: str
    lang: str = ""
    ambiguous: tuple = ()
    said: str = ""          # the owner's whole sentence (the judge reads what was said, not the extractor's cut)


def mode() -> str:
    """``shadow`` (default) | ``off``. Per-call env read; only the word ``off`` (or a falsy value) turns it off."""
    raw = (os.environ.get("ZOE_STRUCTURAL_VERIFIER") or "shadow").strip().lower()
    return "off" if raw in ("0", "false", "no", "off", "disabled") else "shadow"


def _budget_ok(now: float) -> bool:
    while _calls and now - _calls[0] > 3600:
        _calls.popleft()
    return len(_calls) < MAX_PER_HOUR


def breaker_open(now: Optional[float] = None) -> bool:
    return (now if now is not None else time.monotonic()) < _open_until


def reset() -> None:
    """Test hook: forget the rate window and the breaker."""
    global _fail, _open_until
    _calls.clear()
    _fail = 0
    _open_until = 0.0
    STATS.clear()


async def judge(fact: str, said: str, *, url: Optional[str] = None, model: Optional[str] = None,
                timeout: float = TIMEOUT_S) -> Optional[str]:
    """ONE constrained yes/no call. Returns ``"yes"`` | ``"no"`` or None (error / unparseable). Does not enforce the
    budget - ``verify_post_turn`` does; this is also what the bounded live check drives."""
    import httpx

    if url is None:
        from memory_digest import _GEMMA_URL

        url = _GEMMA_URL
    payload = {
        "model": model or os.environ.get("MEMORY_DIGEST_MODEL", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"),
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": USER.format(said=(said or "")[:_SAID_MAX].replace('"', "'"),
                                                              fact=(fact or "")[:200].replace('"', "'"))}],
        "max_tokens": 2, "temperature": 0, "stream": False, "grammar": GRAMMAR, "cache_prompt": True,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{url}/v1/chat/completions", json=payload)
        resp.raise_for_status()
        text = str(resp.json()["choices"][0]["message"]["content"] or "").strip().lower()
    return text if text in ("yes", "no") else None


async def verify_post_turn(items: list[Item], *, user_id: str = "") -> list[tuple[Item, Optional[str]]]:
    """Judge up to ``MAX_PER_TURN`` of ``items`` (serially, bounded) and log each verdict. Returns ``[(item, verdict)]``.
    Never raises; returns ``[]`` when off, over budget or the breaker is open."""
    global _fail, _open_until, _lock
    out: list[tuple[Item, Optional[str]]] = []
    try:
        import structural_claims as sc

        if mode() == "off" or not sc.active() or not items:
            return out
        if _lock is None:
            _lock = asyncio.Lock()
        async with _lock:
            for it in items[:MAX_PER_TURN]:
                now = time.monotonic()
                if breaker_open(now):
                    STATS["skipped_breaker"] += 1
                    break
                if not _budget_ok(now):
                    STATS["skipped_budget"] += 1
                    break
                _calls.append(now)
                t0 = time.monotonic()
                verdict: Optional[str] = None
                try:
                    verdict = await judge(it.fact, it.said or it.quote)
                    _fail = 0
                except Exception as exc:  # noqa: BLE001
                    _fail += 1
                    if _fail >= BREAKER_AFTER:
                        _open_until = time.monotonic() + BREAKER_S
                        logger.warning("STRUCTURAL_VERIFY breaker open for %ds after %d failures (%s)",
                                       int(BREAKER_S), _fail, type(exc).__name__)
                        _fail = 0
                ms = int((time.monotonic() - t0) * 1000)
                says_holds = it.structural in ("promote", "anchor")
                agrees = None if verdict is None else ((verdict == "yes") == says_holds)
                STATS[(verdict or "error", it.structural, it.lang or "-")] += 1
                logger.info("STRUCTURAL_VERIFY verdict=%s lexical=%s structural=%s agrees_structural=%s lang=%s ms=%d ambiguous=%s",
                            verdict or "error", it.lexical or "-", it.structural, "-" if agrees is None else int(agrees),
                            it.lang or "-", ms, ",".join(it.ambiguous) or "-")
                out.append((it, verdict))
    except Exception as exc:  # noqa: BLE001 - a measurement never breaks the digest
        logger.debug("structural verifier skipped: %s", type(exc).__name__)
    return out
