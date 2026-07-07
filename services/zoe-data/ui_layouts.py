"""ui_layouts — layout memory for the composed UI (the interface EVOLVES).

Persists, per (user, intent-family), the most recently *successfully composed*
component tree (``ui_layouts`` table, migration 0016) so later compositions for
similar requests converge on a consistent layout instead of being amnesiac.

v1 semantics — **layout-as-few-shot**: a stored tree is NEVER rendered or
re-bound directly (its text content is stale the moment it is saved). The sole
consumer, ``ui_compose.compose_card``, injects it into the compose prompt as a
structural hint ("Previously, a good layout for a similar request was: …
Prefer this structure, updated with the new content."), so layouts converge
per user+intent over time without ever showing stale data.

Flag: ``ZOE_LAYOUT_MEMORY`` — default **ON** when unset. The feature is only
reachable when ``ZOE_COMPOSE_UI`` is on (compose_card is the only caller), so
the default-on flag is a no-op for compose-off deployments. Set
``ZOE_LAYOUT_MEMORY=0`` to disable layout memory while keeping composition.

Failure semantics: layout memory must NEVER break a turn. Every helper here is
exception-safe — reads return ``None`` on any failure, writes are best-effort
fire-and-forget and never raise.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from db_pool import get_db_ctx

logger = logging.getLogger(__name__)

# Words that carry no layout-shaping intent. Deliberately small and boring:
# the goal is a cheap, deterministic bucket, not NLU. Includes common openers
# (please/hey/zoe), auxiliaries, pronouns, prepositions, and near-term time
# words (today/tonight/tomorrow) — "weather today" and "weather tomorrow" want
# the same layout.
_STOPWORDS = frozenset(
    """
    a an the is are was were be been being am do does did done have has had
    can could will would shall should may might must
    i me my mine you your yours we our ours us it its they them their he she
    his her him
    this that these those there here
    what whats who whos whom when where which how why
    and or but not no nor so then than too very really quite just some any
    of in on at to for with from by about as into onto over under up down out
    off again also please hey hi hello ok okay yeah thanks thank
    zoe tell show give get me want need like
    now today tonight tomorrow yesterday currently
    """.split()
)
_MAX_FAMILY_TOKENS = 6


def layout_memory_enabled() -> bool:
    """ZOE_LAYOUT_MEMORY flag — default ON when unset (see module docstring)."""
    val = os.environ.get("ZOE_LAYOUT_MEMORY", "").strip().lower()
    if val in ("0", "false", "no", "off"):
        return False
    return True


def intent_family_for(user_message: str) -> str:
    """Cheap deterministic intent bucket for a user message. No LLM.

    Normalise (lowercase, drop apostrophes, strip digits + punctuation), drop
    stopwords, keep the first ~6 salient tokens, join with spaces. Falls back
    to ``"general"`` when nothing salient survives.
    """
    text = (user_message or "").lower().replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z]+", " ", text)  # strips digits AND punctuation
    tokens = [t for t in text.split() if t not in _STOPWORDS]
    return " ".join(tokens[:_MAX_FAMILY_TOKENS]) or "general"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_layout(user_id: str, family: str) -> Optional[dict[str, Any]]:
    """Return the stored tree for (user, family), or None. Never raises."""
    if not user_id or not family:
        return None
    try:
        async with get_db_ctx() as db:
            row = await db.fetchrow(
                "SELECT tree FROM ui_layouts WHERE user_id = $1 AND intent_family = $2",
                user_id, family,
            )
        if not row:
            return None
        tree = json.loads(row["tree"])
        return tree if isinstance(tree, dict) else None
    except Exception as exc:  # noqa: BLE001 — layout memory never breaks a turn
        logger.info("ui_layouts.get_layout skipped (non-fatal): %s", exc)
        return None


async def save_layout(user_id: str, family: str, tree: dict[str, Any]) -> None:
    """Upsert the layout for (user, family): replace tree, bump uses, refresh
    last_used_at. Best-effort; never raises."""
    if not user_id or not family or not tree:
        return
    try:
        now = _now_iso()
        async with get_db_ctx() as db:
            await db.execute(
                """
                INSERT INTO ui_layouts (id, user_id, intent_family, tree, uses, last_used_at, created_at)
                VALUES ($1, $2, $3, $4, 1, $5, $5)
                ON CONFLICT (user_id, intent_family) DO UPDATE SET
                    tree = EXCLUDED.tree,
                    uses = ui_layouts.uses + 1,
                    last_used_at = EXCLUDED.last_used_at
                """,
                uuid.uuid4().hex, user_id, family,
                json.dumps(tree, separators=(",", ":")), now,
            )
    except Exception as exc:  # noqa: BLE001
        logger.info("ui_layouts.save_layout skipped (non-fatal): %s", exc)


async def touch(user_id: str, family: str) -> None:
    """Bump uses + last_used_at for (user, family) without replacing the tree.
    Best-effort; never raises."""
    if not user_id or not family:
        return
    try:
        async with get_db_ctx() as db:
            await db.execute(
                "UPDATE ui_layouts SET uses = uses + 1, last_used_at = $3 "
                "WHERE user_id = $1 AND intent_family = $2",
                user_id, family, _now_iso(),
            )
    except Exception as exc:  # noqa: BLE001
        logger.info("ui_layouts.touch skipped (non-fatal): %s", exc)
