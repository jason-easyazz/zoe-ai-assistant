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
(router_two_stage: MLP top-3 shortlist + 0.5 chat gate + grammar-constrained
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
# module already computes per turn (+~0.2 ms). SHADOW-ONLY today: it logs its
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


def shadow_log_segments(path: str = None) -> list[str]:
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
            import joblib

            head = joblib.load(_HEAD_PATH)
            # sanity: needs predict_proba + classes_ (sklearn LogisticRegression)
            if not (hasattr(head, "predict_proba") and hasattr(head, "classes_")):
                raise TypeError(f"unexpected head artifact type {type(head)!r}")
            _HEAD = head
            logger.info("semantic_router head loaded %s (%d classes)",
                        _HEAD_PATH, len(head.classes_))
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
      gate_abstain    stage-1 said chat (top==chat or conf < gate) → brain
      shortlist_miss  the decoder took the chat escape / no legal tool → brain
      error_fallback  head/sidecar failure or timeout → brain
    """
    tool: Optional[str]
    args: dict = field(default_factory=dict)
    confidence: float = 0.0
    source: str = "error_fallback"
    latency_ms: float = 0.0


def route_two_stage(text: str) -> RouterDecision:
    """Run ONLY the two-stage decision for `text` (embed → MLP shortlist +
    gate → grammar-constrained sidecar decode). Standalone: needs the
    sidecar up, not the zoe-data server. Never raises."""
    t0 = time.perf_counter()
    try:
        _ensure_loaded()
        v = np.asarray(next(iter(_MODEL.embed([text or ""]))), dtype=np.float32)
        v /= (np.linalg.norm(v) + 1e-9)
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
    v = np.asarray(next(iter(_MODEL.embed([text or ""]))), dtype=np.float32)
    v /= (np.linalg.norm(v) + 1e-9)
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
            out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            # `routed` is still the SIMILARITY decision the two-stage pre-empted —
            # log it as the independent baseline (out["routed"] is now the
            # two-stage's own output, so it cannot serve as ground truth).
            _log_two_stage(_two_stage_rec(text, decision, "active",
                                          out["routed"], similarity_routed=routed))
        # decision None → similarity behavior unchanged (brain-safe)
    return out
