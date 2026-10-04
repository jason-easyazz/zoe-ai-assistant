"""Tier-1 embedding semantic router for Zoe's voice/chat path.

A local bge-small (ONNX, CPU, ~7ms/query) classifies an utterance into a domain
(calendar/lists/reminders/timers/weather/time/people/memory) or 'chat' (→ the
Tier-2 Pi brain). It sits BETWEEN the deterministic regex fast-path (Tier 0) and
the brain (Tier 2): regex-class latency with LLM-class fuzziness, without putting
the LLM in the routing hot-path (the approach that failed on the Jetson before).

Runs OBSERVE-ONLY by default (ZOE_ROUTER_MODE=shadow) — it logs its decision and
whether that agrees with what actually handled the turn, so accuracy can be
validated on live traffic before it ever changes behavior.

SetFit head (ZOE_ROUTER_HEAD=off|shadow|shadow2|active, default off): 'shadow'
logs the logreg head's prediction + agreement per turn (utterance-hash only)
and never routes. 'shadow2' computes + logs the FULL two-stage decision
(router_two_stage: MLP top-3 shortlist + 0.5 chat gate + 0.70 low-confidence
floor (ZOE_ROUTER_HEAD_MIN_CONF) + grammar-constrained
FunctionGemma sidecar on :11436) in a background thread — still never routes.
'active' lets that two-stage decision pick the domain (proven 90.1%/0% chat-FP
on the 81-case corpus, labs/router-90-campaign); ANY failure falls back to the
similarity decision and thence the brain. Score logs with
scripts/maintenance/router_shadow_report.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = __import__("logging").getLogger(__name__)

# Dedicated logger for the SetFit-head shadow comparison lines so they can be
# filtered/scored independently of the main router logs.
shadow_logger = __import__("logging").getLogger("zoe.router_head_shadow")

# Domain example utterances. Keep paraphrase-diverse; the embedding generalizes.
ROUTES: dict[str, list[str]] = {
    "calendar": [
        "what's on my calendar today", "what's on my calendar tomorrow",
        "do I have anything on this week", "schedule a meeting for friday at 3",
        "add a dentist appointment next tuesday", "am I free on saturday",
        "what appointments do I have", "book me in for a haircut next week",
        "put lunch with sarah on my calendar monday",
    ],
    "lists": [
        "add milk to my shopping list", "put bread on the grocery list",
        "what's on my shopping list", "show me my todo list",
        "add eggs and butter to the list", "create a new packing list",
        "is milk on my shopping list", "remove bananas from my list",
        "what do I still need to buy",
    ],
    "reminders": [
        "remind me to call mum at 6", "set a reminder to take the bins out",
        "remind me to water the plants tomorrow", "what reminders do I have",
        "remind me about the meeting", "nudge me to ring the dentist later",
        "don't let me forget to pay the rent",
    ],
    "timers": [
        "set a timer for 10 minutes", "start a 5 minute timer",
        "set a timer for the pasta", "how long left on my timer",
        "cancel the timer", "give me ten minutes on the timer",
    ],
    "weather": [
        "what's the weather like", "is it going to rain today",
        "what's the temperature outside", "weather forecast for the weekend",
        "do I need an umbrella", "how hot is it in perth", "will it be sunny this arvo",
    ],
    "time": [
        "what time is it", "what's the date today", "what day is it",
        "what's today's date", "is it morning or afternoon", "got the time on you",
    ],
    "people": [
        # questions about a person
        "when is john's birthday", "what's sarah's phone number",
        "tell me about my brother", "who is michael",
        "what do I know about emma", "what's my wife's favourite colour",
        "what is my mum's name", "what's my dad's name",
        # STATEMENTS that teach Zoe a fact about a person (store, not query)
        "my mum's name is janice", "my dad's name is neil",
        "my brother is called tom", "my wife's name is sarah",
        "my friend's birthday is in june", "remember that my friend lives in perth",
        "let me tell you about my friend", "I want to tell you about my mum",
        "her name is emma and she's my sister", "his birthday is the third of may",
        # birthdays/dates spoken as NUMERIC dates ("the 17th of the 11th, 1947")
        "my mum's birthday is the 17th of the 11th 1947",
        "my dad's birthday is the 5th of the 6th 1950",
        "her birthday is the 22nd of the 9th", "his anniversary is the 10th of the 6th",
    ],
    "memory": [
        "what did I say about the project", "do you remember what I told you yesterday",
        "what did I tell you about my goals", "remind me what we discussed",
        "what's my favourite restaurant", "have I mentioned my car before",
        # statements to remember (store a fact)
        "remember that I parked on level three", "I want you to remember something",
        "make a note that the wifi password is bluebird", "keep in mind I'm allergic to nuts",
        "remember I like my coffee black",
    ],
    "chat": [
        "how are you feeling today", "tell me a joke", "what's the meaning of life",
        "I'm feeling a bit down today", "what do you think about space",
        "let's have a chat", "good morning zoe", "thank you so much",
        "what is the capital of japan", "tell me a fun fact about the ocean",
        "I was just testing your voice", "give me a reason to drink water",
    ],
}

_MODEL = None
_MATRIX: Optional[np.ndarray] = None
_LABELS: Optional[np.ndarray] = None
_DOM_IDX: dict[str, np.ndarray] = {}
_LOCK = threading.Lock()
_MODEL_NAME = os.environ.get("ZOE_ROUTER_MODEL", "BAAI/bge-small-en-v1.5")

# --- SetFit classifier head (labs/setfit-router PR #1296) -------------------
# A 38 KB logistic-regression head trained on the SAME bge-small embedding this
# module already computes per turn (+~0.2 ms). Served from the numpy export
# (models/router_head_logreg.npz + .json, router_heads_numpy.py) — the .joblib
# path below is the configured name; the numpy backend reads the pair beside it. SHADOW-ONLY today: it logs its
# prediction + agreement with the similarity router and NEVER changes routing.
_HEAD = None
_HEAD_FAILED = False  # load failed once → don't retry every turn
_HEAD_PATH = os.environ.get(
    "ZOE_ROUTER_HEAD_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "models", "router_head_logreg.joblib"),
)
_HEAD_LOG_PATH = os.environ.get(
    "ZOE_ROUTER_HEAD_LOG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "data", "router_head_shadow.jsonl"),
)

# ── Shadow-log rotation ─────────────────────────────────────────────────────
# The shadow log is append-only on EVERY routed turn, so left alone it grows
# without bound on a box with a small disk.
#
# It is ROTATED, never truncated, because the self-training MINER
# (labs/router-selftrain/mine_candidates.py) reads the whole history by default
# (`--since` defaults to 0.0) to turn real measured mistakes into training
# candidates. Dropping records in place would silently starve the mine→label→
# ratchet loop of exactly the traffic it exists to learn from. Rotated segments
# stay on disk as `<log>.1`, `<log>.2`, … and the readers glob them back in, so
# rotation is lossless until a segment ages out of the retention window.
#
# Budget: MAX_BYTES per segment × (KEEP + 1) segments. The defaults keep ~80 MB
# of history — at the observed record size that is a large multiple of what the
# miner has ever needed in one run, while bounding the worst case.
_SHADOW_MAX_BYTES = int(os.environ.get("ZOE_ROUTER_SHADOW_MAX_BYTES", 16 * 1024 * 1024))
_SHADOW_KEEP = int(os.environ.get("ZOE_ROUTER_SHADOW_KEEP", 4))

# Serialises rotate+append. Both the per-turn head shadow and the shadow2
# two-stage logger (which runs in a BACKGROUND thread) append to this file, so
# without this two threads could rotate concurrently and lose a segment.
_shadow_write_lock = threading.Lock()


def _rotate_shadow_log(path: str) -> None:
    """Roll `path` to `path.1` (and shift older segments) once it exceeds the cap.

    Caller must hold `_shadow_write_lock`. Best-effort: rotation must never break
    a turn, so any OSError is swallowed by the callers' existing handlers.
    """
    if _SHADOW_MAX_BYTES <= 0:  # 0/negative disables rotation entirely
        return
    try:
        if os.path.getsize(path) < _SHADOW_MAX_BYTES:
            return
    except OSError:
        return  # missing file -> nothing to rotate

    # Drop the oldest, then shift each segment down: .3 -> .4, .2 -> .3, .1 -> .2
    oldest = f"{path}.{_SHADOW_KEEP}"
    if os.path.exists(oldest):
        os.remove(oldest)
    for seg in range(_SHADOW_KEEP - 1, 0, -1):
        src = f"{path}.{seg}"
        if os.path.exists(src):
            os.replace(src, f"{path}.{seg + 1}")
    os.replace(path, f"{path}.1")


def _append_shadow_line(path: str, line: str) -> None:
    """Rotate-if-needed then append one JSON line, under the shadow write lock."""
    with _shadow_write_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _rotate_shadow_log(path)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def shadow_log_segments(path: str | None = None) -> list[str]:
    """Every existing shadow-log segment, OLDEST first.

    THE contract for readers. Rotation moves history into `<log>.1`, `<log>.2`,
    …, so anything that reads only `<log>` silently sees just the newest slice.
    The miner and the shadow reports go through this so rotation stays lossless
    for them.

    Ordering is oldest→newest (`.N` … `.1`, then the live file) so that
    concatenating segments yields records in append order, which is what the
    readers' chronological assumptions (and `--since`) expect.
    """
    path = path or _HEAD_LOG_PATH
    segments = [
        f"{path}.{seg}"
        for seg in range(_SHADOW_KEEP, 0, -1)
        if os.path.exists(f"{path}.{seg}")
    ]
    if os.path.exists(path):
        segments.append(path)
    return segments


def head_mode() -> str:
    """ZOE_ROUTER_HEAD: 'off' (default) | 'shadow' | 'shadow2' | 'active'.

    off      no head at all (similarity routing only).
    shadow   logreg head logs prediction+agreement per turn; never routes.
    shadow2  the FULL two-stage decision (router_two_stage.decide: MLP top-3
             shortlist + gate + FunctionGemma sidecar) is computed in a
             BACKGROUND thread and logged; never routes. Rehearsal for active.
    active   the two-stage decision routes: its tool→domain replaces the
             similarity domain in route(); any failure/timeout/gate-abstain
             falls back to the similarity decision (and thence the brain).
    """
    val = (os.environ.get("ZOE_ROUTER_HEAD", "off") or "off").strip().lower()
    if val in ("", "0", "false", "no", "off"):
        return "off"
    if val in ("shadow", "shadow2", "active"):
        return val
    logger.warning("unknown ZOE_ROUTER_HEAD=%r — treating as 'off'", val)
    return "off"


def shadow_text_enabled() -> bool:
    """ZOE_ROUTER_SHADOW_TEXT — opt-in RAW-TEXT capture in the shadow log.

    DEFAULT OFF. The shadow log is keyed by an utterance HASH by design, so a
    routing post-mortem never needs the family's words. Turning this on adds a
    plaintext ``utt_text`` field to the shadow-log FILE records (never to the
    INFO log line, which stays hash-only so journald/log shipping is unaffected)
    so the router self-training miner can build labelled training data from real
    traffic instead of templates.

    This is a LOCAL-ONLY, family-opt-in training-data switch: the text is written
    to a file on this box, is mined by a local script, and is labelled by the
    local Gemma brain. It never leaves the box. Leave it off unless the household
    has agreed to a self-training round; the hash is kept either way.
    """
    return ((os.environ.get("ZOE_ROUTER_SHADOW_TEXT", "") or "")
            .strip().lower() in ("1", "true", "yes", "on"))


def _with_text(rec: dict, text: str) -> dict:
    """The record as written to the shadow-log FILE: hash always, raw text only
    under the ZOE_ROUTER_SHADOW_TEXT opt-in."""
    if not shadow_text_enabled():
        return rec
    return {**rec, "utt_text": text or ""}


def head_threshold() -> float:
    """Confidence gate for the head's *hypothetical* decision (README: 0.4)."""
    try:
        return float(os.environ.get("ZOE_ROUTER_HEAD_THRESHOLD", "0.4"))
    except Exception:
        return 0.4


def is_enabled() -> bool:
    return (os.environ.get("ZOE_ROUTER_ENABLED", "1").strip().lower()
            in ("1", "true", "yes", "on"))


def mode() -> str:
    """'shadow' (observe-only, default) or 'active' (may route)."""
    return (os.environ.get("ZOE_ROUTER_MODE", "shadow") or "shadow").strip().lower()


def threshold() -> float:
    try:
        return float(os.environ.get("ZOE_ROUTER_THRESHOLD", "0.62"))
    except Exception:
        return 0.62


def _ensure_loaded():
    global _MODEL, _MATRIX, _LABELS, _DOM_IDX
    if _MODEL is not None:
        return
    with _LOCK:
        if _MODEL is not None:
            return
        from fastembed import TextEmbedding

        model = TextEmbedding(model_name=_MODEL_NAME)
        labels, examples = [], []
        for dom, utts in ROUTES.items():
            for u in utts:
                labels.append(dom)
                examples.append(u)
        M = np.asarray(list(model.embed(examples)), dtype=np.float32)
        M /= (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
        lab = np.asarray(labels)
        _DOM_IDX = {d: np.where(lab == d)[0] for d in ROUTES}
        _LABELS = lab
        _MATRIX = M
        _MODEL = model
        logger.info("semantic_router loaded %s (%d examples, %d domains)",
                    _MODEL_NAME, len(labels), len(ROUTES))
    _ensure_head_loaded()


def _ensure_head_loaded():
    """Lazy-load the SetFit head (only when ZOE_ROUTER_HEAD != off)."""
    global _HEAD, _HEAD_FAILED
    if _HEAD is not None or _HEAD_FAILED:
        return
    if head_mode() == "off":
        return
    with _LOCK:
        if _HEAD is not None or _HEAD_FAILED:
            return
        try:
            # numpy backend by default (no sklearn/scipy in this process);
            # ZOE_ROUTER_HEADS_BACKEND=joblib is the one-release fallback.
            import router_heads_numpy

            head = router_heads_numpy.load_head(_HEAD_PATH)
            # sanity: needs predict_proba + classes_ (the LogisticRegression API)
            if not (hasattr(head, "predict_proba") and hasattr(head, "classes_")):
                raise TypeError(f"unexpected head artifact type {type(head)!r}")
            _HEAD = head
            logger.info("semantic_router head loaded %s (%d classes, backend=%s)",
                        _HEAD_PATH, len(head.classes_), router_heads_numpy.backend())
        except Exception as exc:
            _HEAD_FAILED = True
            logger.warning("semantic_router head load failed (shadow disabled, "
                           "non-fatal): %s", exc)


def warm() -> bool:
    """Startup pre-load so the first real turn doesn't pay model load."""
    if not is_enabled():
        return False
    try:
        t0 = time.monotonic()
        _ensure_loaded()
        route("warmup query")  # warm the onnx session
        logger.info("semantic_router warmup completed in %.1fs", time.monotonic() - t0)
        return True
    except Exception as exc:
        logger.warning("semantic_router warmup failed (non-fatal): %s", exc)
        return False


def _head_shadow(text: str, v: np.ndarray, routed: str) -> None:
    """SHADOW-ONLY head comparison. Never influences routing, never raises.

    Logs a structured line keyed by an utterance HASH (no raw text at INFO —
    privacy) so live agreement can be scored later via
    scripts/maintenance/router_shadow_report.py.
    """
    if head_mode() != "shadow":
        return
    try:
        _ensure_head_loaded()
        if _HEAD is None:
            return
        t0 = time.perf_counter()
        proba = _HEAD.predict_proba(v.reshape(1, -1))[0]
        idx = int(np.argmax(proba))
        head_pred = str(_HEAD.classes_[idx])
        head_conf = float(proba[idx])
        # the decision the head WOULD take under the recommended gate
        head_routed = (head_pred
                       if (head_conf >= head_threshold() and head_pred != "chat")
                       else "chat")
        rec = {
            "ts": round(time.time(), 3),
            "utt": hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12],
            "head_pred": head_pred,
            "head_conf": round(head_conf, 4),
            "head_routed": head_routed,
            "actual_routed": routed,
            "agree": head_routed == routed,
            "head_ms": round((time.perf_counter() - t0) * 1000, 3),
        }
        shadow_logger.info("router_head_shadow %s", json.dumps(rec, sort_keys=True))
        try:
            _append_shadow_line(
                _HEAD_LOG_PATH,
                json.dumps(_with_text(rec, text), sort_keys=True),
            )
        except OSError as exc:
            shadow_logger.debug("shadow log append failed: %s", exc)
    except Exception as exc:  # shadow must never break a turn
        logger.warning("router head shadow failed (non-fatal): %s", exc)


def _log_two_stage(rec: dict) -> None:
    """Append a structured two-stage line to the shadow log.

    The INFO log line is ALWAYS hash-only (privacy: journald never sees the
    family's words). The file record carries raw text only when the record was
    built under the ZOE_ROUTER_SHADOW_TEXT opt-in (see `_with_text`).
    """
    try:
        line_rec = {k: v for k, v in rec.items() if k != "utt_text"}
        shadow_logger.info("router_two_stage %s", json.dumps(line_rec, sort_keys=True))
        _append_shadow_line(_HEAD_LOG_PATH, json.dumps(rec, sort_keys=True))
    except Exception as exc:
        shadow_logger.debug("two_stage log append failed: %s", exc)


def _two_stage_rec(text: str, decision: Optional[dict], mode_: str,
                   actual_routed: str,
                   similarity_routed: Optional[str] = None) -> dict:
    """One shadow-log record for a two-stage decision.

    `similarity_routed` is the INDEPENDENT baseline — what the similarity router
    would have done. It matters in 'active' mode, where the two-stage decision IS
    the route and `actual_routed` is therefore just an echo of `two_stage_domain`
    (comparing them is a tautology). Without the baseline field, an active record
    cannot express "the router got this wrong", and the self-training miner has
    nothing to learn from. In 'shadow2' the two-stage doesn't route, so the
    baseline and the actual route are the same thing.
    """
    d = decision or {}
    return _with_text({
        "ts": round(time.time(), 3),
        "mode": mode_,
        "utt": hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12],
        "similarity_routed": (similarity_routed if similarity_routed is not None
                              else actual_routed),
        "two_stage_tool": d.get("tool"),
        "two_stage_domain": d.get("domain"),
        "shortlist": d.get("shortlist"),
        "head_conf": d.get("head_conf"),
        "gated": d.get("gated"),
        "reason": d.get("reason"),
        "failed": decision is None,
        "two_stage_ms": d.get("ms"),
        "actual_routed": actual_routed,
    }, text)


def _two_stage_shadow2(text: str, v: np.ndarray, routed: str) -> None:
    """shadow2: compute+log the full two-stage decision OFF the turn's
    critical path (the sidecar call is ~400 ms — never block a live turn)."""
    def _run():
        try:
            import router_two_stage

            decision = router_two_stage.decide(text, v)
            _log_two_stage(_two_stage_rec(text, decision, "shadow2", routed))
        except Exception as exc:  # shadow must never break anything
            logger.warning("two_stage shadow2 failed (non-fatal): %s", exc)
    threading.Thread(target=_run, name="router-shadow2", daemon=True).start()


def _two_stage_active(text: str, v: np.ndarray) -> Optional[dict]:
    """active: the two-stage decision, synchronous (it IS the route).
    None → caller keeps the similarity decision (brain-safe fallback)."""
    try:
        import router_two_stage

        return router_two_stage.decide(text, v)
    except Exception as exc:
        logger.warning("two_stage active failed (non-fatal, similarity "
                       "fallback): %s", exc)
        return None


@dataclass
class RouterDecision:
    """Public result of the two-stage router (interface contract for the
    corpus/prod-path harness). `source`:
      two_stage       the sidecar decoded a validated tool call
      gate_abstain    stage-1 said chat (top==chat, conf < gate, or a non-chat
                      top below ZOE_ROUTER_HEAD_MIN_CONF) → brain
      shortlist_miss  the decoder took the chat escape / no legal tool → brain
      error_fallback  head/sidecar failure or timeout → brain
    """
    tool: Optional[str]
    args: dict = field(default_factory=dict)
    confidence: float = 0.0
    source: str = "error_fallback"
    latency_ms: float = 0.0


# One-slot embedding cache: a keyword-lane head check (head_verdict) and the
# turn's route() embed the SAME text back to back; the second is free. Keyed by
# (model identity, exact text), replaced atomically (a tuple), so concurrent
# turns can only miss, never read another turn's vector.
_EMBED_CACHE: tuple[int, str, np.ndarray] | None = None


def embed(text: str) -> np.ndarray:
    """Normalized bge-small embedding of `text` (a fresh copy every call)."""
    global _EMBED_CACHE
    key = text or ""
    _ensure_loaded()
    hit = _EMBED_CACHE
    if hit is not None and hit[0] == id(_MODEL) and hit[1] == key:
        return hit[2].copy()
    v = np.asarray(next(iter(_MODEL.embed([key]))), dtype=np.float32)
    v /= (np.linalg.norm(v) + 1e-9)
    _EMBED_CACHE = (id(_MODEL), key, v.copy())
    return v


# ── Evidence precedence: when-did-I / what-did-I-say / are-you-sure
# (memory_gate.is_evidence_question) never belongs to a domain expert. The head
# reads a NAME as people (live 2026-09-30: "What exactly did I say about
# Marisol?" → people @ 0.9872), but only the recall packet can answer (it dates
# and quotes the user's words — recall_evidence). when/said → memory; sure →
# chat (the brain: a challenge may be about the world). ONE rule on BOTH head
# surfaces — route() (Tier-1, Skybridge, voice) and head_verdict() (INTENT_GATE).
# Unflagged, like the low-confidence floor (router_two_stage.gate_reason).
EVIDENCE_REASON = "evidence_question"


def evidence_target(text: str, domain: Optional[str]) -> Optional[str]:
    """Where an evidence-shaped question goes instead of `domain`, or None to
    keep `domain` (not evidence-shaped, or already chat/memory). Pure."""
    if domain in (None, "chat", "memory"):
        return None
    from memory_gate import evidence_question_kind  # stdlib-only

    kind = evidence_question_kind(text or "")
    if not kind:
        return None
    return "chat" if kind == "sure" else "memory"


# ── Event-time precedence (day-sim 9, flag-dark ZOE_ROUTER_EVENT_TIME_PRECEDENCE):
# "What time is my dentist appointment on Friday?" → head time @ 0.997 → "It's
# 10:41 PM." (live 2026-10-03). A when/what-time question about the user's OWN
# event (memory_gate.is_event_time_question) keeps a calendar/memory/chat claim
# (the calendar expert defers a question to the brain, which holds the calendar
# tool and the recall floor) and any other domain is re-pointed to memory.
EVENT_TIME_REASON = "event_time_question"
_EVENT_TIME_KEEP = frozenset({"chat", "memory", "calendar"})


def event_time_precedence_enabled() -> bool:
    """ZOE_ROUTER_EVENT_TIME_PRECEDENCE — default OFF (voice path; the operator
    flips it after the replay gate). Per-call env read."""
    raw = (os.environ.get("ZOE_ROUTER_EVENT_TIME_PRECEDENCE") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def event_time_target(text: str, domain: Optional[str]) -> Optional[str]:
    """"memory" when an event-time question was claimed by a domain other than
    calendar/memory/chat (and the flag is on), else None. Pure but for the flag."""
    if domain is None or domain in _EVENT_TIME_KEEP or not event_time_precedence_enabled():
        return None
    from memory_gate import is_event_time_question  # stdlib-only

    return "memory" if is_event_time_question(text or "") else None


# ── Own-fact precedence (live 2026-10-04, flag-dark ZOE_OWN_FACT_PRECEDENCE):
# "When is my birthday?" → head time (the word "when") → "It's 7:50 AM.". A
# question about a stored fact of the user's own life (birthday, address, age,
# where they live — memory_gate.is_own_fact_question) can never be answered by
# a clock, calendar, weather, list or timer tool; only chat (the brain + recall
# packet) and memory/people keep their claim, anything else is re-pointed to
# memory (recall_memory). The ONE rule on both head surfaces, like the others.
OWN_FACT_REASON = "own_fact_question"
_OWN_FACT_KEEP = frozenset({"chat", "memory", "people"})


def own_fact_precedence_enabled() -> bool:
    """ZOE_OWN_FACT_PRECEDENCE — default OFF (voice path; the operator flips it
    after the replay gate). Per-call env read."""
    raw = (os.environ.get("ZOE_OWN_FACT_PRECEDENCE") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def own_fact_target(text: str, domain: Optional[str]) -> Optional[str]:
    """"memory" when an own-fact question was claimed by a clock/calendar/
    weather/… domain (and the flag is on), else None. An event-time question
    ("when is my birthday party") is the event-time rule's, not this one."""
    if domain is None or domain in _OWN_FACT_KEEP or not own_fact_precedence_enabled():
        return None
    from memory_gate import is_event_time_question, is_own_fact_question  # stdlib-only

    msg = text or ""
    if is_event_time_question(msg) or not is_own_fact_question(msg):
        return None
    return "memory"


def question_precedence(text: str, domain: Optional[str]) -> Optional[tuple[str, str]]:
    """(target, reason) when a question-shape rule overrides `domain`, else
    None. Evidence first (a "when did I…" is evidence, not an event time); then
    event-time (keeps a calendar claim); then own-fact (keeps none of the
    tools)."""
    target = evidence_target(text, domain)
    if target:
        return target, EVIDENCE_REASON
    target = event_time_target(text, domain)
    if target:
        return target, EVENT_TIME_REASON
    target = own_fact_target(text, domain)
    return (target, OWN_FACT_REASON) if target else None


def _evidence_decision(decision: dict, target: str, reason: str = EVIDENCE_REASON) -> dict:
    """`decision` re-pointed at `target`; head_top/head_conf keep the head's."""
    if target == "chat":
        return {**decision, "tool": None, "domain": "chat", "args": {},
                "gated": True, "reason": reason}
    return {**decision, "tool": "recall_memory", "domain": "memory", "args": {},
            "gated": False, "reason": reason}


def _apply_question_precedence(text: str, out: dict, scores: dict) -> None:
    """route(): re-point a domain-expert claim on an evidence / event-time
    question (in place) and log ``INTENT_GATE <reason>=1 head=… conf=… routed=…``."""
    hit = question_precedence(text, out.get("domain"))
    if hit is None:
        return
    target, reason = hit
    ts = out.get("two_stage")
    head = (ts or {}).get("head_top") or out.get("domain")
    conf = (ts or {}).get("head_conf")
    if isinstance(ts, dict):
        out["two_stage"] = _evidence_decision(ts, target, reason)
    out["domain"] = target
    if out.get("routed") != "chat":
        out["routed"] = target
    out["score"] = round(float(scores.get(target, 0.0)), 3)
    logger.info("INTENT_GATE %s=1 head=%s conf=%s routed=%s", reason,
                head, "-" if conf is None else f"{float(conf):.4f}", target)


def head_verdict(text: str) -> Optional[dict]:
    """The ACTIVE two-stage head's stage-1 verdict on `text` (no sidecar call).

    None unless ZOE_ROUTER_HEAD=active and the router is enabled — i.e. the
    head only arbitrates keyword claims when it is also routing. See
    router_two_stage.head_verdict for the shape; an evidence-shaped or (flag on)
    event-time / own-fact question comes back re-pointed
    (``reason="evidence_question"`` / ``"event_time_question"`` /
    ``"own_fact_question"``). NEVER raises.
    """
    if not is_enabled() or head_mode() != "active":
        return None
    try:
        import router_two_stage

        verdict = router_two_stage.head_verdict(embed(text))
        # head_top: an unsure (below_gate) head must not wave a claim through
        hit = question_precedence(text, (verdict or {}).get("head_top"))
        return _evidence_decision(verdict, *hit) if hit else verdict
    except Exception as exc:
        logger.warning("head_verdict failed (non-fatal): %s", exc)
        return None


def route_two_stage(text: str) -> RouterDecision:
    """Run ONLY the two-stage decision for `text` (embed → MLP shortlist +
    gate → grammar-constrained sidecar decode). Standalone: needs the
    sidecar up, not the zoe-data server. Never raises."""
    t0 = time.perf_counter()
    try:
        _ensure_loaded()
        v = embed(text)
        import router_two_stage

        d = router_two_stage.decide(text, v)
    except Exception as exc:
        logger.warning("route_two_stage failed (non-fatal): %s", exc)
        d = None
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if d is None:
        return RouterDecision(None, {}, 0.0, "error_fallback", ms)
    conf = float(d.get("head_conf") or 0.0)
    if d.get("tool"):
        return RouterDecision(d["tool"], dict(d.get("args") or {}), conf,
                              "two_stage", ms)
    if d.get("gated"):
        return RouterDecision(None, {}, conf, "gate_abstain", ms)
    return RouterDecision(None, {}, conf, "shortlist_miss", ms)


def route(text: str) -> dict:
    """Classify an utterance. Returns {domain, score, routed, scores, ms}.

    `domain` = best-scoring domain; `routed` = domain if score>=threshold else
    'chat' (Tier-2). 'chat' domain itself always routes to chat.
    """
    _ensure_loaded()
    t0 = time.perf_counter()
    v = embed(text)
    sims = _MATRIX @ v
    scores = {d: float(sims[_DOM_IDX[d]].max()) for d in ROUTES}
    domain = max(scores, key=scores.get)
    score = scores[domain]
    thr = threshold()
    routed = domain if (score >= thr and domain != "chat") else "chat"
    _head_shadow(text, v, routed)
    out = {
        "domain": domain,
        "score": round(score, 3),
        "routed": routed,
        "scores": {k: round(v, 3) for k, v in sorted(scores.items(), key=lambda x: -x[1])},
        "ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    hm = head_mode()
    if hm == "shadow2":
        _two_stage_shadow2(text, v, routed)
    elif hm == "active":
        decision = _two_stage_active(text, v)
        if decision is not None:
            ts_domain = decision.get("domain") or "chat"
            out["two_stage"] = decision
            out["similarity_domain"] = domain
            out["similarity_routed"] = routed
            out["domain"] = ts_domain
            out["routed"] = "chat" if ts_domain == "chat" else ts_domain
            # keep `score` meaningful for downstream per-domain gates: the
            # similarity score OF the two-stage-chosen domain. A domain with
            # no similarity examples (notes/journal/music/smart_home) gets
            # 0.0 — never another domain's score — so expert per-domain
            # threshold gates deny rather than act on a borrowed confidence.
            out["score"] = round(float(scores.get(ts_domain, 0.0)), 3)
            _apply_question_precedence(text, out, scores)
            out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            # `routed` is still the SIMILARITY decision the two-stage pre-empted —
            # log it as the independent baseline (out["routed"] is now the
            # two-stage's own output, so it cannot serve as ground truth).
            _log_two_stage(_two_stage_rec(text, decision, "active",
                                          out["routed"], similarity_routed=routed))
        # decision None → similarity behavior unchanged (brain-safe)
    if "two_stage" not in out:
        _apply_question_precedence(text, out, scores)
    return out
