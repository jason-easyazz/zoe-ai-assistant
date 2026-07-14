"""Tier-1 embedding semantic router for Zoe's voice/chat path.

A local bge-small (ONNX, CPU, ~7ms/query) classifies an utterance into a domain
(calendar/lists/reminders/timers/weather/time/people/memory) or 'chat' (→ the
Tier-2 Pi brain). It sits BETWEEN the deterministic regex fast-path (Tier 0) and
the brain (Tier 2): regex-class latency with LLM-class fuzziness, without putting
the LLM in the routing hot-path (the approach that failed on the Jetson before).

Runs OBSERVE-ONLY by default (ZOE_ROUTER_MODE=shadow) — it logs its decision and
whether that agrees with what actually handled the turn, so accuracy can be
validated on live traffic before it ever changes behavior.

SetFit head (ZOE_ROUTER_HEAD=off|shadow, default off): a 38 KB logreg head
(labs/setfit-router) on the same embedding. In 'shadow' it logs prediction +
agreement per turn (utterance-hash only) and never influences routing; 'active'
is deliberately NotImplementedError until live agreement is confirmed. Score
the shadow log with scripts/maintenance/router_shadow_report.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
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


def head_mode() -> str:
    """ZOE_ROUTER_HEAD: 'off' (default) | 'shadow' | 'active' (not implemented)."""
    val = (os.environ.get("ZOE_ROUTER_HEAD", "off") or "off").strip().lower()
    if val in ("", "0", "false", "no", "off"):
        return "off"
    if val == "shadow":
        return "shadow"
    if val == "active":
        raise NotImplementedError(
            "ZOE_ROUTER_HEAD=active is not implemented yet — the head is "
            "shadow-only until live agreement confirms the offline numbers."
        )
    logger.warning("unknown ZOE_ROUTER_HEAD=%r — treating as 'off'", val)
    return "off"


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
    if head_mode() == "off":  # 'active' raises NotImplementedError here
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
    """SHADOW-ONLY head comparison. Never influences routing, never raises
    (except the explicit NotImplementedError for ZOE_ROUTER_HEAD=active).

    Logs a structured line keyed by an utterance HASH (no raw text at INFO —
    privacy) so live agreement can be scored later via
    scripts/maintenance/router_shadow_report.py.
    """
    if head_mode() != "shadow":  # 'active' raises NotImplementedError here
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
            with open(_HEAD_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
        except OSError as exc:
            shadow_logger.debug("shadow log append failed: %s", exc)
    except Exception as exc:  # shadow must never break a turn
        logger.warning("router head shadow failed (non-fatal): %s", exc)


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
    return {
        "domain": domain,
        "score": round(score, 3),
        "routed": routed,
        "scores": {k: round(v, 3) for k, v in sorted(scores.items(), key=lambda x: -x[1])},
        "ms": round((time.perf_counter() - t0) * 1000, 1),
    }
