"""Channel-agnostic deterministic turn core: Tier-0 → Tier-1 → Tier-1.5.

Every channel — web chat, voice (panel + Jabra), LiveKit, Telegram — calls
`resolve()` to get a sub-second answer (a `DispatchResult`) or `None` (→ the
caller's brain lane). This is the hexagonal "domain core": it depends only on the
tier modules (`intent_router`, `semantic_router`, `expert_dispatch`), never on a
channel's I/O. Channels pass a `channel` tag whose profile sets the per-channel
knobs (run_tier0 / allow_writes); explicit kwargs always override the profile.

Design notes (see docs/architecture/stage-a-channel-agnostic-core.md):
  - Tier-0 is a deterministic regex *read* shortcut: only idempotent read intents
    whose `execute_intent` returns finished text short-circuit here. Writes / forms
    / panel / delegation return None so the channel's own handlers (or the brain)
    own them.
  - The ambiguity *margin check* defers to the brain when the top two routed
    domains are within a small margin — a standard semantic-router safeguard.
  - `fast_path.resolve` is a back-compat shim re-exporting `resolve`.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Read intents whose `execute_intent` returns a finished spoken/printed string and
# are idempotent (safe to short-circuit at Tier-0). Writes / forms / panel /
# delegation are intentionally excluded — they need richer channel handling.
_TIER0_READ_INTENTS = frozenset({
    "time_query", "date_query", "list_show", "calendar_show",
    "reminder_list", "weather", "timer_status",
})

# Pretty domain label for a Tier-0 intent (metadata only).
_TIER0_DOMAIN = {
    "time_query": "time", "date_query": "time", "list_show": "lists",
    "calendar_show": "calendar", "reminder_list": "reminders",
    "weather": "weather", "timer_status": "timers",
}

# Per-channel profiles — the "tag → profile" model. Explicit resolve() kwargs win.
# voice keeps run_tier0=False because it has its own richer public-intent Tier-0
# (policy/scope gates) ahead of this call; folding that in is a later, replay-gated
# step. chat/telegram run the shared Tier-0 read shortcut.
# `defer_domains` lists domains that this channel must NOT fast-path — they fall
# straight to the brain. voice defers people/memory: on-device that path was both
# slow (2-4s recall/store) and wrong (it mis-stored recall *questions* as facts),
# so the brain — now given the user's facts + portrait — owns recall and chat.
CHANNEL_PROFILES: dict[str, dict[str, Any]] = {
    "chat":     {"run_tier0": True,  "allow_writes": False},
    "voice":    {"run_tier0": False, "allow_writes": True, "defer_domains": {"people", "memory"}},
    "livekit":  {"run_tier0": True,  "allow_writes": True},
    "telegram": {"run_tier0": True,  "allow_writes": True},
}


def profile_for(channel: Optional[str]) -> dict[str, Any]:
    """Return a copy of the named channel's profile, or {} if unknown."""
    return dict(CHANNEL_PROFILES.get((channel or "").strip().lower(), {}))


def _router_margin() -> float:
    """Min gap between the top two routed domains; below it = ambiguous → brain.

    Tunable via ZOE_ROUTER_MARGIN. 0 disables the check (pure threshold routing).
    """
    try:
        return float(os.environ.get("ZOE_ROUTER_MARGIN", "0.05"))
    except Exception:
        return 0.05


async def _tier0(text: str, user_id: str):
    """Deterministic regex read shortcut. Returns a `DispatchResult` or `None`."""
    try:
        from intent_router import detect_intent, execute_intent

        intent = detect_intent(text, log_miss=False)
        if not intent or intent.name not in _TIER0_READ_INTENTS:
            return None
        # A `raw` slot means the intent still needs slot extraction we don't do at
        # Tier-0 — defer rather than execute a half-formed intent.
        if "raw" in (getattr(intent, "slots", None) or {}):
            return None
        reply = await execute_intent(intent, user_id)
        reply = (reply or "").strip()
        if not reply:
            return None
        import expert_dispatch as _xd

        return _xd.DispatchResult(
            domain=_TIER0_DOMAIN.get(intent.name, intent.name),
            reply=reply,
            intent=intent.name,
            tier="tier0",
        )
    except Exception as exc:  # never let the shortcut break a turn
        logger.warning("fast_tiers tier0 failed (non-fatal): %s", exc)
        return None


async def resolve(
    text: str,
    user_id: str,
    session_id: str,
    *,
    channel: Optional[str] = None,
    router_decision: Optional[dict] = None,
    extra_ctx: Optional[dict] = None,
    allow_writes: Optional[bool] = None,
    run_tier0: Optional[bool] = None,
):
    """Run the deterministic tiers (Tier-0 → Tier-1 → Tier-1.5) for `text`.

    Returns a `DispatchResult` (`.reply`, `.domain`, `.intent`, `.ui`, `.tier`) or
    `None` when nothing confident matches (caller should fall to its brain lane).
    Never raises — any internal error returns `None` (a turn is never broken).

    `channel` selects a profile from CHANNEL_PROFILES (run_tier0 / allow_writes);
    explicit `allow_writes` / `run_tier0` kwargs override it. Pass a precomputed
    `router_decision` (from `semantic_router.route`) to avoid re-embedding.
    `extra_ctx` is merged into the dispatch context (e.g. voice passes db/panel_id).
    `allow_writes=False` keeps the read/recall fast path but defers WRITE intents.
    """
    prof = profile_for(channel)
    if allow_writes is None:
        allow_writes = bool(prof.get("allow_writes", True))
    if run_tier0 is None:
        run_tier0 = bool(prof.get("run_tier0", False))
    try:
        import expert_dispatch as _xd

        if not _xd.is_enabled():
            return None

        # Tier-0 — deterministic regex read shortcut (opt-in per channel).
        if run_tier0:
            t0 = await _tier0(text, user_id)
            if t0 is not None:
                return t0

        # Tier-1 — embedding router (unless the caller precomputed a decision).
        rr = router_decision
        if rr is None:
            import semantic_router as _sr

            # Respect the router's own enable flag — if off, fall to the brain
            # rather than silently embedding anyway.
            if not _sr.is_enabled():
                return None
            rr = _sr.route(text)
        domain = rr.get("domain") if rr else None
        if domain in (None, "chat"):
            return None

        # Channel-level defer list: this domain is intentionally not fast-pathed on
        # this channel (e.g. voice defers people/memory to the brain). Skip Tier-1.5.
        if domain in prof.get("defer_domains", ()):  # type: ignore[arg-type]
            logger.info("fast_tiers defer domain=%s (channel=%s) → brain", domain, channel)
            return None

        # Ambiguity margin — if the top two domains are within MARGIN, treat as
        # ambiguous and defer to the brain rather than guessing a domain.
        margin = _router_margin()
        if margin > 0:
            scores = rr.get("scores") or {}
            if isinstance(scores, dict) and len(scores) >= 2:
                top = sorted((float(v) for v in scores.values()), reverse=True)
                if (top[0] - top[1]) < margin:
                    logger.info(
                        "fast_tiers ambiguous (margin %.3f < %.2f) → brain",
                        top[0] - top[1], margin,
                    )
                    return None

        # Tier-1.5 — domain-expert dispatch. Base fields are authoritative: build
        # ctx from extra_ctx FIRST, then set user_id/session_id/score so a caller's
        # extra_ctx can never silently overwrite them.
        ctx: dict[str, Any] = dict(extra_ctx or {})
        ctx.update({
            "user_id": user_id,
            "session_id": session_id,
            "score": float(rr.get("score") or 0.0),
        })
        res = await _xd.dispatch(domain, text, ctx, write_ok=allow_writes)
        if res is not None and not getattr(res, "tier", ""):
            try:
                res.tier = "tier1.5"
            except Exception:
                pass
        return res
    except Exception as exc:  # never let the fast path break a turn
        logger.warning("fast_tiers.resolve failed (non-fatal): %s", exc)
        return None


# `TurnOutcome` is the channel-neutral alias for the core's return type so callers
# can read either name (see plan §3.2). The field set is unchanged (`.reply` etc).
try:  # pragma: no cover - import-time alias
    from expert_dispatch import DispatchResult as TurnOutcome  # noqa: F401
except Exception:  # pragma: no cover
    TurnOutcome = None  # type: ignore
