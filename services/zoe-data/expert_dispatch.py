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

# NOTE / JOT cue: phrasings that belong to the notes capability (note_create),
# NOT memory-store. Deliberately narrow — only explicit "note"/"jot" verbs, so
# genuine memory teaches ("remember that …", "don't forget …", "keep in mind …")
# are untouched. When present, store_fact defers to the brain → note_create.
_NOTE_CUE_RE = re.compile(
    r"\b(make\s+a\s+note|take\s+a\s+note|note\s+(?:that|this|down)|jot(?:\s+(?:this|that|it))?\s+down|jot\s+down)\b",
    re.IGNORECASE,
)

# Question/fragment cue: when a write-domain utterance has no create verb, this
# prevents defaulting to a bogus CREATE (misheard "what's on my calendar today" →
# don't add an event). These fall to the brain instead.
_QUESTIONISH_RE = re.compile(
    r"\bwhat\b|\bwhen\b|\bwhere\b|\bwho\b|\bwhich\b|\bis\s+there\b|\bare\s+there\b|"
    r"\bdo\s+i\b|\bdid\s+i\b|\bhave\s+i\b|\babout\s+(this|my|the)\b|\?\s*$",
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
# NOTE: timers excluded — no backing store yet, so it would falsely confirm.
_DEFAULT_ACTIVE = "weather,time,lists,calendar,reminders,people,memory"


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
    # Provenance of the answer: "tier0" (regex shortcut) or "tier1.5" (expert
    # dispatch). Set by fast_tiers; lets a channel recognise a fast-tier reply
    # (e.g. LiveKit's add_to_chat_ctx=False rule). Metadata only — never rendered.
    tier: str = ""


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


async def dispatch(domain: str, text: str, ctx: dict[str, Any], *, write_ok: bool = True) -> Optional[DispatchResult]:
    """Fulfill `text` as `domain` via existing handlers, or None → brain.

    `write_ok=False` lets a caller take the read/recall fast path but defer any
    WRITE intent (create/add) to the brain — used by the chat channel, where a
    write needs LLM slot-extraction anyway (never sub-50ms) and we'd rather not
    run that extraction speculatively only to fall through. Reads/expert-recall
    are unaffected."""
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

    # Caller opted out of writes on this channel (e.g. chat): defer create/add to
    # the brain BEFORE paying for slot extraction. Reads/expert-recall continue.
    if kind == "write" and not write_ok:
        logger.info("EXPERT_DEFER_WRITE domain=%s intent=%s (write_ok=False) → brain", domain, intent_name)
        return None

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
            reply = await store_fact(domain, text, user_id, session_id,
                                     user_turn_id=str(ctx.get("user_turn_id") or "") or None)
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


def _calendar_qualifier(text: str) -> str:
    """Parse a date scope from a calendar query so 'tomorrow'/'this week' aren't
    lost when the show-intent comes through without slots (→ defaulted to today)."""
    low = (text or "").lower()
    if "tomorrow" in low:
        return "tomorrow"
    if "this month" in low:
        return "this month"
    if "this week" in low or "the week" in low or "weekend" in low:
        return "this week"
    if "today" in low or "tonight" in low:
        return "today"
    return ""


def _plan(domain: str, text: str):
    """Return (intent_name, regex_slots, kind) or None. kind in {read,write,expert}.
    Pure/cheap: regex + a create-signal heuristic; NO slot extraction here."""
    from intent_router import detect_intent

    if domain == "weather":
        low = text.lower()
        # Advice questions get a DIRECT yes/no answer, not a forecast dump.
        if re.search(r"\bumbrella\b|\brain(coat)?\b|will\s+it\s+rain|going\s+to\s+rain", low):
            return ("weather", {"advice": "rain"}, "read")
        if re.search(r"\bjacket\b|\bcoat\b|\bjumper\b|\bwarm(ly)?\b|\bcold\b|"
                     r"what\s+should\s+i\s+wear|do\s+i\s+need\s+a", low):
            return ("weather", {"advice": "warmth"}, "read")
        # Otherwise: forecast for time-shifted questions, current for "right now".
        fc = bool(re.search(
            r"\b(later|tonight|tomorrow|this\s+week|weekend|forecast|will\s+it|"
            r"going\s+to|gonna|sunny)\b", low))
        return ("weather", {"forecast": fc}, "read")
    if domain == "time":
        det = detect_intent(text, log_miss=False)
        name = det.name if det and det.name in ("time_query", "date_query") else "time_query"
        return (name, {}, "read")
    if domain == "memory":
        return ("memory_expert", {}, "expert")
    if domain == "people":
        det = detect_intent(text, log_miss=False)
        if det and det.name == "people_search":
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
        slots = dict(det.slots or {})
        if det.name == "calendar_show" and not slots.get("qualifier"):
            slots["qualifier"] = _calendar_qualifier(text)
        return (det.name, slots, kind)

    table = {
        "lists": ("list_add", "list_show"),
        "calendar": ("calendar_create", "calendar_show"),
        "reminders": ("reminder_create", "reminder_list"),
    }
    if domain in table:
        create_intent, show_intent = table[domain]
        # Explicit show cue → read; explicit create cue → write.
        if _SHOW_RE.search(text):
            slots = {"qualifier": _calendar_qualifier(text)} if show_intent == "calendar_show" else {}
            return (show_intent, slots, "read")
        if _CREATE_RE.search(text):
            return (create_intent, {}, "write")
        # No explicit cue. If it reads like a QUESTION or conversational fragment
        # ("what's on...", a garbled "what? ... today", "about this week"), do NOT
        # invent a create — that regression turned misheard SHOW queries into bogus
        # events. Defer to the brain instead.
        if _QUESTIONISH_RE.search(text):
            return None
        # Otherwise it's a bare statement with an addable noun ("milk on the
        # shopping list", "dentist tomorrow") → default to CREATE. The forced-tool
        # slot extractor still validates: no real item/title → None → brain.
        return (create_intent, {}, "write")
    if domain == "timers":
        return ("timer_create", {}, "write") if _CREATE_RE.search(text) else None
    return None


_EXPERT_PROMPTS = {
    "people": ("Answer the question about a person the user knows using ONLY the facts below. "
               "If it's not there, say you don't have that on file. One or two spoken sentences."),
    "memory": ("Recall what the user previously told you using ONLY the remembered facts below. "
               "If it isn't there, say you don't recall it. One or two spoken sentences."),
}


_QUESTION_RE = re.compile(
    r"\b(what'?s|what\s+is|what\s+are|what\s+was|what\s+were|who'?s|who\s+is|who\s+are|"
    r"when'?s|when\s+is|when\s+are|where'?s|where\s+is|where\s+are|whose|which|"
    r"does|do\s+i|did|how\s+old|how\s+many|how\s+much|tell\s+me\b(?!.*\bremember\b)|"
    r"remind\s+me\s+what)\b",
    re.IGNORECASE,
)


def _echo_fact(text: str) -> str:
    """Turn a stored statement into a second-person read-back for confirmation.
    "My mum's birthday is the 17th of the 11th" → "your mum's birthday is the
    17th of the 11th". Strips any leading 'remember that…' so it doesn't double up."""
    t = (text or "").strip().rstrip(".!?")
    t = re.sub(r"^(please\s+)?(remember|note|don'?t\s+forget|make\s+a\s+note|keep\s+in\s+mind)"
               r"(\s+(that|to))?\s*", "", t, flags=re.IGNORECASE)
    for pat, rep in (
        (r"\bI'?m\b", "you're"), (r"\bI\s+am\b", "you are"),
        (r"\bI'?ve\b", "you've"), (r"\bI'?ll\b", "you'll"), (r"\bI\b", "you"),
        (r"\bmy\b", "your"), (r"\bmine\b", "yours"), (r"\bme\b", "you"),
    ):
        t = re.sub(pat, rep, t, flags=re.IGNORECASE)
    return t.strip()


def _record_quality_reject(source: str, reason: str, text: str) -> None:
    """Log + count a write-quality reject so we can audit what's being dropped."""
    logger.info("MEMORY_QUALITY_REJECT source=%s reason=%s text=%r",
                source, reason, (text or "")[:120])
    try:
        from memory_metrics import memory_quality_reject_count
        memory_quality_reject_count.labels(source=source, reason=reason).inc()
    except Exception:
        pass


async def _ingest_or_supersede(svc, text: str, *, user_id: str, source: str,
                               session_id: Optional[str], user_turn_id: Optional[str],
                               memory_type: str, confidence: float,
                               tags: list[str]) -> str:
    """Ingest a conversational fact, merging it with an equivalent existing row.

    Returns an outcome string so callers can be HONEST about what happened
    (QA review F13 — teach replies must not claim success over a silent drop):

      * "stored"  — a row was written (new or superseding).
      * "skip"    — an equivalent fact already exists; nothing new written,
                    but the fact IS in the store, so confirming is honest.
      * "dropped" — the ingest silently refused the write (PII scrub reject /
                    opt-out); the fact is NOT stored and the caller must not
                    claim it is.

    Best-effort and OFF the voice hot path (callers already run this in the
    background). Uses MemoryService.search to find a near-duplicate / same-
    attribute fact, then asks ``classify_against_existing`` what to do:

      * "add"    — no equivalent fact; store as new.
      * "update" — same fact, the candidate is richer/more current; archive the
                   old row and ingest the candidate with a `supersedes` link.
      * "skip"   — same fact, the existing row is at least as informative (e.g.
                   it already spells the name); keep it and store NOTHING, so
                   consolidation stops accumulating sparser near-duplicates.

    Any failure in the dedup step degrades to a plain ingest so we never lose a
    real fact."""
    old_id: Optional[str] = None
    try:
        # Shared cross-writer decision (QA review F9): reconcile_for_ingest
        # wraps search + classify_against_existing AND the entity guards
        # (namesake / third-person-name protection) this path previously
        # lacked — a Jessica correction can no longer supersede Karen's row.
        from memory_quality import reconcile_for_ingest
        action, match_id = await reconcile_for_ingest(svc, text, user_id)
        if action == "skip" and match_id:
            # The existing row is at least as good — don't write a duplicate.
            try:
                from memory_metrics import memory_dedup_skip_count
                memory_dedup_skip_count.inc()
            except Exception:
                pass
            logger.info("MEMORY_DEDUP_SKIP source=%s kept=%s cand=%r",
                        source, match_id, text[:80])
            return "skip"
        if action == "update" and match_id:
            old_id = match_id
    except Exception as exc:
        logger.debug("near-dedup check failed (%s) → plain ingest", exc)
        old_id = None

    metadata = {"supersedes": old_id} if old_id else None
    ref = await svc.ingest(
        text, user_id=user_id, source=source,
        session_id=session_id, user_turn_id=user_turn_id,
        memory_type=memory_type, confidence=confidence, status="approved",
        tags=tags, metadata=metadata,
    )
    new_id = getattr(ref, "id", None)
    if ref is None:
        # ingest returned None: either an idempotent dedup (fact already in the
        # store — honest to confirm) or a genuine silent drop (PII reject /
        # opt-out — NOT stored). Distinguish with the same scrubber ingest uses
        # so the caller can reply honestly (QA review F13).
        outcome = "skip"
        try:
            from memory_service import scrub_pii
            _, _pii_reject = scrub_pii(text)
            if _pii_reject:
                outcome = "dropped"
        except Exception:
            pass  # scrubber unavailable → assume dedup, matching old behaviour
        if outcome == "dropped":
            logger.info("MEMORY_STORE_DROPPED source=%s text=%r", source, text[:80])
        return outcome
    # Only archive the old row if the ingest actually wrote a DISTINCT new row.
    # ingest dedups on a text-derived mem_id, so a near-identical value can map to
    # the same id as old_id (or be dropped, ref=None) — archiving then would delete
    # the only copy of the fact. Guard against that.
    if old_id and new_id and new_id != old_id:
        try:
            await svc.review(old_id, decision="archive", actor=source,
                             note="superseded by newer conversational fact")
            from memory_metrics import memory_supersede_count
            memory_supersede_count.labels(source=source).inc()
            logger.info("MEMORY_SUPERSEDE source=%s old=%s new=%r",
                        source, old_id, text[:80])
        except Exception as exc:
            # New row is already stored; failing to archive the old one just
            # leaves a (harmless) near-dup — don't lose the new fact over it.
            logger.debug("supersede archive failed (%s); new row kept", exc)
    return "stored"


async def store_fact(domain: str, text: str, user_id: str, session_id: str = "",
                     user_turn_id: Optional[str] = None) -> Optional[str]:
    """people/memory: STORE a taught fact (statement) or RECALL one (question).

    Statements are persisted to MemPalace via MemoryService.ingest (which has
    built-in idempotency via user_turn_id, so a double-submit can't double-store)
    so they become recallable. Questions fall back to the recall expert."""
    text = (text or "").strip()
    if not text:
        return None
    # STORE (imperative teach) vs RECALL (a question). The tricky case: "Do you
    # remember what my mum's name is?" is a RECALL question that happens to contain
    # the word 'remember' — it must not be mistaken for the imperative "remember
    # that …", or the question itself gets stored verbatim as a fact (observed
    # on-device). _QUESTION_RE also didn't know the "do YOU remember/recall/know"
    # form (only "do i"), so such questions slipped straight through to store.
    low = text.lower()
    # NOTE / JOT phrasings belong to the notes capability, not memory-store.
    # "make a note …", "jot this down", "note that …" must NOT be swallowed as a
    # remembered fact ("Got it — I'll remember …"); defer to the brain, which
    # routes them to note_create. This is a capability-defer, mirroring the
    # intent_router list_add guard: when a competing-domain cue owns the turn,
    # the deterministic fast path steps aside rather than guessing.
    if _NOTE_CUE_RE.search(low):
        return None
    asks_recall = bool(re.search(
        r"\b(do|did|does|can|could|would|will)\s+(you|ya)\s+"
        r"(remember|recall|recollect|know|have)\b", low))
    is_question = asks_recall or bool(_QUESTION_RE.search(text)) or text.rstrip().endswith("?")
    # Imperative teach only: a command to store, never a recall question. The
    # asks_recall guard covers the "do you / do ya remember…?" forms (any pronoun);
    # the lookbehinds also stop a bare "you/ya remember…" from reading as a command.
    imperative_store = (not asks_recall) and (
        bool(re.search(r"\b(note that|don'?t forget|make a note|keep in mind)\b", low))
        or bool(re.search(r"(?<!you )(?<!ya )\bremember\b", low))
    )
    if is_question and not imperative_store:
        return await _run_expert(domain, text, user_id, session_id)
    # Correction/negation shapes ("no <Name> is …", "no, …", "that's wrong…",
    # "actually …", "i meant …") must NEVER be verbatim-taught: "No Caitlin is
    # allergic to shellfish, …" was stored literally as a fact in prod (QA
    # review F8). The correction path (memory_extractor, run below per #1242's
    # ordering) owns these turns; when it yields nothing we drop with an honest
    # reply instead of storing junk.
    try:
        from memory_quality import ambiguous_negation_subject, looks_like_correction
        correctionish = looks_like_correction(text) and not imperative_store
    except Exception:
        # Gate unavailable must NOT reopen F8 (verbatim-storing a negation):
        # degrade to a minimal inline shape check that still catches the
        # correction openers, and drop rather than store when it fires.
        correctionish = (not imperative_store) and bool(re.match(
            r"^\s*(?:no\b|actually\b|that'?s\s+(?:wrong|not|incorrect)|"
            r"(?:sorry\s+)?i\s+meant\b|wait\s*,?\s*no\b)", low))
        ambiguous_negation_subject = lambda _t: None  # noqa: E731 — gate unavailable
        looks_like_correction = lambda _t: False  # noqa: E731
    # Write-quality gate (mem0-style): even past the store-vs-recall split, drop
    # candidates that aren't shaped like a storable personal fact (interrogative
    # leftovers, LLM meta-rambling, empty/too-short). Conservative — leans ACCEPT.
    try:
        from memory_quality import is_storable_fact
        storable, reason = is_storable_fact(text)
    except Exception:
        storable, reason = True, ""  # gate unavailable → degrade to plain store
    if not storable:
        _record_quality_reject("voice_fact", reason, text)
        # Not a fact → treat as a recall/conversational turn so the user still
        # gets a useful reply instead of a bogus "Got it, I'll remember …".
        return await _run_expert(domain, text, user_id, session_id)
    # Statement → store the fact.
    # Idempotency: ingest dedups on user_turn_id, but the voice path rarely
    # supplies one, so the same spoken fact got stored 3-4× (which then polluted
    # recall ranking). Fall back to a stable hash of (user, normalized text) so
    # repeats of the same statement collapse to one row.
    if not user_turn_id:
        import hashlib
        norm = re.sub(r"\s+", " ", text.lower()).strip()
        user_turn_id = "fact-" + hashlib.sha1(f"{user_id}|{norm}".encode()).hexdigest()[:16]
    # Person-fact linkage FIRST: if this fact is about a person — a named contact
    # ("Caitlin is allergic to nuts") or a pronoun subject resolved to the person
    # the prior turn introduced ("she is allergic to nuts" after "I have a friend
    # Caitlin Farrell") — route it through the coreference-aware extractor so it is
    # stored LINKED to that contact's people.id (and therefore recallable by their
    # name), not as a raw unlinked self-fact. Without this, "she is allergic to
    # nuts" was stored verbatim and "tell me about Caitlin" found nothing.
    try:
        from memory_extractor import extract_and_ingest as _mx_extract
        linked = await _mx_extract(
            text, user_id=user_id, session_id=session_id or None,
            source="voice_fact", auto_approve=True,
        )
    except Exception as exc:
        logger.debug("store_fact person-link path skipped (%s)", exc)
        linked = 0
    if linked and linked > 0:
        # Fact landed via the coreference extractor — an EXPLICIT teach beats a
        # recent forget, but only clear the shadow AFTER a successful store
        # (the extractor's voice_fact source is tombstone-exempt at ingest).
        try:
            from memory_tombstones import clear_matching as _tomb_clear_l
            _tomb_clear_l(user_id, text)
        except Exception:
            pass
        if correctionish:
            # A correction the extractor resolved — never echo the raw
            # negation ("I'll remember No Caitlin is…") back as a teach.
            return "Got it — I've updated that."
        echo = _echo_fact(text)
        return f"Got it — I'll remember {echo}." if echo else "Got it — I'll remember that."
    if correctionish:
        # Correction shape but the correction path produced nothing: NEVER
        # fall through to the verbatim ingest of the RAW text (QA F8). The
        # ambiguous "No <Name> is …" shape gets an honest clarification.
        name = ambiguous_negation_subject(text)
        if name:
            _record_quality_reject("voice_fact", "correction_shape_unresolved", text)
            return (
                f"I want to get that right about {name} and I'm not sure "
                f"which way you mean it — I haven't saved anything. "
                f"Could you say it once more, plainly?"
            )
        # A correction OPENER over a self-contained fact ("actually my dentist
        # is on Tuesday") still carries a storable remainder — strip the opener
        # and store THAT (never the raw negation-shaped text). Only when the
        # remainder is itself junk/correction-shaped do we drop to the brain.
        stripped = re.sub(
            r"^\s*(?:no\s*[,!.:;-]\s*|actually\s*,?\s*|wait\s*,?\s*(?:no\s*,?\s*)?|"
            r"(?:sorry\s*,?\s*)?i\s+meant\s+|that'?s\s+(?:wrong|incorrect|not\s+"
            r"(?:right|true|correct))\s*[,!.:;-]?\s*)+",
            "", text, flags=re.IGNORECASE).strip()
        remainder_ok = False
        if stripped and stripped.lower() != low:
            try:
                remainder_ok = (
                    is_storable_fact(stripped)[0]
                    and not looks_like_correction(stripped)
                )
            except Exception:
                remainder_ok = False
        if not remainder_ok:
            _record_quality_reject("voice_fact", "correction_shape_unresolved", text)
            return None  # defer to the brain, reply-only, nothing stored
        text = stripped
    try:
        from memory_service import get_memory_service
        svc = get_memory_service()
        # Near-dedup / supersession (best-effort, background path only): if a
        # high-similarity same-attribute fact already exists ("my dad's name is
        # Neil" vs an earlier value), UPDATE/supersede it instead of stacking a
        # duplicate/contradiction. Falls through to a plain ingest on any error.
        outcome = await _ingest_or_supersede(
            svc, text, user_id=user_id, source="voice_fact",
            session_id=session_id or None, user_turn_id=user_turn_id,
            memory_type="fact", confidence=0.85, tags=["voice", "self"],
        )
    except Exception as exc:
        # QA review F13: this teach fast path replies INSTANTLY, so a failed
        # write must never be answered with "Got it — I'll remember…" (and
        # deferring to the brain risks the same false confirmation). Be honest.
        logger.warning("store_fact ingest failed (%s) → honest no-save reply", exc)
        return ("Hmm — I couldn't save that just now. "
                "Mind telling me again in a moment?")
    if outcome == "dropped":
        # The store refused the write (e.g. PII scrub). Nothing landed —
        # don't claim it did, and don't invite a retry that would re-reject.
        _record_quality_reject("voice_fact", "store_dropped", text)
        return ("I wasn't able to save that one — it looks like something "
                "I shouldn't keep. Nothing was stored.")
    # "stored" or "skip" (an equivalent fact already lives in the store — the
    # fact IS remembered, so confirming stays honest). Legacy None (test fakes)
    # also lands here, matching pre-F13 behaviour.
    # Echo the captured fact back (second person) so the user hears exactly what
    # landed and can correct it — a confirmation without a separate "should I
    # save that?" turn.
    # Store succeeded (or an equivalent fact already lives there): the explicit
    # teach now clears any matching forget tombstone. Never on the failure/
    # dropped paths above — a failed store must keep the shadow (Greptile P1).
    try:
        from memory_tombstones import clear_matching as _tomb_clear
        _tomb_clear(user_id, text)
    except Exception:
        pass
    echo = _echo_fact(text)
    return f"Got it — I'll remember {echo}." if echo else "Got it — I'll remember that."


async def _run_expert(domain: str, text: str, user_id: str, session_id: str) -> Optional[str]:
    facts = ""
    # Query-RELEVANT retrieval first. A generic top-N (load_for_prompt) buries the
    # one fact that answers the question once a user has dozens of memories — which
    # is exactly why "what's my mum's name" missed despite "My mum's name is Janice"
    # sitting in the store. Semantic search keyed to the question surfaces it.
    try:
        from memory_service import get_memory_service
        rows = await get_memory_service().search(text, user_id=user_id, limit=8)
        seen: set[str] = set()
        lines: list[str] = []
        for r in rows or []:
            t = (getattr(r, "text", "") or "").strip()
            key = t.lower()
            if t and key not in seen:
                seen.add(key)
                lines.append(f"- {t[:200]}")
        if lines:
            facts = "## Relevant things you've told me:\n" + "\n".join(lines)
    except Exception as exc:
        logger.debug("recall search failed (%s) → top-N fallback", exc)
    # Fallback: generic top-N (covers anything search didn't return).
    if not facts:
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
