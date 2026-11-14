"""The truthful reply of the plan loop: receipts in, sentences out. No model writes a word of it.

``render`` turns a ``plan_loop.PlanResult`` into one sentence per part - done / not done / not sure - in plan order. The words
of a "done" sentence come from the TOOL that ran (``I added floss to the shopping list``), never from how the person phrased the
ask: a new reminder is "I set a reminder", it is never called a "move". A sentence that says something was done is produced on
exactly one branch (``status == "done"``), so the reply cannot claim more than the receipts show.

``speak`` then passes that text through the existing write-claim guard (``write_claim_guard.guard_text``) with the CALLER's turn
start. The loop re-recorded every proven write in the caller's turn, so the guard finds a receipt for each "done" sentence and
for nothing else: it is a second, independent check, and if this renderer ever claimed an unproven write the guard would replace
the sentence (pinned by a negative control in ``tests/test_plan_loop_reply.py``).

A plan refused by a safety floor renders as "" on purpose: the distress / medical floor owns that turn's reply.

English only (v1). The phrases are data in this file; arguments are cut to one short clause before they are spoken.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

import plan_loop

#: tool -> (what Zoe says when a receipt proves it, the same act as an infinitive for "couldn't" / "tried to")
PHRASES: Mapping[str, tuple[str, str]] = {
    "list_add": ("I added {item} to the {list_type} list.", "add {item} to the {list_type} list"),
    "list_remove": ("I took {item} off the {list_type} list.", "take {item} off the {list_type} list"),
    "reminder_create": ("I set a reminder: {title}{when}.", "set a reminder: {title}{when}"),
    "calendar_create": ("I added {title} to your calendar{when}.", "add {title} to your calendar{when}"),
    "timer_create": ("I set a timer for {minutes} minutes.", "set a timer for {minutes} minutes"),
    "alarm_set": ("I set that alarm.", "set that alarm"),
    "note_create": ("I saved that note.", "save that note"),
    "memory_store": ("I saved that to memory.", "save that to memory"),
    "memory_retire": ("I have forgotten that.", "forget that"),
    "smart_home": ("I switched that for you.", "switch that"),
    "list_show": ("", "read the {list_type} list"),
    "calendar_show": ("", "check your calendar"),
    "reminder_list": ("", "check your reminders"),
    "note_search": ("", "search your notes"),
    "weather": ("", "check the weather"),
}
_DEFAULTS = {"list_type": "shopping"}
_MAX_VALUE_CHARS = 60
_CLAUSE_BREAK = re.compile(r"[.!?;:\n\r\"]+")

REFUSED_LEAD = "I haven't done any of that."
_REFUSED = {
    "fridge_rule": "I can't delete, empty or rename a household list from the panel.",
    "too_many_steps": "That is more than I can do in one go, so ask me for a few at a time.",
}
_NOT_ALLOWED = {"child": "Part of it needs a grown-up.",
                "guest": "Part of it needs a member of the household to ask.",
                "unrecognised_voice": "Part of it needs someone I recognise to ask."}
_UNSAFE_PLAN = "I couldn't work out a safe way to do it."


def _value(v: Any) -> str:
    text = " ".join(str(x) for x in v) if isinstance(v, (list, tuple)) else str(v)
    return " ".join(_CLAUSE_BREAK.sub(" ", text).split())[:_MAX_VALUE_CHARS].strip()


def _when(args: Mapping[str, Any]) -> str:
    date, clock = _value(args.get("date") or ""), _value(args.get("time") or "")
    return (f" on {date}" if date else "") + (f" at {clock}" if clock else "")


def _fill(template: str, step: plan_loop.Step) -> Optional[str]:
    """The template with the step's own arguments, or None when one it needs is missing (the caller then uses the part)."""
    values = {**_DEFAULTS, **{k: _value(v) for k, v in (step.args or {}).items() if v not in (None, "")}}
    values["when"] = _when(step.args or {})
    try:
        return template.format_map(values)
    except (KeyError, IndexError, ValueError):
        return None


def _quoted_part(step: plan_loop.Step) -> str:
    return "“" + (_value(step.part) or step.tool.replace("_", " ")) + "”"


def act(step: plan_loop.Step) -> str:
    """The step as an infinitive ("add floss to the shopping list"); a tool with no phrase is named by the speaker's own words."""
    phrase = PHRASES.get(step.tool)
    return (phrase and _fill(phrase[1], step)) or f"do this part: {_quoted_part(step)}"


def done_sentence(step: plan_loop.Step) -> str:
    phrase = PHRASES.get(step.tool)
    return (phrase and phrase[0] and _fill(phrase[0], step)) or f"Done: {_quoted_part(step)}."


def _sentence(o: plan_loop.StepOutcome) -> str:
    if o.status == "done":                                    # the ONLY branch that says something happened
        if o.code == "read_ok":
            text = " ".join((o.result or "").split())
            return text if text[-1:] in ".!?" else text + "."
        return done_sentence(o.step)
    if o.status == "failed":
        return f"I couldn't {act(o.step)}."
    if o.status == "uncertain":
        if o.code in ("timeout", "error_after_send"):
            return f"I tried to {act(o.step)}, but I can't confirm it went through, so please check before asking again."
        return f"I asked to {act(o.step)}, but I can't confirm it happened."
    if o.code == "dependency":
        return f"I didn't {act(o.step)}, because the step before it didn't work."
    return f"I ran out of time before I could {act(o.step)}."


def _unplanned(parts: Sequence[str]) -> list[str]:
    return [f"I don't have a way to do this yet: “{_value(p)}”." for p in parts if _value(p)]


def _refusal(result: plan_loop.PlanResult, plan: Sequence[plan_loop.Step]) -> str:
    r = result.refusal
    if r.reason == "safety_floor":
        return ""
    if r.reason == "not_allowed":
        why = _NOT_ALLOWED.get(r.role, "Part of it isn't something I can do.")
    elif r.reason == "needs_confirmation":
        step = plan[r.step_index] if 0 <= r.step_index < len(plan) else None
        what = f" to {act(step)}" if step is not None else ""
        why = (f"I need a grown-up to say yes before I try{what}." if r.role == "child"
               else f"I need a yes from you before I try{what}.")
    else:
        why = _REFUSED.get(r.reason, _UNSAFE_PLAN)
    return f"{REFUSED_LEAD} {why}"


def parts(result: plan_loop.PlanResult) -> list[tuple[str, str, str]]:
    """(status, code, sentence) per step, in plan order."""
    return [(o.status, o.code, _sentence(o)) for o in result.outcomes]


def render(result: plan_loop.PlanResult, *, plan: Sequence[plan_loop.Step] = (), unplanned: Sequence[str] = ()) -> str:
    """The whole reply. ``plan`` is needed only to name the step a refusal is about; ``unplanned`` are parts of the ask the
    planner had no tool for. "" when there is nothing to say (flag off / shadow / a safety-floor turn)."""
    if result.status == "refused" and result.refusal is not None:
        return _refusal(result, plan)
    if result.status != "ran":
        return ""
    return " ".join([s for _, _, s in parts(result)] + _unplanned(unplanned))


def speak(result: plan_loop.PlanResult, speaker: plan_loop.Speaker, started: float, *, utterance: str = "",
          plan: Sequence[plan_loop.Step] = (), unplanned: Sequence[str] = (), session_id: str = "") -> str:
    """``render``, then the write-claim guard with the caller's turn start: the reply every lane would speak."""
    text = render(result, plan=plan, unplanned=unplanned)
    if not text:
        return text
    import write_claim_guard

    return write_claim_guard.guard_text(text, speaker.user_id, started, user_text=utterance, lane="plan_loop",
                                        session_id=session_id)
