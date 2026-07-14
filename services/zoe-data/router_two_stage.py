"""Two-stage active router: SetFit MLP shortlist → FunctionGemma sidecar.

The proven 90.1% config from labs/router-90-campaign (results/r2-gb-mlp-g0.5):

  Stage 1  MLP head (models/router_head_mlp.joblib, trained in
           labs/setfit-router on the SAME frozen bge-small embedding
           semantic_router already computes per turn) → top-3 non-chat
           domain shortlist + chat gate 0.5 (top==chat OR conf<gate → no
           tool call, fall to the brain).
  Stage 2  FunctionGemma-270M round-2 fine-tune served by a resident
           llama-server SIDECAR (:11436, CPU, ~600 MB — unit
           scripts/setup/systemd/functiongemma-router.service) decoding
           ONE complete tool call under a GBNF grammar restricted to the
           shortlist's tools (+ the <unused20> chat escape).

Measured on the frozen 81-case corpus: 90.1% overall / 100% canonical /
0% chat false-positives / 424 ms p50.

This module is the DECISION only. Execution stays in the existing intent
dispatch (fast_tiers → expert_dispatch → intent_router); the brain
(Gemma 4 E4B — the rock, see docs/CANONICAL.md) remains the fallback for
every gate-abstain, shortlist miss, sidecar failure, timeout, or
malformed output. `decide()` NEVER raises: any failure returns None and
the caller falls through exactly as before.

Env (defaults = the proven config):
  ZOE_ROUTER_SIDECAR_URL          http://127.0.0.1:11436
  ZOE_ROUTER_TWO_STAGE_GATE       0.5
  ZOE_ROUTER_TWO_STAGE_TIMEOUT_S  1.5   (strict client timeout → brain)
  ZOE_ROUTER_HEAD_MLP_PATH        services/zoe-data/models/router_head_mlp.joblib
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Static router policy (mirrors labs/two-stage-router-eval) ───────────────
# Which of Zoe's 20 tools a stage-1 domain unlocks in the stage-2 grammar.
DOMAIN_TOOLS: dict[str, list[str]] = {
    "time": ["get_time"],
    "weather": ["get_weather"],
    "lists": ["shopping_list_add", "add_to_list", "show_list", "list_remove"],
    "calendar": ["show_calendar", "add_calendar_event"],
    "reminders": ["add_reminder", "list_reminders"],
    "timers": ["set_timer"],
    "notes": ["create_note", "note_search"],
    "journal": ["journal"],
    "people": ["people"],
    "music": ["media"],
    "smart_home": ["home"],
    "memory": ["recall_memory", "remember_fact", "remember_emotional_moment"],
    "chat": [],
}
TOOL_DOMAIN: dict[str, str] = {
    t: d for d, tools in DOMAIN_TOOLS.items() for t in tools
}
# Legal argument keys per tool (labs/functiongemma-finetune/zoe_tools.json).
# Unknown keys are dropped during validation; they never fail the route.
TOOL_ARGS: dict[str, set[str]] = {
    "get_time": {"query"},
    "recall_memory": {"query"},
    "shopping_list_add": {"item"},
    "get_weather": {"forecast", "location"},
    "list_reminders": {"qualifier"},
    "show_calendar": {"qualifier"},
    "show_list": {"list_type"},
    "set_timer": {"minutes", "label"},
    "add_reminder": {"title", "date", "time"},
    "add_calendar_event": {"title", "date", "time", "category"},
    "create_note": {"content", "title"},
    "add_to_list": {"item", "list_type"},
    "list_remove": {"item", "list_type"},
    "note_search": {"query"},
    "journal": {"action", "content", "mood"},
    "people": {"action", "name", "relationship", "query", "notes"},
    "media": {"action", "query", "command", "level", "direction"},
    "home": {"action", "room"},
    "remember_fact": {"fact"},
    "remember_emotional_moment": {"moment", "valence", "intensity"},
}

# The fine-tune's ~47-token routing prompt (labs/two-stage-router-eval).
SYSTEM_PROMPT = (
    "You are Zoe's router. Answer with the routing token for the user's "
    "utterance, then the function call arguments."
)

_HEAD = None
_HEAD_FAILED = False
_LOCK = threading.Lock()


def sidecar_url() -> str:
    return (os.environ.get("ZOE_ROUTER_SIDECAR_URL")
            or "http://127.0.0.1:11436").rstrip("/")


def gate() -> float:
    try:
        return float(os.environ.get("ZOE_ROUTER_TWO_STAGE_GATE", "0.5"))
    except Exception:
        return 0.5


def timeout_s() -> float:
    try:
        return float(os.environ.get("ZOE_ROUTER_TWO_STAGE_TIMEOUT_S", "1.5"))
    except Exception:
        return 1.5


def _head_path() -> str:
    return os.environ.get(
        "ZOE_ROUTER_HEAD_MLP_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "models", "router_head_mlp.joblib"),
    )


def _ensure_head():
    """Lazy-load the MLP head; a load failure disables two-stage for good."""
    global _HEAD, _HEAD_FAILED
    if _HEAD is not None or _HEAD_FAILED:
        return _HEAD
    with _LOCK:
        if _HEAD is not None or _HEAD_FAILED:
            return _HEAD
        try:
            import joblib

            head = joblib.load(_head_path())
            if not (hasattr(head, "predict_proba") and hasattr(head, "classes_")):
                raise TypeError(f"unexpected head artifact {type(head)!r}")
            _HEAD = head
            logger.info("two_stage head loaded %s (%d classes)",
                        _head_path(), len(head.classes_))
        except Exception as exc:
            _HEAD_FAILED = True
            logger.warning("two_stage head load failed (two-stage disabled, "
                           "non-fatal): %s", exc)
    return _HEAD


def build_grammar(names: list[str]) -> str:
    """GBNF constraining output to one legal call or the chat escape.

    Verified live against llama.cpp build b9733 on this box: special-token
    pieces (<start_function_call>, <unusedK>) match GBNF string literals;
    <unused20> is not rendered into content, so empty output == chat.
    """
    name_alt = " | ".join(f'"{n}"' for n in names)
    prefix_alt = " | ".join(f'"<unused{i}>"' for i in range(21))
    return "\n".join([
        "root ::= chat | call",
        'chat ::= "<unused20>"',
        f"prefix ::= {prefix_alt}",
        ('call ::= prefix? "<start_function_call>call:" name '
         '"{" inner "}" "<end_function_call>"'),
        f"name ::= {name_alt}",
        "inner ::= [^{}]*",
    ])


_CALL_RE = re.compile(r"call:([a-zA-Z0-9_]+)\{(.*)\}", re.DOTALL)
_ARG_RE = re.compile(
    r"([a-zA-Z0-9_]+):(<escape>.*?<escape>|[^,{}]*)", re.DOTALL)


def parse_call(raw: str) -> tuple[Optional[str], dict[str, Any]]:
    """Parse `call:name{k:<escape>v<escape>,k2:literal}` → (name, args).

    Empty/escape-only output (the <unused20> chat escape) → (None, {}).
    Args are best-effort: a junk inner never fails the route — the tool
    NAME is the routing decision; args are advisory (logged, validated).
    """
    m = _CALL_RE.search(raw or "")
    if not m:
        return None, {}
    name, inner = m.group(1), m.group(2)
    args: dict[str, Any] = {}
    for k, v in _ARG_RE.findall(inner):
        v = v.strip()
        if v.startswith("<escape>") and v.endswith("<escape>"):
            args[k] = v[len("<escape>"):-len("<escape>")]
        elif v.lower() in ("true", "false"):
            args[k] = v.lower() == "true"
        else:
            try:
                args[k] = int(v)
            except ValueError:
                try:
                    args[k] = float(v)
                except ValueError:
                    args[k] = v
    return name, args


def validate_call(name: Optional[str], args: dict[str, Any],
                  legal: list[str]) -> tuple[Optional[str], dict[str, Any]]:
    """Keep only a legal tool name + its schema'd arg keys."""
    if name is None or name not in legal or name not in TOOL_ARGS:
        return None, {}
    return name, {k: v for k, v in args.items() if k in TOOL_ARGS[name]}


def _post_sidecar(text: str, grammar: str) -> str:
    payload = {
        "model": "functiongemma", "temperature": 0, "max_tokens": 64,
        "stop": ["<end_function_call>"], "grammar": grammar,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": text}],
    }
    req = urllib.request.Request(
        f"{sidecar_url()}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s()) as r:
        resp = json.load(r)
    return resp["choices"][0]["message"].get("content") or ""


def sidecar_healthy(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{sidecar_url()}/health",
                                    timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def decide(text: str, vec) -> Optional[dict]:
    """Full two-stage decision for `text` given its (normalized) embedding.

    Returns a decision dict or None (= caller keeps its existing behavior):
      {"tool": str|None, "domain": str, "args": {...}, "shortlist": [...],
       "head_top": str, "head_conf": float, "gated": bool, "ms": float}
    tool None + domain "chat" = confident no-tool (gate abstain or the
    decoder's chat escape). None = infrastructure failure → brain fallback.
    NEVER raises.
    """
    try:
        import numpy as np

        head = _ensure_head()
        if head is None:
            return None
        t0 = time.perf_counter()
        proba = head.predict_proba(np.asarray(vec, dtype=np.float32)
                                   .reshape(1, -1))[0]
        order = proba.argsort()[::-1]
        classes = head.classes_
        top = str(classes[order[0]])
        conf = float(proba[order[0]])
        shortlist = [str(c) for c in classes[order] if c != "chat"][:3]
        base = {"shortlist": shortlist, "head_top": top,
                "head_conf": round(conf, 4)}
        if top == "chat" or conf < gate():
            return {**base, "tool": None, "domain": "chat", "args": {},
                    "gated": True,
                    "ms": round((time.perf_counter() - t0) * 1000, 1)}
        legal = [n for d in shortlist for n in DOMAIN_TOOLS.get(d, [])]
        if not legal:
            return None
        raw = _post_sidecar(text, build_grammar(legal))
        name, args = parse_call(raw)
        name, args = validate_call(name, args, legal)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        if name is None:
            # decoder chose the chat escape (or emitted junk): no tool call
            return {**base, "tool": None, "domain": "chat", "args": {},
                    "gated": False, "ms": ms}
        return {**base, "tool": name, "domain": TOOL_DOMAIN.get(name, "chat"),
                "args": args, "gated": False, "ms": ms}
    except Exception as exc:  # sidecar down/timeout/anything → brain fallback
        logger.warning("two_stage decide failed (non-fatal, → fallback): %s",
                       exc)
        return None
