"""Channel-agnostic Tier-1/1.5 fast path: embedding router → expert dispatch.

Voice, chat, and any future channel bridge (Telegram, etc.) call `resolve()` to
get a sub-second answer for calendar / lists / weather / time / people / memory
WITHOUT going to the LLM brain. It returns a `DispatchResult` (reply + domain +
intent + ui) or `None` (→ the caller falls through to the brain).

This is the single shared implementation, so every channel inherits the exact
same fast path; the channel layer only *renders* the result (TTS for voice, text
for chat/Telegram). Keeping this here — and free of any heavy framework — is the
"fast-path stays independent" guardrail: a channel/brain/harness outage never
takes the sub-second tiers down.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def resolve(
    text: str,
    user_id: str,
    session_id: str,
    *,
    router_decision: Optional[dict] = None,
    extra_ctx: Optional[dict] = None,
):
    """Run the embedding router + Tier-1.5 expert dispatch for `text`.

    Returns the expert `DispatchResult` (has `.reply`, `.domain`, `.intent`, `.ui`)
    or `None` when nothing confident matches (caller should fall to the brain).

    Pass a precomputed `router_decision` (from `semantic_router.route`) to avoid
    re-embedding when the caller already routed for its own shadow logging.
    `extra_ctx` is merged into the dispatch context (e.g. voice passes db/panel_id)
    so callers can keep their existing context shape byte-for-byte.
    """
    try:
        import expert_dispatch as _xd

        if not _xd.is_enabled():
            return None
        rr = router_decision
        if rr is None:
            import semantic_router as _sr

            rr = _sr.route(text)
        domain = rr.get("domain") if rr else None
        if domain in (None, "chat"):
            return None
        ctx: dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "score": float(rr.get("score") or 0.0),
        }
        if extra_ctx:
            ctx.update(extra_ctx)
        return await _xd.dispatch(domain, text, ctx)
    except Exception as exc:  # never let the fast path break a turn
        logger.warning("fast_path.resolve failed (non-fatal): %s", exc)
        return None
