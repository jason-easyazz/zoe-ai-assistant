"""A reply that is tool-call SYNTAX is never shown or spoken (``ZOE_TOOL_TEXT_GUARD``, default on; ``off`` is the kill switch).

THE MISS (QA round 3b, 2026-11-07, demo user): the 4B brain answered "chuck swim goggles for asha on my to-do list" with the reply
text ``add_to_list(item="chuck swim goggles for asha", list_type="tasks/to-do")`` - no tool call, nothing written, code on the
screen. The Flue sidecar now turns an unambiguous printed call of a disclosed tool into a real one (labs/flue-zoe-brain-2x
``src/text-tool-calls.ts``); this is the net for everything it leaves as text (a tool that was not offered, a positional argument,
two calls, a call followed by prose).

The rule, at the ONE place every brain turn ends (``brain_dispatch``, in front of the write-claim guard, with the same replay
harness exemption): when a reply OPENS with
call syntax - ``name(`` right at the start, optionally behind a backtick or a code fence - every line that is just a call (or
a fence) is dropped, and a reply left with nothing becomes one honest line. A reply that opens with anything else is passed
through untouched, and streaming releases it at its first non-identifier character (about one token of delay), so streaming and
one-shot output are identical. Tool sentinels (``__TOOL__:`` / ``__THINKING__:`` chunks) are never held or changed.

Stdlib only; never raises into a turn.
"""
from __future__ import annotations

import logging
import re
from typing import AsyncIterator

from typed_env import env_str

logger = logging.getLogger(__name__)

ENV_FLAG = "ZOE_TOOL_TEXT_GUARD"
#: what the user hears instead of code - the write did not happen, and it says so (no claim, no promise)
HONEST_LINE = "Sorry, that didn't go through on my end. Could you ask me that again?"

_LEAD_UNDECIDED_RE = re.compile(r"^\s*(?:`{1,3}[A-Za-z]*\s*)?(?:[A-Za-z_][\w.]*)?$")
_LEAD_CALL_RE = re.compile(r"^\s*(?:```[A-Za-z]*\s*|`)?[A-Za-z_][\w.]*\(")
# One whole line that is a call: `name(...)` (a namespace prefix allowed), optionally in backticks, optionally ending with `;`.
_CALL_LINE_RE = re.compile(r"^\s*`*\s*(?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*\(.*\)\s*;?\s*`*\s*$")
_FENCE_LINE_RE = re.compile(r"^\s*```[A-Za-z]*\s*$")


def enabled() -> bool:
    """``ZOE_TOOL_TEXT_GUARD`` - default ON; ``0|false|no|off`` disables. Read per call."""
    return (env_str("ZOE_TOOL_TEXT_GUARD", "") or "").strip().lower() not in {"0", "false", "no", "off"}


def classify_lead(text: str) -> str:
    """``call`` (the reply opens ``name(``), ``undecided`` (it still could) or ``prose``. Pure."""
    if _LEAD_CALL_RE.match(text):
        return "call"
    if _LEAD_UNDECIDED_RE.match(text):
        return "undecided"
    return "prose"


def clean_reply(text: str) -> str:
    """``text`` with its call lines and fences dropped when it OPENS with call syntax; the honest line when nothing is left;
    ``text`` unchanged otherwise. Pure."""
    if classify_lead(text) != "call":
        return text
    kept = [ln for ln in text.split("\n") if not (_CALL_LINE_RE.match(ln) or _FENCE_LINE_RE.match(ln))]
    rest = "\n".join(kept).strip()
    if rest and classify_lead(rest) == "call":   # an unclosed or multi-line call: still code, never shown
        rest = ""
    return rest or HONEST_LINE


def _log(text: str, out: str) -> None:
    name = re.match(r"^\s*`*\s*(?:```[A-Za-z]*\s*)?(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)", text)
    logger.warning("TOOL_TEXT_BLOCKED tool=%s replaced=%s", name.group(1) if name else "-", out == HONEST_LINE)


def guard_text(reply: str) -> str:
    """The one-shot form. Never raises."""
    try:
        if not reply or not enabled():
            return reply
        out = clean_reply(reply)
        if out != reply:
            _log(reply, out)
        return out
    except Exception:  # noqa: BLE001
        return reply


async def guard_stream(inner: AsyncIterator[str]) -> AsyncIterator[str]:
    """The streaming form: hold the reply's lead only while it could still be a call (see the module doc)."""
    if not enabled():
        async for delta in inner:
            yield delta
        return
    mode = "lead"
    held: list[str] = []
    try:
        async for delta in inner:
            if mode == "pass" or delta.startswith(("__TOOL__:", "__THINKING__:")):
                yield delta
                continue
            held.append(delta)
            if mode == "lead":
                verdict = classify_lead("".join(held))
                if verdict == "prose":
                    mode = "pass"
                    for d in held:      # the deltas exactly as they came
                        yield d
                    held.clear()
                elif verdict == "call":
                    mode = "call"
        if held and mode != "call":
            for d in held:
                yield d
        elif held:
            text = "".join(held)
            out = clean_reply(text)
            if out != text:
                _log(text, out)
            yield out
    except Exception:
        # The brain failed MID-stream: what the guard still holds is text the person was owed before the error (the failover /
        # error handling above decides what happens next, not this filter). A lead not (yet) proven to be a call is released as
        # it came; a proven call is cleaned like any end of stream. Then the error goes on, unchanged.
        if held:
            if mode == "call":
                yield clean_reply("".join(held))
            else:
                for d in held:
                    yield d
            held.clear()
        raise
    finally:
        aclose = getattr(inner, "aclose", None)
        if aclose is not None:
            await aclose()
