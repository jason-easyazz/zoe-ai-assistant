"""ambient_briefing — the composed idle briefing for the touch panel.

When the Skybridge panel rests on the ambient clock, it can show one modest
composed card ("Good morning — 2 events today, milk + eggs on the list, 19°
clear") built from Zoe's REAL data. Facts come from the deterministic
skybridge resolvers (calendar / shopping list / weather ``spoken_summary``
strings — cheap, no LLM). Composition via ``ui_compose.compose_card`` takes
5–10s on the local brain, so it NEVER runs on the request path:

  - ``get_briefing_card(user_id)`` answers from a module-level cache
    immediately — the fresh card, the stale card, or ``None`` the first time —
    and, when the cache is stale/missing, kicks a fire-and-forget refresh task.
  - TTL via ``ZOE_BRIEFING_TTL_S`` (seconds, default 900).
  - When composition is unavailable (flag off / brain down) the refresh falls
    back to a STATIC tree built directly from the facts and validated through
    ``ui_catalog.validate_component_tree`` — the feature works without the LLM.

Guest safety: the resolvers already enforce guest visibility; auth-required
results are skipped as facts (a resting screen must never nag to sign in).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any, Optional

import ui_compose
from card_contract import CardContractError
from skybridge_service import resolve_skybridge_request
from ui_catalog import validate_component_tree

logger = logging.getLogger(__name__)

# The three deterministic fact sources (each yields a spoken_summary string).
FACT_QUERIES: tuple[str, ...] = (
    "show my calendar today",
    "show my shopping list",
    "what is the weather",
)

_COMPOSE_REQUEST = "compose an ambient briefing card"
_MAX_FACT_CHARS = 220   # keep each ListRow modest and the tree far under budget

# user_id -> (card-or-None, monotonic timestamp of the refresh that produced it)
_cache: dict[str, tuple[Optional[dict[str, Any]], float]] = {}
_refreshing: set[str] = set()
_tasks: set[asyncio.Task] = set()   # strong refs so fire-and-forget tasks aren't GC'd


def briefing_ttl_s() -> float:
    try:
        return float(os.environ.get("ZOE_BRIEFING_TTL_S", "900"))
    except ValueError:
        return 900.0


def greeting_for_hour(hour: int) -> str:
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    if 17 <= hour < 22:
        return "Good evening"
    return "Hello"


async def gather_facts(user_id: str) -> list[str]:
    """Resolve the deterministic fact queries; failures and auth-nags are skipped."""
    facts: list[str] = []
    for query in FACT_QUERIES:
        try:
            result = await resolve_skybridge_request(query, user_id)
        except Exception as exc:  # noqa: BLE001 — one broken resolver must not kill the briefing
            logger.info("briefing fact %r skipped (non-fatal): %s", query, exc)
            continue
        if not isinstance(result, dict) or not result.get("handled") or result.get("auth_required"):
            continue
        summary = str(result.get("spoken_summary") or "").strip()
        if summary:
            facts.append(summary[:_MAX_FACT_CHARS])
    return facts


def build_static_card(greeting: str, facts: list[str]) -> Optional[dict[str, Any]]:
    """LLM-free fallback: Stack[Text kicker greeting, ListRow per fact], validated."""
    if not facts:
        return None
    children: list[dict[str, Any]] = [
        {"component": "Text", "text": greeting, "role": "kicker"},
    ]
    for fact in facts:
        children.append({"component": "ListRow", "title": fact})
    try:
        tree = validate_component_tree({"component": "Stack", "gap": "sm", "children": children})
    except CardContractError as exc:
        logger.warning("briefing static tree rejected (non-fatal): %s", exc)
        return None
    return {"component": "compose", "props": {"tree": tree}}


async def build_briefing_card(user_id: str) -> Optional[dict[str, Any]]:
    """Build a fresh briefing card (slow path — only ever called off-request)."""
    facts = await gather_facts(user_id)
    if not facts:
        return None
    greeting = greeting_for_hour(datetime.now().hour)
    card: Optional[dict[str, Any]] = None
    if ui_compose.compose_enabled():
        facts_text = greeting + ".\n" + "\n".join(f"- {fact}" for fact in facts)
        card = await ui_compose.compose_card(_COMPOSE_REQUEST, facts_text, user_id=user_id)
    if card is None:
        card = build_static_card(greeting, facts)
    return card


async def _refresh(user_id: str) -> None:
    try:
        card = await build_briefing_card(user_id)
        _cache[user_id] = (card, time.monotonic())
    except Exception as exc:  # noqa: BLE001 — refresh is best-effort by design
        logger.warning("briefing refresh failed (non-fatal) for %s: %s", user_id, exc)
    finally:
        _refreshing.discard(user_id)


def _spawn_refresh(user_id: str) -> None:
    _refreshing.add(user_id)
    task = asyncio.get_running_loop().create_task(_refresh(user_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def get_briefing_card(user_id: str) -> Optional[dict[str, Any]]:
    """Cached briefing for ``user_id`` — always instant, never waits on the LLM.

    Returns the cached card (fresh OR stale) immediately; ``None`` until the
    first refresh lands. A stale/missing cache kicks one background refresh.
    """
    cached = _cache.get(user_id)
    fresh = cached is not None and (time.monotonic() - cached[1]) < briefing_ttl_s()
    if not fresh and user_id not in _refreshing:
        _spawn_refresh(user_id)
    return cached[0] if cached else None
