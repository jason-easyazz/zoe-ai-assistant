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

### Increment 1 — redesign to Flue's real model: DONE, build-clean (2026-06-27)

The scaffold was rebuilt to the canonical Flue shape (verified against the bundled
`flue docs` + `@flue/runtime@1.0.0-beta.6` types). **`npm run build` (`flue build
--target node`) and `npm run typecheck` (`tsc --noEmit`) both PASS.** Structure:

- `src/app.ts` — `registerProvider('openrouter', { api: 'openai-completions',
  baseUrl, apiKey })` + mounts `flue()`. OpenRouter is the harness model endpoint
  (OpenAI-compatible), separate from the `:11434` voice brain.
- `src/roles.ts` — orchestrator `defineAgent(() => ({ model, instructions,
  subagents: [scout, verifier], sandbox: local(), cwd }))` + scout/verifier
  `defineAgentProfile`s. Phases are **subagents**, delegated via
  `session.task({ agent })` — the correct mapping of the `case` pattern.
- `src/workflows/harness.ts` — `defineWorkflow({ agent, input, output, run })`;
  `run({ harness, input, log })` drives scout → implement → verify → openPR with
  Valibot-validated `{ issue }` input / `{ prUrl, verdict }` output. Run via
  `flue run harness --input '{"issue": 715}'`.
- Removed the old wrong-API files (`provider.ts`, `agents.ts`, `workflow.ts`,
  `index.ts`, `checkLlm.ts`); `config.ts` + `github.ts` helpers were reused.

### LIVE RUN — 2026-06-27, on the Jetson. The loop closed end-to-end on Flue.

Run: `flue run harness --input '{"issue": 715}'`. Harness model =
`openrouter/anthropic/claude-haiku-4.5` (via OpenRouter, the box's Hermes key;
the `:11434` voice brain was never touched). `ZOE_CHECKOUT` = a disposable
worktree (`~/.worktrees/flue-spike-target`), so the live tree was untouched.

- **Scout** correctly diagnosed #715 (zoe-auth OIDC breaks behind the Cloudflare
  tunnel — hardcoded `http://zoe.local` issuer + Host-derived redirect URIs +
  host-locked state cookie) and named the right files. Genuinely good triage.
- **Implement** produced a real, coherent **5-file diff** on its own branch
  (`services/zoe-auth/oidc/{clients,router,startup}.py` + the postgres migration
  SQL + `.env.example`; +75/-22).
- **Verify** ran the command; **verifier subagent** returned **FAIL** — correctly,
  because `VERIFY_CMD` was the placeholder `echo`, which it judged "inconclusive"
  per its conservative instruction. The `verify.ok && /^PASS/` gate then **threw
  before opening a PR**. The safety gate works exactly as intended.

Net: Flue carried scout → implement → verify with per-agent models, subagent
delegation, and a writable `local()` sandbox; the gate behaved correctly. The
**only** reason no PR opened is the trivial placeholder verify. Substrate de-risked.

### Still open

- **openPR phase not yet exercised on a PASS** — re-run with a *real* scoped
  `VERIFY_CMD` (operator picks the issue, since a PASS opens a real PR).
- **#735 voice-latency probe** to be read while the harness runs (no-regression =
  Phase 0 acceptance, PR #736 §5).

## The Samantha tests — did the loop close?

| Phase | Outcome | Notes |
|-------|---------|-------|
| scout (read issue, produce plan) | **PASS** | Correctly diagnosed #715's OIDC/tunnel root cause; named the right files |
| implement (real diff on a branch) | **PASS** | Real 5-file diff (+75/-22) on its own branch in the disposable checkout |
| verify (ran check, captured evidence) | **PASS** | Ran `VERIFY_CMD`, captured output, fed it to the verifier |
| openPR (reviewable PR with evidence) | **GATED** | Correctly NOT opened — verifier FAILed the placeholder verify; needs a real check to reach a PASS |
| harness on separate model (not the voice brain) | **PASS** | Ran on OpenRouter `claude-haiku-4.5`; `:11434` untouched |

- [ ] **Measure voice latency with the #735 probe while the harness runs** —
  Phase 0 acceptance (PR #736 §5) is **no voice-latency regression** on the
  Jetson with the harness on its dev model. Record the probe reading here:
  - baseline (harness stopped): \_\_\_ ms
  - with harness running: \_\_\_ ms
  - regression? (must be no):

- **PR opened:** none — correctly gated (placeholder verify → verifier FAIL).
- **Was the harness model's output usable?** Yes — scout's triage of #715 was
  accurate and the implementer produced a real, scoped 5-file diff.
- **Did it fail loudly at the right boundary?** Yes — it stopped at the verify
  gate with a clear error, not silently.

## Verdict

- [x] **Flue is a viable substrate — proceed.** Installs clean on the Jetson,
  shares the brain's Pi 0.79 runtime, and the redesigned harness **closed the
  scout → implement → verify loop end-to-end on a real issue** with per-agent
  models and subagent delegation. The original `case`-style `step.run()` model was
  the only real blocker, and it's resolved (phases = subagents).

**Notes / next steps:**
1. ~~Redesign the pipeline to Flue's real model.~~ **DONE (Increment 1).**
2. ~~Live run.~~ **DONE 2026-06-27** — loop closed end-to-end; gate correctly
   blocked the PR on a placeholder verify. See the live-run section + table above.
3. **Exercise openPR on a PASS:** re-run with a *real* scoped `VERIFY_CMD` (operator
   picks the issue, since a PASS opens a real PR).
4. Read the **#735 latency probe** under harness load to confirm no voice
   regression (Phase 0 acceptance, PR #736 §5).
5. Future hardening before any non-lab use: real sandbox isolation for the writable
   checkout (vs `local()`), per-phase models (cheap scout / stronger implementer),
   and the durable-resume / retrospective phases of the `case` pattern.
