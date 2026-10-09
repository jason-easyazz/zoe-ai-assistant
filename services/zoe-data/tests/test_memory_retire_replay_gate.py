"""memory_retire step 1 is REPLAY-GATED, not a bare read (Samantha bar S10, quote-backed retirement).

The brain's `memory_retire` tool has two steps: step 1 (no argument) shows the saved notes a sentence might end, step 2 (pick=N) retires
one. Step 2 is a write and goes through ``runWrite`` like every other write. Step 1 changes nothing, but it stages per-turn state in
zoe-data, so a REPLAY turn (the voice-regression gate feeding real recordings through the live pipeline) must not run it.

The way to make that so is NOT to list ``memory_retire`` in ``test_replay_write_isolation.py::_BARE_DISPATCH_READS`` (that allowlist is
the owner's call) and not to edit that file at all: step 1 goes through ``runWrite``'s chokepoint in its read mode (``isRead``), which
keeps the replay gate and drops only the write-only gates. This file pins that shape from the source, red-when-removed:

* a bare ``dispatchIntent('memory_retire', ...)`` anywhere outside ``runWrite`` -> red (the security test goes red too, until someone
  classifies it - these tests say WHY it must not be classified, and what to do instead);
* the replay gate in ``runWrite`` deleted, or made conditional on the read flag -> red;
* step 1 not marked as the read call, or step 2 marked as one (which would skip ZOE_BRAIN_ALLOW_WRITES and the untrusted tier) -> red.

The behavioural half (a replay turn dispatches NOTHING for step 1, a live turn dispatches it) is in the sidecar lane:
labs/flue-zoe-brain-2x/test/replay_isolation.test.ts (the ``memory_retire`` no-pick case).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_TOOLS_TS = Path(__file__).resolve().parents[3] / "labs" / "flue-zoe-brain-2x" / "src" / "tools" / "zoe-tools.ts"


def _span(src: str, header: str) -> tuple[int, int]:
    start = src.index(header)
    i = src.index("{", src.index("): Promise<string>", start))      # the body, not a `{` inside the parameter list
    depth, j = 0, i
    while True:
        depth += {"{": 1, "}": -1}.get(src[j], 0)
        if depth == 0:
            return start, j
        j += 1


def _src() -> str:
    return _TOOLS_TS.read_text()


def _runwrite_calls(src: str) -> "list[list[str]]":
    out = []
    for m in re.finditer(r"\brunWrite\(\s*'memory_retire'", src):
        i, depth = m.end(), 1
        i = src.index("(", m.start()) + 1
        depth = 1
        while depth:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        body = src[src.index("(", m.start()) + 1: i - 1]
        parts, d, buf = [], 0, []
        for c in body:
            if c in "([{":
                d += 1
            elif c in ")]}":
                d -= 1
            if c == "," and d == 0:
                parts.append("".join(buf).strip())
                buf = []
            else:
                buf.append(c)
        parts.append("".join(buf).strip())
        out.append(parts)
    return out


def test_memory_retire_is_never_a_bare_dispatch():
    src = _src()
    lo, hi = _span(src, "async function runWrite(")
    bare = [m.start() for m in re.finditer(r"dispatchIntent\(\s*['\"]memory_retire['\"]", src) if not lo <= m.start() < hi]
    assert not bare, (
        "memory_retire is dispatched by a bare dispatchIntent: it would need listing in the replay-isolation test's bare-read "
        "allowlist (the owner's decision). Route it through runWrite (read mode) so a replay turn never dispatches it."
    )


def test_both_steps_go_through_runwrite_and_only_step_one_is_the_read():
    calls = _runwrite_calls(_src())
    assert len(calls) == 2, f"expected the two memory_retire steps through runWrite, got {calls}"
    step1 = [c for c in calls if c[1] == "{}"]
    step2 = [c for c in calls if c[1] != "{}"]
    assert len(step1) == 1 and len(step2) == 1, calls
    assert len(step1[0]) == 7 and step1[0][6] == "true", f"step 1 must pass the read flag (runWrite's 7th arg): {step1[0]}"
    assert len(step2[0]) == 6, f"step 2 is the WRITE: it must not carry the read flag (that would skip ZOE_BRAIN_ALLOW_WRITES and the untrusted tier): {step2[0]}"


def test_runwrite_keeps_the_replay_gate_ahead_of_the_dispatch_and_unconditional_on_the_read_flag():
    src = _src()
    lo, hi = _span(src, "async function runWrite(")
    body = src[lo:hi]
    gate = "if (isReplayTurn(signal) && actingUserId(signal)) return successFallback;"
    assert gate in body, "runWrite lost its replay gate - every write AND memory_retire step 1 would dispatch on a replay turn"
    assert body.index(gate) < body.index("dispatchIntent(intent"), "the replay gate must come BEFORE the dispatch"
    # the read flag may relax only the two gates that exist to stop a WRITE; it must never appear on the replay gate's line
    uses = [ln.strip() for ln in body.splitlines() if "isRead" in ln and not ln.strip().startswith("//")]
    assert uses == ["isRead = false,", "if (!isRead && !allowWrites()) {", "if (!isRead && isTurnUntrusted(signal)) return untrustedWriteRefusal(dryRunItem);"], uses
