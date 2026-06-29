# Flue brain — tool-call reliability

## recall_memory fix (2026-06-29)

**Problem (from the n=30 benchmark, PR #902):** `recall_memory` fired only ~67% of
the time — the model answered "I don't remember" from its own head WITHOUT calling
the tool (a silent failure, and a "Zoe forgets you" regression risk). time/list
tools were 100%.

**Fix applied (this PR):**
- **Persona instruction** (`src/agents/zoe.ts`): Zoe now knows she has *no* memory
  of the person from her own head — the only way to know their name/facts/
  preferences is to call `recall_memory`. She MUST call it first whenever asked
  what she knows/remembers, and must never say "I don't remember" until the tool
  has answered.
- **Tool description** (`src/tools/zoe-tools.ts`): made imperative — call before
  ever claiming ignorance about the user.
- All **11 tools** reconciled onto a clean branch (hardened semantics kept:
  identity fail-closed, dispatch `ok` checked, validated timeout, honest dry-run).

**Review hardening (PR #915):**
- **No false confirmation** (`src/tools/zoe-tools.ts`): `dispatchIntent` now requires
  an EXPLICIT `ok === true` from `/api/system/intent-dispatch` before reporting
  success. A 200 that doesn't confirm (ok missing, ok:false, non-boolean ok, garbled
  body) returns a non-confirming line instead of a fabricated "done" reply. Covered by
  `test/dispatch_confirm.test.ts` (`node --experimental-strip-types --test test/`).
- **Scorer can't undercount** (`parity/recall_reliability.py`): `recall_fired` now
  recognises a recall_memory firing across event-stream shapes — history as a list or
  an object wrapping an events/messages array; event types tool_start/tool/tool_call/
  function_call (case/separator-insensitive); tool name from toolName/name/tool_name —
  while still ignoring text mentions. Covered by `parity/test_recall_reliability.py`.

**AFTER-fix numeric re-benchmark: DEFERRED (environment, not the fix).** The shared
Jetson was under heavy concurrent GPU load (multiple fleet agents), so each Gemma
call crawled and the ≥30-trial benchmark timed out. The fix is directionally sound
and build-clean (`flue build` + `tsc` pass).

**GATE — do NOT flip the cutover until this is re-measured.** Re-run
`parity/recall_reliability.py` in a quiet GPU window and confirm `recall_memory`
≥ ~90% before `ZOE_BRAIN_BACKEND=flue` in production. The voice-corpus parity gate
(operator-authorized, drives live STT) also still stands.
