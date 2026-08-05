"""Tavily FREE tier — primary search, hard-capped at the free allowance.

Operator decision 2026-08-03: **free-only, no PAYGO.** Tavily's free plan is
1,000 credits/month with no card on file; overage does not bill, it fails. So
the ceiling is real and must be enforced client-side rather than discovered.

    1000 credits / 30 days ≈ 33 searches per day

`ZOE_TAVILY_DAILY_BUDGET` (default 33) is a **local** counter — Tavily's API
does not return remaining quota, so this cannot be authoritative. It is a
spend-limiter, not an accountant: it stops us burning the month in an
afternoon. `budget_state()` reports usage so the eval harness can show it.

zoe-data already has a Tavily client (`services/zoe-data/web_search_provider.py`,
`tavily_search_sync`); this module is the LAB stand-in so the eval harness can
score the tier. On promotion the budget guard moves into that module and this
one is deleted — there must not be two Tavily clients.

CORRECTION 2026-08-03: an earlier note here claimed no `TAVILY_API_KEY` was
configured on this box. That was WRONG — the key is in
`services/zoe-data/.env`, and the tier was recorded `unconfigured` only because
the harness was run without sourcing it. Both Tavily combos are now measured
(`eval/results/20260803T134932Z.md`). Source the key into the run environment;
never print, commit, or write it to a results file.

The `unconfigured` path still matters and is still tested: an unconfigured tier
and a failing tier are different findings, and neither is silently scored zero.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
from dataclasses import dataclass

import httpx

from .engines import Result

API_URL = "https://api.tavily.com/search"
DEFAULT_DAILY_BUDGET = 33
# Lab-local counter; deliberately outside the repo.
BUDGET_FILE = pathlib.Path.home() / ".zoe" / "web-search-spike-tavily-budget.json"


class TavilyUnconfigured(RuntimeError):
    """No API key. Distinct from a failure — the tier was never asked."""


class TavilyBudgetExhausted(RuntimeError):
    """The local daily spend limit is reached. Distinct from an API error."""


@dataclass(slots=True)
class BudgetState:
    day: str
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def daily_budget() -> int:
    raw = os.environ.get("ZOE_TAVILY_DAILY_BUDGET", "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_DAILY_BUDGET


def budget_state(path: pathlib.Path | None = None) -> BudgetState:
    """Read today's local usage. Resets implicitly when the UTC date rolls."""
    store = path or BUDGET_FILE
    day = _today()
    try:
        data = json.loads(store.read_text())
        if data.get("day") == day:
            return BudgetState(day=day, used=int(data.get("used", 0)), limit=daily_budget())
    except (OSError, ValueError):
        pass
    return BudgetState(day=day, used=0, limit=daily_budget())


def _record_spend(path: pathlib.Path | None = None) -> BudgetState:
    store = path or BUDGET_FILE
    state = budget_state(store)
    state.used += 1
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps({"day": state.day, "used": state.used}))
    except OSError:
        pass  # a lab counter that cannot persist must not break the lookup
    return state


def api_key() -> str:
    return os.environ.get("TAVILY_API_KEY", "").strip()


def configured() -> bool:
    return bool(api_key())


def search(
    query: str,
    *,
    limit: int = 6,
    timeout: float = 10.0,
    budget_path: pathlib.Path | None = None,
    client: httpx.Client | None = None,
) -> list[Result]:
    """One Tavily free-tier search. Raises rather than returning [] on refusal."""
    key = api_key()
    if not key:
        raise TavilyUnconfigured("TAVILY_API_KEY is not set")

    state = budget_state(budget_path)
    if state.remaining <= 0:
        raise TavilyBudgetExhausted(f"local daily budget spent ({state.used}/{state.limit})")

    owned = client or httpx.Client(timeout=timeout, trust_env=False)
    try:
        resp = owned.post(
            API_URL,
            json={
                "api_key": key, "query": query, "max_results": limit,
                # 'basic' is 1 credit; 'advanced' is 2. Free-only => always basic.
                "search_depth": "basic",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if client is None:
            owned.close()

    _record_spend(budget_path)
    _maybe_capture(query, payload)
    return parse_tavily(payload)


def _maybe_capture(query: str, payload: dict) -> None:
    """Opt-in raw-RESPONSE capture, so fixtures can be recorded from real calls.

    Enabled by setting ``ZOE_TAVILY_CAPTURE_DIR``. This exists because a
    hand-written fixture cannot prove the parser matches production — the old
    `tests/fixtures/tavily_response.json` was synthetic and said so.

    SECURITY: captures the RESPONSE ONLY. The request body carries `api_key`
    and is never written. Tavily's response does not echo the key back;
    `test_capture_writes_response_only` pins that no captured file contains it.
    """
    target = os.environ.get("ZOE_TAVILY_CAPTURE_DIR", "").strip()
    if not target:
        return
    try:
        directory = pathlib.Path(target)
        directory.mkdir(parents=True, exist_ok=True)
        slug = "".join(c if c.isalnum() else "-" for c in query.lower())[:60].strip("-")
        (directory / f"{slug}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass  # a capture that cannot persist must not break the lookup


def parse_tavily(payload: dict) -> list[Result]:
    """Pure parser — the offline test seam."""
    out: list[Result] = []
    for rank, row in enumerate(payload.get("results") or []):
        url = str(row.get("url") or "").strip()
        title = str(row.get("title") or "").strip()
        if not url or not title:
            continue
        out.append(
            Result(
                title=title, url=url,
                snippet=str(row.get("content") or "").strip(),
                engine="tavily-free", rank=rank,
                extra={"score": row.get("score")},
            )
        )
    return out
