# FINDINGS — Flue Harness Spike

> Fill this in after running the spike **on the Jetson, isolated from the live
> path** (no dev box). This file is the actual output of the spike: it's how we
> decide whether Flue becomes Zoe's harness substrate.

## Environment

- Box / OS: the live Jetson Orin NX (this box), in an isolated git worktree off `main`.
- Node version: **v22.22.0** (≥ required `22.19.0`).
- Flue versions installed: **`@flue/runtime@1.0.0-beta.6`, `@flue/sdk@1.0.0-beta.6`** (install PASS).
- Harness model endpoint used: **none yet** — no live run (needs operator endpoint/key).
- Voice brain left untouched on `:11434`? **Yes** — Phase 0 so far was install + typecheck only; no agent touched the GPU.

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

## Flue API reality check — Phase 0 run, 2026-06-26 (on the Jetson Orin NX)

**Environment:** Node v22.22.0 (≥ the beta's required `>=22.19.0`); npm 10.9.4.

**`npm install`: PASS.** `@flue/runtime@1.0.0-beta.6` + `@flue/sdk@1.0.0-beta.6`
resolve and install cleanly (283 packages). This is the first validation of the
PR-#737 version fix: the originally-scaffolded `@flue/runtime@0.4.0` is a **404
on npm** (only the `1.0.0-beta.x` line is published), so the original pins would
have made `npm install` fail outright. Notably the beta depends on
`@earendil-works/pi-agent-core@^0.79.10` / `@earendil-works/pi-ai@^0.79.10` —
**the same Pi ~0.79 lineage as Zoe's brain**, exactly as ADR §3.4 intended.

**`npm run typecheck`: FAIL — the scaffold was written against an imagined API
that diverges materially from the real beta.** The `// FLUE-API:` markers flagged
precisely the right call sites. Concrete deltas:

| Scaffold assumed | Real beta `1.0.0-beta.6` | Fix |
|---|---|---|
| `import { registerProvider } from '@flue/sdk'` (provider.ts:25) | `@flue/sdk` is the **client** SDK (`createFlueClient`, run/invoke types only). `registerProvider` is in **`@flue/runtime`**. | change import package |
| `import { defineAgent } from '@flue/sdk'` (agents.ts:18) | `defineAgent` is in **`@flue/runtime`** | change import package |
| `registerProvider({ ...single options object })` | `registerProvider(providerId: string, registration: ProviderRegistration)` — **two positional args** | rewrite call |
| `defineAgent({ ...static config object })` | `defineAgent(initialize: (ctx: AgentInitializerContext) => AgentRuntimeConfig)` — **a function** returning runtime config | rewrite |
| `defineWorkflow({ name, handler({ step }) })` with `step.run('phase', fn)` | `defineWorkflow({ agent: AgentDefinition, input?, output?, run(ctx) })` where `ctx = { harness, log, input }` — **no `step` / no per-step durable helper** | redesign (see below) |
| `agent.run({...})` for invocation (workflow.ts) | no `.run()` on agents; use top-level `dispatch(agent, req)` / the workflow's bound agent | rewrite |
| `workflow.run({})` to execute (index.ts:37) | `invoke(workflow, request)` → receipt, and it needs a running Flue app/runtime (`createDefaultFlueApp()` → Hono, `NodeRuntime`) | rewrite + stand up a runtime |

### Headline finding (the real spike result)

**Flue's durability unit is the _workflow_ = one bound agent + one action; it does
NOT expose a `case`-style per-step `step.run()` checkpoint API inside a handler.**
The scaffold's central control-flow assumption (four durable `step.run()` phases
— scout → implement → verify → openPR — inside one workflow handler) is therefore
**invalid against the real API.** In Flue, a multi-phase pipeline is expressed
either as the bound agent delegating to **first-class subagents** (Flue ships
subagents + `dispatch`/`invoke`), or as **chained workflows/actions**, with the
agent's own tool-use loop doing the within-phase work — not inline `step.run`.

This is a *good* outcome for the spike, not a blocker: Flue installs clean, shares
the brain's Pi 0.79 runtime, and has every primitive we need (agents, subagents,
providers/per-agent models, durable workflows, MCP, `local()` sandbox). The
**case pattern still maps — phases become subagents/chained workflows, not steps.**
The scaffold needs a control-flow redesign before it runs; that redesign is the
next increment (and the `scout/implement/verify` agents + GitHub/exec helpers are
otherwise reusable).

### Not yet done (needs operator input — deliberately not run autonomously)

- **No live agent run.** Running the loop needs a `HARNESS_LLM_*` endpoint + key
  (a cloud/dev OpenAI-compatible model, kept off the `:11434` voice GPU). That
  credential/endpoint choice is the operator's. `npm run check:llm` / `npm run
  spike` were **not** executed.
- **No PR opened** by the harness (the spike's success artifact) — gated on the
  above and on explicit go-ahead, since it pushes a branch + opens a real PR.
- **#735 voice-latency probe not yet read** under harness load (no harness ran).

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

- [x] **Promising — proceed, but the scaffold needs a control-flow redesign first.**
  Flue installs clean on the Jetson, shares the brain's Pi 0.79 runtime, and has
  every primitive the harness needs. The only blocker found is that the spike's
  `case`-style `step.run()` phase model isn't how Flue does durability — phases map
  onto **subagents / chained workflows**, not inline steps.

**Notes / next steps:**
1. **Redesign the pipeline** to Flue's real model: a top-level workflow whose bound
   agent delegates scout → implement → verify → openPR to **subagents** (or chain
   workflows/actions), using `defineAgent(initialize → AgentRuntimeConfig)`,
   `registerProvider(id, registration)` from `@flue/runtime`, and
   `invoke(workflow, req)` against a `NodeRuntime` / `createDefaultFlueApp()`.
   The existing scout/implement/verify prompts + GitHub/exec helpers are reusable.
2. **Operator inputs needed to actually run it:** a `HARNESS_LLM_*` cloud/dev
   endpoint + key (off the `:11434` voice GPU), and go-ahead for the harness to
   open a real PR.
3. Then read the **#735 latency probe** under harness load to confirm no voice
   regression (Phase 0 acceptance, PR #736 §5).
