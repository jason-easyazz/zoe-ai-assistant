"""The planner seam of the plan loop: the brain PROPOSES a plan as constrained JSON, code decides what survives.

``maybe_run`` is the one entry a turn would call (nothing calls it yet). In order:

  1. ``ZOE_PLAN_LOOP`` off -> None before anything else.
  2. ``looks_multi_part`` (deterministic, no model): a turn that does not join two different actions returns None at once, so a
     single-tool turn never reaches the planner and keeps today's path untouched.
  3. A turn the distress / medical floor owns returns None: it is never planned.
  4. The planner is asked ONCE. It is given the speaker's words and the tool list - nothing else: no history, no memory, no
     list contents, no calendar titles, no tool output. The plan is frozen before the first tool runs, so text inside a list
     item or an event title has no path to the planner.
  5. ``parse_plan`` validates the JSON in code. A step is DROPPED (never repaired, never guessed) when its tool is not in the
     registry, an argument is outside the schema, or its words are not the speaker's (``ungrounded``: every content word of
     the step's ``part`` and of its text arguments must occur in the utterance). A plan with fewer than two parts left returns
     None (a single-tool turn).
  6. shadow: ``plan_loop.run`` judges and logs (codes and counts), executes nothing, and the turn carries on as today (None).
     on: the plan runs and the reply is rendered from receipts and passed through the write-claim guard.

The real planner call (``brain_planner``) goes to the Gemma brain inside ``brain_scheduler.declare("turn:plan_loop")`` (a call
made for a person's turn), with llama-server's JSON-schema response format. Tests and the lab pass a scripted fake.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional

import plan_loop
import plan_loop_reply

logger = logging.getLogger(__name__)

PLANNER_TIMEOUT_S = 8.0
MAX_RAW_CHARS = 6000
MAX_PLANNED = 12                     # steps READ from the planner; the gate still refuses a plan of more than MAX_STEPS

_S, _N, _B = "str", "num", "bool"
#: tool -> {argument: (type, required)}: the only arguments a planned step may carry (mirrors the Flue tool lane's slots)
TOOL_ARGS: Mapping[str, Mapping[str, tuple[str, bool]]] = {
    "list_add": {"item": (_S, True), "list_type": (_S, False)},
    "list_remove": {"item": (_S, True), "list_type": (_S, False)},
    "list_show": {"list_type": (_S, False)},
    "reminder_create": {"title": (_S, True), "date": (_S, False), "time": (_S, False)},
    "reminder_list": {},
    "calendar_create": {"title": (_S, True), "date": (_S, False), "time": (_S, False), "category": (_S, False)},
    "calendar_show": {"qualifier": (_S, False)},
    "timer_create": {"minutes": (_N, True), "label": (_S, False)},
    "note_create": {"content": (_S, True), "title": (_S, False)},
    "note_search": {"query": (_S, True)},
    "memory_store": {"text": (_S, True)},
    "memory_retire": {"pick": (_N, False)},
    "smart_home": {"action": (_S, True), "entity": (_S, False), "room": (_S, False)},
    "weather": {"location": (_S, False), "forecast": (_B, False)},
    "time_query": {}, "date_query": {},
}
#: words a step may use that the speaker need not have said: grammar, the tool nouns, and the values the tools default to
_FILLER = frozenset("a an the to my me i on in at of for from off and then with is are it that this what whats please zoe "
                    "list lists reminder reminders calendar timer note notes memory shopping todo today tomorrow tonight "
                    "light lights true false".split())
_JOINERS = frozenset({"and", "then", "also", "plus", "after"})
_ACTIONS = frozenset("add put remove take delete set remind schedule book move tell what whats show read check remember "
                     "forget start cancel create make note turn switch save rename clear change".split())
_WORD = re.compile(r"[a-z0-9]+")


def _words(text: Any) -> list[str]:
    folded = unicodedata.normalize("NFKC", str(text or "")).casefold().replace("'", "").replace("’", "")
    return _WORD.findall(folded)


def looks_multi_part(utterance: str) -> bool:
    """Cheap and deterministic: two DIFFERENT action words and something joining them. "add milk and eggs" is one action."""
    words = _words(utterance)
    joined = bool(_JOINERS & set(words)) or bool(re.search(r"[,;]", utterance or ""))
    return joined and len(_ACTIONS & set(words)) >= 2


def grounded(text: Any, said: frozenset) -> bool:
    """True when every content word of ``text`` is a word the speaker said (numbers pass: dates and times get normalised)."""
    return all(w in said or w in _FILLER or w.isdigit() for w in _words(text))


@dataclass(frozen=True)
class Parsed:
    steps: tuple[plan_loop.Step, ...] = ()
    unplanned: tuple[str, ...] = ()      # parts of the ask (the speaker's words) no tool could be planned for
    dropped: tuple[str, ...] = ()        # one code per dropped step: not_json | bad_shape | unknown_tool | bad_args | ungrounded | needs_dropped

    @property
    def parts(self) -> int:
        return len(self.steps) + len(self.unplanned)


def _typed(value: Any, kind: str) -> bool:
    if kind == _S:
        return isinstance(value, str) and bool(value.strip())
    if kind == _N:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, bool)


def _args_code(tool: str, args: Mapping[str, Any], said: frozenset) -> str:
    """"" when the arguments fit the tool's schema and are the speaker's words. A permission-shaped key is left for the gate
    (``widens_permissions`` refuses the whole plan, which is stronger than dropping one step)."""
    if any(isinstance(k, str) and k.strip().casefold() in plan_loop.FORBIDDEN_ARG_KEYS for k in args):
        return ""
    if tool in plan_loop.FRIDGE_RULE_TOOLS:
        return ""
    schema = TOOL_ARGS[tool]
    if any(k not in schema for k in args) or any(req and name not in args for name, (_, req) in schema.items()):
        return "bad_args"
    if not all(_typed(v, schema[k][0]) for k, v in args.items()):
        return "bad_args"
    return "" if all(grounded(v, said) for v in args.values() if isinstance(v, str)) else "ungrounded"


def parse_plan(raw: str, utterance: str) -> Parsed:
    """Validate the planner's JSON against the registry and the speaker's own words. Never raises, never repairs."""
    try:
        data = json.loads((raw or "")[:MAX_RAW_CHARS])
        items = data["steps"]
        if not isinstance(items, list):
            raise ValueError
    except Exception:  # noqa: BLE001
        return Parsed(dropped=("not_json",))
    said = frozenset(_words(utterance))
    kept: list[tuple[int, plan_loop.Step]] = []
    unplanned: list[str] = []
    dropped: list[str] = []
    for i, item in enumerate(items[:MAX_PLANNED]):
        ok_shape = (isinstance(item, Mapping) and isinstance(item.get("tool"), str) and isinstance(item.get("part"), str)
                    and isinstance(item.get("args", {}), Mapping) and isinstance(item.get("needs", []), list))
        if not ok_shape:
            dropped.append("bad_shape")
            continue
        tool, part, args = item["tool"].strip(), item["part"].strip(), dict(item.get("args", {}))
        if not part or not grounded(part, said):
            dropped.append("ungrounded")                    # not the speaker's words: not even reported back as a part
            continue
        code = "unknown_tool" if tool not in TOOL_ARGS and tool not in plan_loop.FRIDGE_RULE_TOOLS else _args_code(tool, args, said)
        needs = tuple(n for n in item.get("needs", []) if isinstance(n, int) and not isinstance(n, bool))
        if not code and any(n not in {k for k, _ in kept} for n in needs):
            code = "needs_dropped"
        if code:
            dropped.append(code)
            if code != "ungrounded" and part not in unplanned:
                unplanned.append(part)                      # the person asked for it; say so rather than guess a tool
            continue
        kept.append((i, plan_loop.Step(tool, args, part, needs)))
    index = {old: new for new, (old, _) in enumerate(kept)}
    steps = tuple(plan_loop.Step(s.tool, s.args, s.part, tuple(index[n] for n in s.needs)) for _, s in kept)
    extra = data.get("no_tool") if isinstance(data, Mapping) else None
    for part in extra if isinstance(extra, list) else ():
        if isinstance(part, str) and part.strip() and grounded(part, said) and part.strip() not in unplanned:
            unplanned.append(part.strip())
    return Parsed(steps, tuple(unplanned[:MAX_PLANNED]), tuple(dropped))


# ── the real planner: one constrained brain call ─────────────────────────────────────────────────────────────────────────────

def plan_schema() -> dict:
    step = {"type": "object", "additionalProperties": False, "required": ["tool", "args", "part"],
            "properties": {"tool": {"type": "string", "enum": sorted(TOOL_ARGS)}, "args": {"type": "object"},
                           "part": {"type": "string"}, "needs": {"type": "array", "items": {"type": "integer"}}}}
    return {"type": "object", "additionalProperties": False, "required": ["steps", "no_tool"],
            "properties": {"steps": {"type": "array", "maxItems": plan_loop.MAX_STEPS, "items": step},
                           "no_tool": {"type": "array", "items": {"type": "string"}}}}


def system_prompt() -> str:
    tools = "\n".join(f"- {tool}({', '.join(name + ('' if req else '?') for name, (_, req) in spec.items())})"
                      for tool, spec in sorted(TOOL_ARGS.items()))
    return ("Split the person's request into steps. Reply with JSON only.\n"
            "Each step uses ONE tool from this list and nothing else:\n" + tools + "\n"
            "\"part\" is the exact words of the request that the step does, copied, not rephrased. Argument text is copied from "
            "the request too. If a part of the request has no tool here, put its exact words in \"no_tool\" and do not "
            "approximate it with another tool. Never add a step the person did not ask for.")


async def brain_planner(utterance: str) -> str:
    """One JSON-constrained call to the Gemma brain, declared to the brain scheduler as part of a person's turn."""
    import httpx

    import brain_scheduler
    from gemma_endpoint import gemma_base

    body = {"model": "gemma", "max_tokens": 400, "temperature": 0.0,
            "messages": [{"role": "system", "content": system_prompt()}, {"role": "user", "content": utterance[:1000]}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "zoe_plan", "schema": plan_schema()}}}
    with brain_scheduler.declare("turn:plan_loop"):
        async with httpx.AsyncClient(timeout=PLANNER_TIMEOUT_S) as client:
            resp = await client.post(f"{gemma_base()}/v1/chat/completions", json=body)
            resp.raise_for_status()
            return str(resp.json()["choices"][0]["message"]["content"])


def intent_executor(speaker: plan_loop.Speaker, utterance: str) -> plan_loop.Executor:
    """Run a step through the SAME in-process fulfilment ``POST /api/system/intent-dispatch`` uses, including its write-intent
    gate, so receipts are written in the step's own context. A gate verdict or a 4xx is a definite "did not run"."""
    async def execute(step: plan_loop.Step) -> Mapping[str, Any]:
        import write_intent_gate

        write_intent_gate.note_turn(speaker.user_id, utterance)
        if write_intent_gate.check(speaker.user_id, step.tool, dict(step.args)):
            raise plan_loop.StepRejected("write_not_asked")
        from intent_router import Intent, execute_intent

        result = await execute_intent(Intent(name=step.tool, slots=dict(step.args)), user_id=speaker.user_id)
        return {"ok": result is not None, "result": result or ""}

    return execute


# ── the entry ────────────────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LoopReply:
    text: str
    result: plan_loop.PlanResult
    parsed: Parsed


Planner = Callable[[str], Awaitable[str]]
#: said when the loop broke after it may have run a tool: no claim, and the turn does not fall back to a path that could redo it
BROKE = "Something went wrong part way through, so I can't tell you what was done. Please check before asking again."


async def maybe_run(utterance: str, speaker: plan_loop.Speaker, *, started: float,
                    execute: Optional[plan_loop.Executor] = None, planner: Planner = brain_planner,
                    floors: plan_loop.Floors = plan_loop.safety_floor, session_id: str = "",
                    step_timeout_s: float = plan_loop.STEP_TIMEOUT_S) -> Optional[LoopReply]:
    """The reply for a multi-part ask, or None = "not mine, carry on as today" (off, shadow, a single-part turn, a safety-floor
    turn, or a planner that failed). ``started`` is the caller's ``write_receipts.begin()``. Never raises."""
    mode = plan_loop.mode()
    if mode == "off" or not looks_multi_part(utterance):
        return None
    try:
        if floors(utterance):
            return None
        began = time.monotonic()
        try:
            raw = await asyncio.wait_for(planner(utterance), PLANNER_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - no plan = today's path
            logger.info("PLAN_LOOP_PLAN mode=%s outcome=planner_failed error=%s", mode, type(exc).__name__)
            return None
        parsed = parse_plan(raw, utterance)
        logger.info("PLAN_LOOP_PLAN mode=%s outcome=%s steps=%d unplanned=%d dropped=%s tools=%s ms=%.0f", mode,
                    "planned" if parsed.steps and parsed.parts >= 2 else "single", len(parsed.steps), len(parsed.unplanned),
                    ",".join(sorted(parsed.dropped)) or "-", ",".join(s.tool for s in parsed.steps) or "-",
                    (time.monotonic() - began) * 1000.0)
        if not parsed.steps or parsed.parts < 2:
            return None
    except Exception as exc:  # noqa: BLE001 - nothing has run yet: today's path
        logger.warning("PLAN_LOOP entry failed before any step (%s)", type(exc).__name__)
        return None
    # From here a tool may run, so the turn is the loop's: it must never fall back to a path that could do the writes again.
    try:
        result = await plan_loop.run(parsed.steps, speaker, utterance=utterance,
                                     execute=execute or intent_executor(speaker, utterance), floors=floors,
                                     max_seconds=plan_loop.MAX_SECONDS - (time.monotonic() - began),
                                     step_timeout_s=step_timeout_s)
        if mode != "on" or result.status not in ("ran", "refused"):
            return None                                       # shadow: judged and logged, nothing executed, nothing said
        text = plan_loop_reply.speak(result, speaker, started, utterance=utterance, plan=parsed.steps,
                                     unplanned=parsed.unplanned, session_id=session_id)
        return LoopReply(text, result, parsed) if text else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("PLAN_LOOP failed after it began (%s)", type(exc).__name__)
        return LoopReply(BROKE, plan_loop.PlanResult("ran"), parsed) if mode == "on" else None
