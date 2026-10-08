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

# Intents that must NEVER be answered by the shared Tier-0 read shortcut on the
# VOICE channel: they are user-scoped (personal data) and the voice channel's own
# B3/B4 scope-gate + PIN challenge (`voice_tts.py` _can_use_voice_intent /
# resource="scope_gate") runs AFTER this resolve() call. Short-circuiting them at
# Tier-0 would leak personal data past that gate, so they fall through to the
# brain lane (which runs after the gate). Public/shared reads (weather, time,
# date, lists, calendar) stay eligible — they carry no per-user data and are
# already treated as household-safe by the voice public-intent path.
_VOICE_TIER0_DEFER_INTENTS = frozenset({"reminder_list", "timer_status"})

# Per-channel profiles — the "tag → profile" model. Explicit resolve() kwargs win.
# voice runs the shared Tier-0 read shortcut for the public/idempotent reads
# (weather/time/date/list/calendar) so they answer in ~300ms instead of paying the
# full 2-call brain loop; user-scoped reads are deferred via `tier0_defer_intents`
# so the voice scope gate stays authoritative. chat/telegram run the shared Tier-0.
# `defer_domains` lists domains that this channel must NOT fast-path — they fall
# straight to the brain. voice defers people/memory: on-device that path was both
# slow (2-4s recall/store) and wrong (it mis-stored recall *questions* as facts),
# so the brain — now given the user's facts + portrait — owns recall and chat.
CHANNEL_PROFILES: dict[str, dict[str, Any]] = {
    # identity_tier: own-identity questions ("what's my name", "where do I live") are
    # answered from the ACCOUNT (identity_facts), never recalled from memory. Voice/
    # livekit are deliberately NOT listed: their scope gate runs after resolve() and
    # personal facts must not bypass it (same reason as _VOICE_TIER0_DEFER_INTENTS).
    "chat":     {"run_tier0": True,  "allow_writes": False, "identity_tier": True},
    "voice":    {"run_tier0": True,  "allow_writes": True,
                 "defer_domains": frozenset({"people", "memory"}),
                 "tier0_defer_intents": _VOICE_TIER0_DEFER_INTENTS},
    # livekit's fast tier neither persists the assistant reply nor runs the intent
    # lane's follow-up matchers, so a queued question ("is X the same person?")
    # could never be answered: binds_followups=False tells a `direct` write to
    # state the outcome instead of asking.
    "livekit":  {"run_tier0": True,  "allow_writes": True, "binds_followups": False},
    "telegram": {"run_tier0": True,  "allow_writes": True, "identity_tier": True},
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


# ── The head is the authority over keyword claims ───────────────────────────
# Router-domain class(es) each deterministic KEYWORD intent may stand for. An
# intent absent here has no router class (greetings, panel, engineering, maths,
# …) and is never gated. Sibling domains that genuinely overlap are listed
# together (calendar/reminders, people/memory, notes/memory/journal,
# music/smart_home volume) — the same leniency as the Skybridge gate.
_INTENT_ROUTER_DOMAINS: dict[str, frozenset[str]] = {
    "time_query": frozenset({"time"}), "date_query": frozenset({"time"}),
    "weather": frozenset({"weather"}),
    "list_show": frozenset({"lists"}), "list_add": frozenset({"lists"}),
    "list_remove": frozenset({"lists"}),
    "calendar_show": frozenset({"calendar", "reminders"}),
    "calendar_create": frozenset({"calendar", "reminders"}),
    "reminder_list": frozenset({"reminders", "calendar"}),
    "reminder_create": frozenset({"reminders", "calendar"}),
    "timer_create": frozenset({"timers"}), "timer_status": frozenset({"timers"}),
    "people_search": frozenset({"people", "memory"}),
    "people_create": frozenset({"people", "memory"}),
    "people_introduce": frozenset({"people", "memory"}),
    "memory_remember": frozenset({"memory", "people"}),
    "note_create": frozenset({"notes", "memory", "journal"}),
    "note_search": frozenset({"notes", "memory"}),
    "journal_create": frozenset({"journal", "notes"}),
    "music_play": frozenset({"music"}), "music_control": frozenset({"music"}),
    "music_favorite": frozenset({"music"}),
    "music_volume": frozenset({"music", "smart_home"}),
    "set_volume": frozenset({"music", "smart_home"}),
    "smart_home": frozenset({"smart_home", "music"}),
    "journal_prompt": frozenset({"journal", "notes"}),
    "journal_streak": frozenset({"journal", "notes"}),
    "memory_forget_entity": frozenset({"memory", "people"}),
    "memory_forget_last": frozenset({"memory", "people"}),
    "portrait_reveal": frozenset({"memory", "people"}),
    "portrait_refresh": frozenset({"memory", "people"}),
    "music_setup": frozenset({"music"}),
}

# Detector intents the gate deliberately does NOT consult the head on, and why.
# Every intent the keyword detector can emit must be in exactly one of
# `_INTENT_ROUTER_DOMAINS` or here (pinned by tests/test_intent_router_gate.py) —
# a new detector intent that lands in neither silently bypasses the head.
_INTENT_UNGATED: dict[str, str] = {
    # no router class — the head's 13 labels have nothing to agree with
    "calculate": "maths; no router class",
    "recipe_search": "recipes; no router class",
    "transaction_create": "money; no router class",
    "transaction_summary": "money; no router class",
    "time_planning_clarification": "asks a clarifying question; no router class",
    # conversational micro-replies / the brief: the head labels them `chat`,
    # which cannot tell them apart from a brain turn — gating would only add latency
    "greeting": "conversational micro-reply",
    "acknowledgement": "conversational micro-reply",
    "lets_talk": "conversational micro-reply",
    "good_morning": "morning brief trigger",
    "good_evening": "evening brief trigger",
    "daily_briefing": "brief trigger; spans several domains",
    # replies to an offer Zoe just made: context the head (bare words) cannot see
    "pending_offer_accept": "reply to a pending offer (context follow-up)",
    "pending_offer_dismiss": "reply to a pending offer (context follow-up)",
    "people_same_person_reply": "reply to a same-person question (context follow-up)",
    # operator / system / engineering commands: explicit command syntax, no router class
    "status_check": "system command",
    "self_improve": "system command",
    "extend_capability": "system command",
    "evolution_proposals_review": "system command",
    "user_issue_report": "system command",
    "agent_tasks_status": "system command",
    "board_status": "engineering command",
    "board_heal": "engineering command",
    "build_page": "engineering command",
    "build_widget": "engineering command",
    "engineering_dispatch_pause": "engineering command",
    "engineering_dispatch_resume": "engineering command",
    "engineering_task_create": "engineering command",
    "engineering_task_status": "engineering command",
    "engineering_ticket_list": "engineering command",
    "engineering_ticket_move_todo": "engineering command",
    "engineering_ticket_split": "engineering command",
    "ha_full_setup": "setup flow",
    "panel_setup": "panel command",
    "panel_status": "panel command",
    "panel_list": "panel command",
    "panel_confirm_code": "panel pairing code",
}


def intent_gate_enabled() -> bool:
    """ZOE_INTENT_ROUTER_GATE — default ON; false/0/off/no = the keyword lanes
    ignore the router head again (pre-2026-09-28 behaviour)."""
    raw = (os.environ.get("ZOE_INTENT_ROUTER_GATE", "1") or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def intent_gate_decision(intent_name: str, verdict: Optional[dict]) -> tuple[str, str]:
    """Pure rule: may a deterministic keyword intent answer, given the head's
    stage-1 verdict (router_two_stage.head_verdict shape)? → (decision, reason).

      allow  flag_off / no_router_class / router_unavailable (head off/failed)
             / router_unsure (only `below_gate`: stage 1 under the 0.5 gate on
             a real domain has no opinion — garbled STT recovery)
             / router_agrees (head domain is one of the intent's classes)
      veto   router_chat (chat_top, or the ZOE_ROUTER_HEAD_MIN_CONF `low_conf`
             floor — a deliberate send-to-the-brain verdict)
             / router_disagrees (the head is confident in another domain)
             / evidence_question (semantic_router re-pointed an evidence-
             shaped question: only a memory_* intent may keep it — never a
             people/lists/calendar/… expert, whatever the head's confidence)
             / event_time_question (ZOE_ROUTER_EVENT_TIME_PRECEDENCE: "what
             time is my dentist…" re-pointed off the clock: only a memory_*
             intent or calendar_show may keep it — never time_query)
             / own_fact_question (ZOE_OWN_FACT_PRECEDENCE: "when is my
             birthday" / "what's my address" re-pointed off the clock and
             calendar: only a memory_* or people_* intent may keep it)

    Same rule as the Skybridge router gate (skybridge_router_gate), so every
    deterministic lane shares one authority: the head.
    """
    if not intent_gate_enabled():
        return "allow", "flag_off"
    classes = _INTENT_ROUTER_DOMAINS.get(intent_name)
    if classes is None:
        return "allow", "no_router_class"
    if not isinstance(verdict, dict):
        return "allow", "router_unavailable"
    if verdict.get("reason") == "evidence_question":
        if intent_name.startswith("memory_"):
            return "allow", "evidence_memory_intent"
        return "veto", "evidence_question"
    if verdict.get("reason") == "event_time_question":
        if intent_name.startswith("memory_") or intent_name == "calendar_show":
            return "allow", "event_time_recall_intent"
        return "veto", "event_time_question"
    if verdict.get("reason") == "own_fact_question":
        # ZOE_OWN_FACT_PRECEDENCE: "when is my birthday" / "what's my address"
        # asks for a stored fact — only a memory_* / people_* intent may answer,
        # never the clock, calendar, weather or lists.
        if intent_name.startswith(("memory_", "people_")):
            return "allow", "own_fact_recall_intent"
        return "veto", "own_fact_question"
    if verdict.get("gated"):
        if (verdict.get("reason") or "below_gate") == "below_gate":
            return "allow", "router_unsure"
        return "veto", "router_chat"
    if verdict.get("domain") in classes:
        return "allow", "router_agrees"
    return "veto", "router_disagrees"


def intent_gate(intent_name: str, text: str, *, lane: str) -> bool:
    """True = the keyword intent may execute. Runs the head (numpy, no sidecar)
    only for an intent that has a router class; logs
    ``INTENT_GATE lane=… intent=… head=<domain>@<conf> decision=… reason=…``.
    NEVER raises (a gate failure allows, i.e. today's behaviour)."""
    try:
        if not intent_gate_enabled() or intent_name not in _INTENT_ROUTER_DOMAINS:
            return True
        import semantic_router as _sr

        verdict = _sr.head_verdict(text)
        decision, reason = intent_gate_decision(intent_name, verdict)
        v = verdict or {}
        conf = v.get("head_conf")
        logger.info(
            "INTENT_GATE lane=%s intent=%s head=%s@%s head_reason=%s decision=%s reason=%s",
            lane, intent_name, v.get("head_top", "-"),
            "-" if conf is None else f"{float(conf):.4f}",
            v.get("reason") or "-", decision, reason,
        )
        return decision == "allow"
    except Exception as exc:  # the gate must never break a turn
        logger.warning("fast_tiers intent_gate failed (non-fatal, allow): %s", exc)
        return True


def keyword_intent_allowed(intent_name: str, text: str, *, lane: str) -> bool:
    """INTENT_GATE for a caller holding an intent from detect_and_extract_intent
    (routers/chat.py, after resolve() fell through). Only a KEYWORD claim is
    gated — the bare detector (no conversation context) must produce the same
    intent. A context follow-up ("add eggs" with a list on screen) or a
    classifier-derived intent is not the keyword detector's claim, and the head,
    which sees only the bare words, has no information about it → allow.
    NEVER raises."""
    try:
        from intent_router import detect_intent

        bare = detect_intent(text, log_miss=False)
        if bare is None or bare.name != intent_name:
            return True
        return intent_gate(intent_name, text, lane=lane)
    except Exception as exc:
        logger.warning("fast_tiers keyword_intent_allowed failed (non-fatal, allow): %s", exc)
        return True


async def _tier0(text: str, user_id: str, defer_intents: frozenset[str] = frozenset()):
    """Deterministic regex read shortcut. Returns a `DispatchResult` or `None`.

    `defer_intents` lists intent names this caller must NOT short-circuit at
    Tier-0 (e.g. voice defers user-scoped reads so its downstream scope gate stays
    authoritative); those fall through to the brain lane.
    """
    try:
        from intent_router import detect_intent, execute_intent

        intent = detect_intent(text, log_miss=False)
        if not intent or intent.name not in _TIER0_READ_INTENTS:
            return None
        # Caller-deferred (e.g. user-scoped reads on voice) — don't bypass the
        # caller's own downstream policy/scope gate; let the brain lane own it.
        if intent.name in defer_intents:
            return None
        # A `raw` slot means the intent still needs slot extraction we don't do at
        # Tier-0 — defer rather than execute a half-formed intent.
        if "raw" in (getattr(intent, "slots", None) or {}):
            return None
        # The head is the authority over a keyword claim (INTENT_GATE).
        if not intent_gate(intent.name, text, lane="tier0"):
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


async def _person_half_tier(text: str, user_id: str, session_id: str, speaker_verified: Optional[bool] = None):
    """Two deterministic tiers for the person half (Samantha person bench P5a/P7/P12), ahead of the router, the
    keyword intents and the brain, so a held fact never reaches a model that could cave and an ambiguous target
    is asked about BEFORE any tool call or write. Each is ``shadow`` by default (logs what it would do, changes
    nothing) and returns None unless it is ``enforce`` AND the turn is unmistakably its own:

    * ``ZOE_HOLD_THE_FACT`` (``hold_the_fact``) - "No, I'm sure it's Thursday." against a fact the OWNER stated:
      keep it, disagree once, offer to change it; the second explicit confirmation edits the row.
    * ``ZOE_ASK_WHEN_AMBIGUOUS`` (``ask_when_ambiguous``) - "Tell me about Marisol." with two Marisols: ONE question
      that names the choice; a clear turn is never asked.
    NEVER raises."""
    try:
        import hold_the_fact as _htf

        if _htf.mode() != "off":
            reply = await _htf.handle(text, user_id, session_id, speaker_verified=speaker_verified)
            if reply:
                import expert_dispatch as _xd

                return _xd.DispatchResult(
                    domain="memory", reply=reply, intent="hold_the_fact", tier="hold_the_fact",
                )
        import ask_when_ambiguous as _awa

        if _awa.mode() != "off":
            reply = await _awa.handle(text, user_id, session_id)
            if reply:
                import expert_dispatch as _xd

                return _xd.DispatchResult(
                    domain="people", reply=reply, intent="ask_when_ambiguous", tier="ask_when_ambiguous",
                )
    except Exception as exc:  # never let the tier break a turn
        logger.warning("fast_tiers person-half tier failed (non-fatal): %s", exc)
    return None


async def _conversation_quality_tier(text: str, user_id: str, session_id: str):
    """Two flag-dark deterministic tiers that run BEFORE everything else (a correction or
    a pasted roster must never be answered by a generic apology / a guessed role):

    * ``ZOE_CORRECTION_APPLY`` — "you've got the date wrong" / "Biscuit is their dog" reach the
      STORED record and the reply says what changed (``correction_apply``).
    * ``ZOE_ROSTER_NEUTRAL_ASK`` — a pasted `Name - detail` list whose roles are not stated is
      restated neutrally with ONE question, never assigned wife/girls from first names
      (``people_roles.roster_reply``).

    Each acts only on evidence (a stored row to fix / an unlabelled list) and returns None
    otherwise, so the brain answers every other turn exactly as before."""
    try:
        import correction_apply as _ca

        if _ca.enabled():
            res = await _ca.maybe_apply(text, user_id, session_id)
            if res is not None:
                import expert_dispatch as _xd

                return _xd.DispatchResult(
                    domain="memory", reply=res.reply, intent=f"correction_{res.kind}",
                    tier="correction",
                )
        from typed_env import env_bool

        if env_bool("ZOE_ROSTER_NEUTRAL_ASK", False) and user_id not in ("guest", ""):
            import people_roles as _pr

            if _pr.is_unlabelled_roster(text):
                import expert_dispatch as _xd

                return _xd.DispatchResult(
                    domain="people", reply=_pr.roster_reply(text), intent="roster_neutral_ask",
                    tier="roster",
                )
    except Exception as exc:  # never let the tier break a turn
        logger.warning("fast_tiers conversation-quality tier failed (non-fatal): %s", exc)
    return None


async def _identity_tier(text: str, user_id: str):
    """Own-identity question → one sentence built from the ACCOUNT, before recall.

    Unflagged (a correctness fix): it acts only on a narrow whole-utterance question
    shape from a REGISTERED account whose account actually holds the fact, and returns
    None otherwise — so the brain answers every other turn exactly as before."""
    try:
        import identity_facts as _idf

        hit = await _idf.maybe_answer(text, user_id)
        if hit is None:
            return None
        kind, reply = hit
        import expert_dispatch as _xd

        return _xd.DispatchResult(
            domain="identity", reply=reply, intent=f"identity_{kind}", tier="identity",
        )
    except Exception as exc:  # never let the tier break a turn
        logger.warning("fast_tiers identity tier failed (non-fatal): %s", exc)
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

        cq = await _conversation_quality_tier(text, user_id, session_id)
        if cq is not None:
            return cq

        # The person half: hold an owner-stated fact against a bare "No, I'm sure it's X";
        # ask ONE question when a request names a person two contacts share.
        ph = await _person_half_tier(text, user_id, session_id, (extra_ctx or {}).get("speaker_verified"))
        if ph is not None:
            return ph

        # Identity facts come from the account, never from memory.
        if prof.get("identity_tier"):
            idt = await _identity_tier(text, user_id)
            if idt is not None:
                return idt

        # Tier-0 — deterministic regex read shortcut (opt-in per channel).
        # `tier0_defer_intents` (from the channel profile) names read intents this
        # channel must NOT short-circuit here — e.g. voice defers user-scoped reads
        # so its downstream B3/B4 scope gate stays authoritative.
        if run_tier0:
            t0 = await _tier0(
                text, user_id,
                defer_intents=prof.get("tier0_defer_intents", frozenset()),
            )
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
        # Skipped when the ACTIVE two-stage router made the call: its decision
        # is already sibling-discriminated (grammar-constrained decode), so the
        # similarity margin no longer measures its ambiguity.
        margin = 0.0 if rr.get("two_stage") else _router_margin()
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
        if prof.get("binds_followups") is False:
            ctx["binds_followups"] = False
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
