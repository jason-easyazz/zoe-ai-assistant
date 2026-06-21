# FINDINGS — Flue Harness Spike

> Fill this in after running the spike on a **dev box**. This file is the actual
> output of the spike: it's how we decide whether Flue becomes Zoe's harness
> substrate.

## Environment

- Dev box / OS:
- Node version (`node -v`, must be >=22):
- Flue versions actually installed (`npm ls @flue/runtime @flue/sdk`):
- LLM endpoint used (`LLM_BASE_URL`) and model served:

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
- **Conclusion:** the OpenAI-compatible provider target Flue will use
  (`.../v1/chat/completions`) is **reachable and correctly OpenAI-shaped** on the
  Jetson. The live model answered without being cold-loaded heavily (tiny
  request). On a dev box, prefer a *local* llama.cpp to keep load off prod, or
  point `LLM_BASE_URL` at the Jetson's LAN address for light use.

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
| fully local (no cloud LLM) | | |

- **PR opened:** <url>
- **Was the PR genuinely reviewable?**
- **Did local Gemma-4-E4B produce a usable plan/diff, or did quality block the loop?**
- **Where did the loop stall, if anywhere? Did it fail loudly at the right phase boundary?**

## Verdict

- [ ] Flue is a good substrate for Zoe's harness — proceed.
- [ ] Promising but needs X before deciding.
- [ ] Not a fit — reason:

**Notes / next steps:**
