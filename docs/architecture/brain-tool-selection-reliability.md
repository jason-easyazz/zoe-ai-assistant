# Brain tool-selection reliability — plan for a quiet window

**Status:** investigation done, fix NOT attempted. Blocked on a reproducer and on
box headroom. Written 2026-07-27.

**Goal:** get the brain's tool-selection decision as close to 100% as possible,
or establish that it cannot be done and stop paying for the attempt.

---

## 1. Why this exists (and why it is not urgent)

`services/zoe-data/tests/test_zoe_core_client.py::test_tool_action_dispatches`
fails intermittently: the brain is asked *"Add bread to my shopping list."* and
sometimes does not call the `lists` tool. Measured ~14% (#1478) and 2-of-7 in a
later sample.

**It is not a user-visible bug.** With `ZOE_ROUTER_HEAD=active` — verified live
2026-07-27, FunctionGemma-270M serving on `:11436`, `"mode": "active"` in
`~/.zoe-logs` with same-day traffic — the two-stage router decides this utterance
at tier 1.5 and it never reaches the brain. The router returns the list intent at
~0.9996 confidence in ~300ms warm.

So the cost is not broken behaviour; it is **wasted engineering time**. The
failure's signature (green alone, red in the full suite, green on re-run) is
indistinguishable from a test-isolation leak, and has repeatedly sent people
bisecting. That is the thing worth fixing.

Do not raise the priority of this work on the belief that users are affected.
They are not, on this path. It matters for any *future* path that reaches the
brain's tool lane without the router in front.

## 2. What is established

- **Tool selection is sampled, not decided.** The brain's llama-server runs
  `--temp 0.7 --top-k 64 --top-p 0.95`, and **nothing sets a per-request
  temperature** — grepped `services/zoe-core/extensions/provider-local-gemma.ts`
  and `services/zoe-data/zoe_core_client.py`. Every tool choice is drawn from a
  stochastic distribution. Sampling a classifier is wrong on its face.
- **The failure has been captured once:** the brain dispatched `memory_store`
  instead of `list_add` — reading the sentence as a fact about the user rather
  than a list operation.
- **Config that may matter:** `--parallel 2` on the brain, `--ctx-size 16384`,
  `--cache-ram 2048`; `ZOE_CORE_MAX_WORKERS` defaults to 4. A full-file test run
  creates more sessions than the server has slots.

## 3. What is DISPROVED — do not retry these

| Hypothesis | Verdict | Evidence |
|---|---|---|
| Prompt ambiguity, fixable by rewording | **Wrong** | Rewording to the ability's own advertised example (`"Add milk to the shopping list."`) measured **worse**: 2 failures in 7 file-level runs (~29%) vs ~14%, and failed a *different* way — no dispatch at all. Reverted. |
| Simple lists-vs-memory tool ambiguity | **Not reproducible** | A direct llama-server harness offering the two competing tools scored **120/120** correct across temps 0.7/0.3/0.0, and **24/24** with 4 tools under concurrency. The synthetic setup is too easy. |
| Temperature is the proven cause | **Unproven** | Plausible and principled, but the harness above could not reproduce ANY failure, so it could not show temperature mattering either. Believing it now would be unfalsifiable. |

## 4. The actual blocker

**Nothing reproduces the failure on demand.**

- Isolated real-path calls: **20/20 correct**.
- Synthetic llama-server calls: **100% correct** at every temperature and
  concurrency tried.
- Only full-file / full-suite runs produce it, at ~14–29%, taking ~30–100s per
  attempt.

Per-call probability does not explain this: 20 isolated calls should have shown
~3 failures at 14%. Something about full-file context matters — candidate
mechanisms are slot/KV-cache contention on a `--parallel 2` server, worker
churn across sessions, or accumulated context. **None of these is confirmed.**

Until there is an on-demand reproducer, any fix is unfalsifiable. Phase 1 exists
solely to build one; do not skip to Phase 3.

## 5. Preconditions (hard gates)

This work touches the live brain path. Do not start without:

- **≥2GB MemAvailable, quiet.** During this investigation the box reached
  **13MB** available while concurrent requests were being sent to the brain. It
  survived, but that is the documented condition that has crash-looped it
  before (unified memory — cgroup guards do not cover CUDA/NvMap). Check
  headroom BEFORE applying load, not after.
- **Authorization to stop the brain** for the window. The Orin is a dev box; the
  sanctioned pattern is to stop the brain, work, and restore.
- Nothing else driving PRs or tests against the box concurrently.

## 6. Plan

### Phase 1 — Build a reproducer (blocking; do not skip)

Goal: a command that fails on demand in under ~60s, so a fix can be falsified.

1. Bisect the full-file run: does the failure need *specific* preceding tests, or
   just N prior brain calls? Run `test_tool_action_dispatches` preceded by each
   other integration test in turn.
2. If it is contention, reproduce directly: hold `--parallel 2` slots busy with
   long generations, then issue the tool-choice request.
3. If it is worker churn, reproduce by cycling `ZOE_CORE_MAX_WORKERS` sessions
   before the request.

**Exit criterion:** ≥30% failure rate on demand, in isolation from pytest.
If no reproducer emerges after a bounded effort, STOP and go to §8.

### Phase 2 — Test the temperature lever against the reproducer

With the reproducer failing reliably, compare `temperature: 0` vs the 0.7
default on the *real* path (not a synthetic harness). llama.cpp's
OpenAI-compatible API honours per-request temperature, so this needs no server
restart.

**Exit criterion:** temp 0 measurably beats 0.7 on the reproducer, with enough
trials to be more than noise. If it does not, the cause is elsewhere — go to §7.

### Phase 3 — Implement, if and only if Phase 2 confirms

Add a per-request temperature for **tool-selection turns only**, leaving prose
turns at 0.7 (Gemma at temp 0 is flat and repetitive for conversation — do not
lower it globally). Likely insertion point:
`services/zoe-core/extensions/provider-local-gemma.ts`, behind an env flag with
the current behaviour as default.

### Phase 4 — Gate and roll out

- **Voice replay gate is MANDATORY** (`scripts/maintenance/voice_regression_probe.py`
  under `flock /tmp/zoe-voice-harness.lock`) — this is the brain path.
- Flag stays default-off until the replay gate passes and a quiet-window A/B
  shows the improvement holds.

## 7. Alternatives, if temperature is not the cause

- **Grammar-constrained decoding (GBNF)** to force a valid tool name. Heavier,
  but deterministic by construction. There is prior exploration in the
  `lab/router-90-grammar` worktree — read it before starting fresh.
- **Widen router coverage** so fewer utterances reach the brain's tool lane at
  all. Cheapest option, and consistent with the router already outperforming the
  4B brain at tool choice (0.9996 vs a measured ~14% wrong).
- **Accept non-determinism** and keep the test out of gating runs — already done,
  see §8.

## 8. If this is dropped (already shipped, no further work needed)

The practical cost is already neutralised without touching the brain:

- Failures self-capture to `~/.zoe-logs/nondeterministic-test-failures.jsonl`
  with the losing intents, dispatch bodies and request trace, so the next
  occurrence yields evidence instead of a truncated console.
- `pytest services/zoe-data/tests -m "ci_safe and not integration"` excludes the
  six live-model tests — 6 of 5538 — so local verification stops going red for
  reasons unrelated to the change under test.
- The rejected wording experiment is recorded in the test's docstring so it is
  not retried.

Dropping this plan is a legitimate outcome. The router already covers the
user-visible path.

## 9. Resolved: where `memory_store` comes from

Originally filed here as an open question — `abilities/_dispatch.ts` emits no
`memory_store` and `pi list` reports no installed packages, yet a captured
failure dispatched it. **Answered by review (2026-07-27):** `_dispatch.ts` is
the main *zoe-core* caller of `/api/system/intent-dispatch`, not the only caller
in the repo. The Flue brain has its own tool registry:

    labs/flue-zoe-brain/src/tools/zoe-tools.ts:53
      remember_fact → memory_store {text}   (Wave 3, fulfilled via MemoryService.ingest)

So the competing tool is **`remember_fact`** — "store a durable fact about the
user in long-term memory" — which is a genuinely reasonable reading of *"Add
bread to my shopping list."* It is not a spurious dispatch.

**Consequence for Phase 1:** the brain's tool set is NOT the zoe-core abilities
list. Enumerate what the running brain actually offers before building a
reproducer, or the reproducer will pose an easier choice than production does —
which is exactly how the synthetic harnesses in §3 scored 100% while the real
path flaked. (`ZOE_BRAIN_BACKEND=flue` is the live backend, so Flue's registry
is the one that matters.)
