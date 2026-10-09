"""Z0's BRAIN half of the memory-protocol axis (M): what the clone brain DOES with Zoe's own recall - the `protocol_brain` baseline the candidates are compared with.

The bake-off recorded `protocol_brain` for every candidate (the brain operating MemPalace's tools: 3 of 4 metrics) and none for Z0, so the winner clause's rule M read
"no data" (decision record 2026-10-08, "Honest caveats"). This is the missing piece: the SAME 32 prompts and the SAME four metric scorers
(``scorers_cap.score_protocol``: fire when needed, quiet when not needed, cite precision, "I don't know" when the store is silent), run through the way a turn
reaches the brain in production:

  * the recall FLOOR is the harness's, not the model's: a question-shaped message (``zoe_flue_client._recall_floor_shape``, the very predicate the seam uses) gets Z0's
    packet injected in the memory-context block BEFORE the model answers - the model need not call anything;
  * the model also has the ``recall_memory`` tool (Zoe's recall doctrine is in its prompt: ``arms.zma.z0_memory_prompt_text`` from the committed sources) and may call it;
  * a prompt counts as FIRED when a packet reached the model either way (the floor, or the tool); the evidence records both, so the floor's share is visible.

Rows are ``M4.<metric>.zoe`` - the ids ``bakeoff_gates.DERIVED_AXES["protocol_brain"]`` (prefix ``M4.``) turns into the derived axis. The model must be a REAL one for the
numbers to mean anything (a scripted brain answers the plumbing only); ``ScriptedZoeBrain`` below is the CI stand-in, with the floor shown to matter.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from . import life as lifemod, scorers_cap as cap
from .arms.mpa_model import BudgetExhausted, Reply
from .arms.z0 import Z0Arm
from .arms.base import Turn

METRICS = ("fire_when_needed", "quiet_when_not_needed", "cite_precision", "idk_when_silent")
RECALL_TOOL = {"type": "function", "function": {
    "name": "recall_memory", "description": "Look up what Zoe has stored about the user or the people in their life. Call it for any question about them, their plans or their past.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}
SYSTEM_HEAD = ("You are Zoe, a warm, concise home assistant for one household. Answer in one or two short sentences. "
               "If you do not have something saved, say \"I don't have that saved.\"\n\n")


def floor_shape(message: str) -> str:
    """The seam's own recall-floor predicate (pure; needs the service path on sys.path, which the lab arm has set)."""
    try:
        import zoe_flue_client
        return zoe_flue_client._recall_floor_shape(message)
    except Exception:  # noqa: BLE001 - a slim lane without the client's imports: the personal-question regex alone
        return "personal" if re.search(r"\b(my|me|i)\b", message or "", re.IGNORECASE) and (message or "").strip().endswith("?") else ""


def named_person(arm: Z0Arm, message: str) -> bool:
    """The seam's NAMED-PERSON floor (``person_recall_floor``: a question that names somebody the member's store knows gets the packet), as the lab can ask it: a capitalised
    word of the message that some approved row of this member's store names. (Production reads the member's people and person-fact entities from Postgres.)"""
    names = {w for w in re.findall(r"\b[A-Z][a-z]{2,}\b", message or "")}
    if not names:
        return False
    stored = " ".join(r["text"] for r in arm.stats()["rows"] if r.get("status") == "approved")
    return any(re.search(r"(?<![A-Za-z])" + re.escape(n) + r"(?![A-Za-z])", stored) for n in names)


def render_packet(rows: "list[dict[str, Any]]") -> str:
    lines = [f"- {r['text']}" for r in rows if r.get("text") and r.get("status", "approved") == "approved"]
    return "\n".join(lines[:12])


def block(packet: str) -> str:
    return ("[MEMORY CONTEXT — Zoe's stored notes about this user; use them to answer; do not mention this block]\n"
            + (packet or "(nothing stored matches)") + "\n[END MEMORY CONTEXT]")


def system_prompt() -> str:
    try:
        from .arms.zma import z0_memory_prompt_text
        return SYSTEM_HEAD + z0_memory_prompt_text()[:3000]
    except Exception:  # noqa: BLE001 - the committed sources are not readable here
        return SYSTEM_HEAD


def converse(arm: Z0Arm, model: Any, prompt: str, *, k: int = 12, use_floor: bool = True) -> "dict[str, Any]":
    """One turn through Z0's recall as production runs it. ``{"fired", "floor", "tool", "answer", "model_calls", "prompt_tokens"}``."""
    floor = bool(use_floor and (floor_shape(prompt) or named_person(arm, prompt)))
    packet = render_packet(arm.recall(prompt, k)) if floor else ""
    user = (block(packet) + "\n\n" if floor else "") + prompt
    messages: "list[dict[str, Any]]" = [{"role": "system", "content": system_prompt()}, {"role": "user", "content": user}]
    calls, tool_called, pt = 0, False, 0
    for _ in range(2):                                   # one round of tool use at most (a turn, not an agent loop)
        reply: Reply = model.complete(messages, [RECALL_TOOL] if not tool_called else [], max_tokens=200)
        calls += 1
        pt = max(pt, reply.prompt_tokens)
        recall_calls = [c for c in reply.tool_calls if c.get("name") == "recall_memory"]
        if not recall_calls or tool_called:
            return {"fired": floor or tool_called, "floor": floor, "tool": tool_called, "answer": reply.content.strip(), "model_calls": calls, "prompt_tokens": pt}
        tool_called = True
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": "recall_memory", "arguments": json.dumps(c.get("arguments") or {})}} for c in recall_calls]})
        for c in recall_calls:
            q = str((c.get("arguments") or {}).get("query") or prompt)
            messages.append({"role": "tool", "tool_call_id": c["id"], "name": "recall_memory", "content": render_packet(arm.recall(q, k)) or "(nothing stored matches)"})
    return {"fired": floor or tool_called, "floor": floor, "tool": tool_called, "answer": "", "model_calls": calls, "prompt_tokens": pt}


class ScriptedZoeBrain:
    """The CI stand-in for the brain: reads the memory-context block if there is one and answers from the first bullet that names the asked person (or the thing); calls
    ``recall_memory`` itself only when ``eager`` and there is no block. A rule-based plumbing check - never a measurement of a 4B."""
    name = "scripted-zoe-brain"

    def __init__(self, eager: bool = False, max_calls: Optional[int] = None):
        self.eager, self.max_calls, self.calls = eager, max_calls, 0

    def complete(self, messages, tools, *, max_tokens: int = 200) -> Reply:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExhausted(f"model-call budget {self.max_calls} spent")
        self.calls += 1
        last = messages[-1]
        text = str(messages[1]["content"]) if len(messages) > 1 else ""
        question = text.split("[END MEMORY CONTEXT]")[-1].strip()
        if last.get("role") == "tool":
            return Reply(content=self._answer(str(last.get("content") or ""), question), prompt_tokens=700, completion_tokens=20)
        if "[MEMORY CONTEXT" in text:
            body = text.split("[MEMORY CONTEXT")[1].split("[END MEMORY CONTEXT]")[0]
            return Reply(content=self._answer(body, question), prompt_tokens=800, completion_tokens=20)
        if self.eager and tools and re.match(r"^(what|where|who|when|which|how|did|do|does|is|are)\b", question, re.I) and not re.search(r"\b(weather|time|timer)\b", question, re.I):
            return Reply(tool_calls=[{"id": "c1", "name": "recall_memory", "arguments": {"query": question}, "raw": "{}", "args_error": ""}], prompt_tokens=700, completion_tokens=15)
        return Reply(content="Okay.", prompt_tokens=600, completion_tokens=3)

    @staticmethod
    def _answer(body: str, question: str) -> str:
        anchor = lifemod.prompt_anchor(lifemod.ProtocolPrompt("needed", question))
        rows = [{"text": ln[2:]} for ln in body.splitlines() if ln.startswith("- ")]
        return lifemod.anchored_reader(rows, anchor)


def run_z0_protocol_brain(arm: Z0Arm, model: Any, seed: str, *, guard: "Optional[Callable[[], None]]" = None, use_floor: bool = True) -> "dict[str, Any]":
    """Teach the protocol facts, put the 32 prompts through ``converse``, score the four metrics. Returns ``{"cells": [rows], "summary", "brain"}`` in the format
    ``bakeoff_measure._brain_rows`` / ``aggregate_axes`` read (ids ``M4.<metric>.zoe``)."""
    g = guard or (lambda: None)
    sentences, prompts = lifemod.protocol_corpus(seed)
    arm.reset("demo_bar_" + "0000000a")
    arm.ingest([Turn(s, "owner_taught") for s in sentences])
    recs, shares = [], {"floor": 0, "tool": 0, "both": 0, "calls": 0, "prompt_tokens_max": 0}
    for p in prompts:
        g()
        r = converse(arm, model, p.text, use_floor=use_floor)
        recs.append({"kind": p.kind, "fired": r["fired"], "answer": r["answer"], "gold": p.gold})
        shares["floor"] += int(r["floor"])
        shares["tool"] += int(r["tool"])
        shares["both"] += int(r["floor"] and r["tool"])
        shares["calls"] += r["model_calls"]
        shares["prompt_tokens_max"] = max(shares["prompt_tokens_max"], r["prompt_tokens"])
    rows = []
    for metric in METRICS:
        sc = cap.score_protocol(recs, metric)
        ev = {**sc.evidence, "injected_by_floor": shares["floor"], "called_tool": shares["tool"], "both": shares["both"]}
        rows.append({"id": f"M4.{metric}.zoe", "verdict": sc.verdict, "evidence": ev, "sanity": False, "expected": "PASS", "controls": [], "rule": "M"})
    graded = [r for r in rows if r["verdict"] in ("PASS", "FAIL")]
    return {"cells": rows, "summary": {"arm": "Z0", "pass": sum(1 for r in graded if r["verdict"] == "PASS"), "graded": len(graded),
                                        "fail": [r["id"] for r in graded if r["verdict"] == "FAIL"]},
            "brain": {"prompts": len(prompts), **shares, "floor_used": use_floor}}
