# FINDINGS — Flue Harness Spike

> Fill this in after running the spike **on the Jetson, isolated from the live
> path** (no dev box). This file is the actual output of the spike: it's how we
> decide whether Flue becomes Zoe's harness substrate.

## Environment

- Box / OS (expected: the live Jetson Orin NX):
- Node version (`node -v`, must be >=22):
- Flue versions actually installed (`npm ls @flue/runtime @flue/sdk`):
- Harness model endpoint used (`HARNESS_LLM_BASE_URL`) and model:
- Voice brain left untouched on `LLM_BASE_URL` (`:11434`)? (expected: yes):

## Bounded connectivity check (recorded by the agent that built this scaffold)

- **Date:** 2026-06-21
- **From:** the production Jetson Orin NX (this box), isolated temp dir, single
  curl — NOT an npm install, NOT an agent loop.
- **Target:** `http://127.0.0.1:11434/v1/chat/completions` (live llama.cpp).
- **Request:** one non-streaming POST, `max_tokens: 3`, `temperature: 0`,
  prompt "reply with the single word: ok".
- **Result:** **HTTP 200**. Response body returned
  `choices[0].message.content == "ok"`; `model` reported as
  `/home/zoe/models/gemma4-e4b-qat/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf`;
  usage `total_tokens: 18`; server build `b9733`. Prompt eval ~455ms, 2 tokens
  predicted ~93ms.
- **Conclusion:** the OpenAI-compatible shape (`.../v1/chat/completions`) Flue's
  provider abstraction expects is **reachable and correctly OpenAI-shaped** on the
  Jetson. The live voice brain answered without being cold-loaded heavily (tiny
  request). Note this confirms the *voice brain* endpoint only — for the harness
  run the **agents are bound to the separate `HARNESS_LLM_*` model** (default
  cloud/dev), leaving `:11434` for live voice; run `npm run check:llm` to verify
  that harness endpoint before the loop.

## Flue API reality check (fill after `npm install` + `npm run typecheck`)

For each `// FLUE-API:`-annotated call site, note whether the scaffold matched
the installed package, and what you had to change:

- `registerProvider` (src/provider.ts) — matched? / changes:
- `defineAgent` (src/agents.ts) — matched? / changes:
- agent invocation `.run(...)` (src/workflow.ts) — matched? / changes:
- `defineWorkflow` + `step.run(...)` (src/workflow.ts) — matched? / changes:
- workflow execution entry point (src/index.ts) — matched? / changes:

## The Samantha tests — did the loop close?

| Phase | Outcome (pass/fail) | Notes |
|-------|---------------------|-------|
| scout (read issue, produce plan) | | |
| implement (real diff on a branch) | | |
| verify (ran check, captured evidence) | | |
| openPR (reviewable PR with evidence) | | |
| harness on separate model (not the voice brain) | | |

- [ ] **Measure voice latency with the #735 probe while the harness runs** —
  Phase 0 acceptance (PR #736 §5) is **no voice-latency regression** on the
  Jetson with the harness on its dev model. Record the probe reading here:
  - baseline (harness stopped): \_\_\_ ms
  - with harness running: \_\_\_ ms
  - regression? (must be no):

- **PR opened:** <url>
- **Was the PR genuinely reviewable?**
- **Did the harness model produce a usable plan/diff, or did quality block the loop?**
- **Where did the loop stall, if anywhere? Did it fail loudly at the right phase boundary?**

## Verdict

- [ ] Flue is a good substrate for Zoe's harness — proceed.
- [ ] Promising but needs X before deciding.
- [ ] Not a fit — reason:

**Notes / next steps:**
