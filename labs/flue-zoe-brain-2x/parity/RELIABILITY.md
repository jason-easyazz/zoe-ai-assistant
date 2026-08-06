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

---

## All-tools harness — `tool_reliability.py` (LAB-ONLY)

A second, complementary harness measures **all three** brain tools
(`get_time` / `recall_memory` / `shopping_list_add`), not just recall. Flue
sidecar only — no zoe-data `/api/chat` comparison, no audio/voice path, nothing
mutated (`ZOE_BRAIN_ALLOW_WRITES=false` → `shopping_list_add` is a dry-run).

Ground truth is the **tool call itself, not the reply text**: after each POST it
GETs the Flue session event stream (`GET /agents/zoe/<sid>`) and reads the actual
`tool_start` events (each carries `toolName`) under the `prompt` operation. Reply
text can claim a tool ran when it didn't; the event stream can't.

Baseline run (10 prompts × 3 = n=30, sequential, shared GPU):

| Tool                | Correct tool-call | Rate   |
|---------------------|-------------------|--------|
| `get_time`          | 12 / 12           | 100.0% |
| `recall_memory`     | 6 / 9             | 66.7%  |
| `shopping_list_add` | 9 / 9             | 100.0% |
| **Overall**         | **27 / 30**       | **90.0%** |

All three misses were the same silent failure mode (no tool called, hallucinated
"I don't remember"), on `recall_memory` — the regression the recall fix above
targets. Treat n=30 as directional, not an SLA: the binomial CI on 6/9 is wide
(~35–88%). Re-run under the cutover gate above with ≥30 trials **per tool**.

Run: `python3 parity/tool_reliability.py` (sidecar on :3578). Raw summary lands in
`parity/tool_reliability_last.json` (gitignored runtime artifact).

---

## Statistical reliability gate — `reliability_gate.py` (LAB-ONLY)

Single-pass gates hide 4B-model variance: a prompt can pass one round and fail
the next *identically phrased* (the persona leak in `FIX-PACKET-2026-07-07.md`
item 2 was 1-in-30; the Kate-not-Katie recall is model judgment). This gate
applies the same rigor that proved `recall_memory` (≥30 trials) to the
highest-value, historically-flaky assertions: it runs **each** N times
(`--trials`, default 10) and gates on a per-assertion pass RATE, not a single
pass.

Unlike the sidecar harnesses, it drives the **live authenticated
`/api/chat/?stream=false`** on zoe-data :8000 — the surface production uses,
which routes to the flue sidecar because this deployment runs
`ZOE_BRAIN_BACKEND=flue`. It is therefore a whole-pipeline reliability read
(brain + fast-path), matching what the user actually experiences.

Session discipline (mirrors `hard_gate.py`): a **fresh authenticated test user
is provisioned per run** (`provision_parity_test_user.py` → login for the
`X-Session-ID`), so each run starts from an empty memory store; **every trial
uses a fresh nonce'd conversation `session_id`** — sessions are ownership-bound
and a long shared session overflows the brain at 8192 tokens.

Auto-verified, no human judgment in the pass/fail path: recall/identity/research
score by substring on the reply; the write assertion checks Postgres
(`list_items`) ground truth, so a reply that *claims* it wrote but didn't FAILs.

Assertion set + default thresholds:

| Assertion                          | Kind          | Verify        | Threshold |
|------------------------------------|---------------|---------------|-----------|
| recall of a just-stored fact       | recall        | substring     | ≥ 90%     |
| corrected recall (Kate not Katie)  | recall        | substring     | ≥ 90%     |
| identity: name = Zoe (not Gemma)   | deterministic | substring     | 100%      |
| research-trap statement no stall   | deterministic | substring     | 100%      |
| DB-verified shopping-list write    | deterministic | Postgres      | 100%      |

Thresholds override with `--threshold-recall` / `--threshold-deterministic`.
Run: `python3 parity/reliability_gate.py` (full N=10) or `--trials 2` (smoke).
Result lands in `parity/reliability_gate_last.json` (gitignored). Sequential,
one shared GPU — do **not** run the full N=10 sweep while the shared brain is
mid-campaign.
