"""Tier-1.5 router → domain-expert dispatch.

Fires at the voice "fall-through to brain" point: when every deterministic
regex fast-path (skybridge + intent_router) MISSED but the semantic router
(semantic_router.py) was confident about a DOMAIN. Instead of dropping to the
slow general brain, fulfill the domain by REUSING the existing handlers —
nlu_extractor slot-filling + intent_router.execute_intent for the structured
domains, and a focused recall expert for people/memory.

The LLM is NEVER the router: routing is the ~20ms embedding call. We only run
the existing tightly-scoped slot extractor or a focused expert AFTER the domain
is fixed.

Safety / rollout (env):
  ZOE_EXPERT_MODE        = shadow (default) | active
  ZOE_EXPERT_ACTIVE_DOMAINS = comma list of domains allowed to ACT in active
                           mode (default: read-only safe set). A domain not in
                           the list (or shadow mode) only LOGS what it would do
                           and returns None → caller falls through to the brain.
  ZOE_EXPERT_THRESHOLD__<domain> / ZOE_EXPERT_THRESHOLD = per-domain conf gate.

SHADOW is side-effect-free: it decides the intent and logs it, but never calls
execute_intent (which would create calendar events / list items) or run the
expert brain. Only `active` + an allow-listed domain actually fulfills.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# A create/write SIGNAL — fuzzy misses default to the safe SHOW/read intent
# unless one of these imperative cues is present (so "have I got anything on
# tomorrow" reads the calendar; "chuck bananas on the list" adds to it).
_CREATE_RE = re.compile(
    r"\b(add|put|create|schedule|book|set\s+up|set\s+a|make\s+a|new|chuck|stick|throw|"
    r"bung|jot|note\s+down|remind\s+me|nudge\s+me|ping\s+me|tell\s+me\s+to|get\s+me\s+to|"
    r"i\s+need\s+to|don'?t\s+let\s+me\s+forget)\b",
    re.IGNORECASE,
)
# A SHOW/read signal. Only act on a write-domain miss when we see a clear create
# OR show cue; genuinely ambiguous phrasings fall through to the brain instead
# of guessing (a wrong read/create is worse than the brain handling it).
_SHOW_RE = re.compile(
    r"\b(what'?s?\s+on|what\s+is\s+on|do\s+i\s+have|have\s+i\s+got|is\s+there|show\s+me|"
    r"what'?s\s+in|list\s+my|anything\s+on|am\s+i\s+free|what\s+do\s+i\s+(have|need)|"
    r"what\s+reminders|whats?\s+my)\b|\bis\s+\w+\s+on\s+(my|the)\b",
    re.IGNORECASE,
)

# Read intents = idempotent, safe to run even while validating. Write intents
# mutate, so they stay shadow until explicitly allow-listed.
_READ_DOMAINS = {"weather", "time", "people"}
_WRITE_DOMAINS = {"calendar", "lists", "reminders", "timers"}
_EXPERT_DOMAINS = {"memory", "people"}  # focused recall (people also tries read first)

_DEFAULT_THRESHOLDS: dict[str, float] = {
    # Raised reads to 0.68 so noisy STT fragments (e.g. "Name is genus" → time
    # 0.63) can't act, while real queries (got-the-time 0.71, weather 0.72+) pass.
    "weather": 0.68, "time": 0.68, "people": 0.72,
    "calendar": 0.68, "lists": 0.68, "reminders": 0.70, "timers": 0.68, "memory": 0.72,
}
# Domains allowed to ACT in active mode. Read intents within these are always
# safe; WRITE intents (create/add) additionally require ZOE_EXPERT_ALLOW_WRITES.
_DEFAULT_ACTIVE = "weather,time,lists,calendar,reminders,timers,people,memory"


def _allow_writes() -> bool:
    return os.environ.get("ZOE_EXPERT_ALLOW_WRITES", "0").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class DispatchResult:
    domain: str
    reply: str
    intent: str = ""
    ui: Optional[dict] = None
    source: str = "expert_dispatch"
    meta: dict = field(default_factory=dict)


def is_enabled() -> bool:
    return os.environ.get("ZOE_EXPERT_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")


def mode() -> str:
    return (os.environ.get("ZOE_EXPERT_MODE", "shadow") or "shadow").strip().lower()


def _active_domains() -> set[str]:
    raw = os.environ.get("ZOE_EXPERT_ACTIVE_DOMAINS", _DEFAULT_ACTIVE)
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def threshold_for(domain: str) -> float:
    for key in (f"ZOE_EXPERT_THRESHOLD__{domain}", "ZOE_EXPERT_THRESHOLD"):
        v = os.environ.get(key)
        if v:
            try:
                return float(v)
            except ValueError:
                pass
    return _DEFAULT_THRESHOLDS.get(domain, 0.66)


def _exec_user(user_id: str) -> str:
    return user_id if user_id and user_id != "family-admin" else "guest"


def _ui_for(domain: str) -> Optional[dict]:
    return {"calendar": {"kind": "calendar"}, "lists": {"kind": "list"},
            "reminders": {"kind": "reminder"}, "weather": {"kind": "weather"}}.get(domain)


async def dispatch(domain: str, text: str, ctx: dict[str, Any]) -> Optional[DispatchResult]:
    """Fulfill `text` as `domain` via existing handlers, or None → brain."""
    if not is_enabled():
        return None
    domain = (domain or "").strip().lower()
    text = (text or "").strip()
    if not text or domain in ("", "chat"):
        return None

    score = float(ctx.get("score") or 0.0)
    if score < threshold_for(domain):
        return None
    user_id = str(ctx.get("user_id") or "guest")
    session_id = str(ctx.get("session_id") or "")
    t0 = time.monotonic()

    # 1) Decide intent name + kind (cheap, no side effects, no slot extraction).
    try:
        plan = _plan(domain, text)
    except Exception as exc:
        logger.warning("expert_dispatch plan domain=%s failed: %s", domain, exc)
        return None
    if plan is None:
        logger.info("EXPERT_MISS domain=%s score=%.2f (no plan) → brain", domain, score)
        return None
    intent_name, regex_slots, kind = plan

    # 2) Decide whether we may ACT: active mode, domain allow-listed, and for
    #    WRITE/EXPERT kinds an extra opt-in (reads are always safe).
    can_act = (mode() == "active") and (domain in _active_domains())
    if kind in ("write", "expert"):
        can_act = can_act and _allow_writes()
    if not can_act:
        logger.warning("EXPERT_SHADOW domain=%s score=%.2f would=%s (%s) %.0fms → brain",
                       domain, score, intent_name, kind, (time.monotonic() - t0) * 1000.0)
        return None

    # 3) ACTIVE: fulfill via the existing path.
    try:
        if kind == "expert":
            reply = await _run_expert(domain, text, user_id, session_id)
        else:
            from intent_router import Intent, execute_intent
            slots = dict(regex_slots or {})
            if kind == "write":
                from nlu_extractor import extract_slots_for_intent
                ex = await extract_slots_for_intent(intent_name, text)
                if not ex:
                    return None
                slots = ex
            reply = await execute_intent(Intent(intent_name, slots), _exec_user(user_id))
    except Exception as exc:
        logger.warning("expert_dispatch execute domain=%s intent=%s failed: %s", domain, intent_name, exc)
        return None

    reply = (reply or "").strip()
    if not reply:
        logger.info("EXPERT_EMPTY domain=%s intent=%s → brain", domain, intent_name)
        return None
    logger.warning("EXPERT_ACTIVE domain=%s score=%.2f intent=%s %.0fms reply=%r",
                   domain, score, intent_name, (time.monotonic() - t0) * 1000.0, reply[:80])
    return DispatchResult(domain=domain, reply=reply, intent=intent_name, ui=_ui_for(domain))


def _plan(domain: str, text: str):
    """Return (intent_name, regex_slots, kind) or None. kind in {read,write,expert}.
    Pure/cheap: regex + a create-signal heuristic; NO slot extraction here."""
    from intent_router import detect_intent

    if domain == "weather":
        return ("weather", {}, "read")
    if domain == "time":
        det = detect_intent(text, log_miss=False)
        name = det.name if det and det.name in ("time_query", "date_query") else "time_query"
        return (name, {}, "read")
    if domain == "memory":
        return ("memory_expert", {}, "expert")
    if domain == "people":
        det = detect_intent(text, log_miss=False)
        if det and det.name in ("people_search", "people_relate"):
            return (det.name, det.slots, "read")
        return ("people_expert", {}, "expert")

    # calendar / lists / reminders / timers — disambiguate SHOW (read, safe)
    # from CREATE (write) using a regex hit first, else the create-signal cue.
    det = detect_intent(text, log_miss=False)
    domain_intents = {
        "lists": ("list_show", "list_add"),
        "calendar": ("calendar_show", "calendar_create"),
        "reminders": ("reminder_list", "reminder_create"),
        "timers": ("timer_create", "timer_cancel", "timer_status"),
    }.get(domain, ())
    if det and det.name in domain_intents and "raw" not in (det.slots or {}):
        kind = "read" if det.name.endswith(("_show", "_list", "_status")) else "write"
        return (det.name, det.slots, kind)

    table = {
        "lists": ("list_add", "list_show"),
        "calendar": ("calendar_create", "calendar_show"),
        "reminders": ("reminder_create", "reminder_list"),
    }
    if domain in table:
        create_intent, show_intent = table[domain]
        if _CREATE_RE.search(text):
            return (create_intent, {}, "write")
        if _SHOW_RE.search(text):
            return (show_intent, {}, "read")
        return None  # ambiguous → let the brain handle it
    if domain == "timers":
        return ("timer_create", {}, "write") if _CREATE_RE.search(text) else None
    return None


_EXPERT_PROMPTS = {
    "people": ("Answer the question about a person the user knows using ONLY the facts below. "
               "If it's not there, say you don't have that on file. One or two spoken sentences."),
    "memory": ("Recall what the user previously told you using ONLY the remembered facts below. "
               "If it isn't there, say you don't recall it. One or two spoken sentences."),
}


async def _run_expert(domain: str, text: str, user_id: str, session_id: str) -> Optional[str]:
    facts = ""
    try:
        from zoe_agent import _mempalace_load_user_facts
        facts = await _mempalace_load_user_facts(user_id, limit=20)
    except Exception:
        pass
    if domain == "memory" and not facts:
        return None
    composed = f"{_EXPERT_PROMPTS.get(domain, '')}\n\nUser asked: {text}"
    from zoe_core_client import run_zoe_core_streaming
    parts: list[str] = []
    async for d in run_zoe_core_streaming(composed, session_id or f"expert-{domain}",
                                          user_id=user_id, db_memory_context=facts or None,
                                          voice_mode=True):
        if d:
            parts.append(d)
    return "".join(parts).strip() or None
