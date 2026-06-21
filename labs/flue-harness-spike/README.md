# Flue Harness Spike (lab-only)

> **STATUS: LAB SPIKE. NOT WIRED INTO PRODUCTION.**
> Everything here lives under `labs/flue-harness-spike/` and is intentionally
> isolated from the Zoe runtime. Nothing in this directory is imported, built,
> or executed by any production service, Docker image, or CI job.

## What this spike proves

We want to know whether **[Flue](https://github.com/withastro/flue)** (Apache-2.0,
built on `@earendil-works/pi-agent-core` — the same Pi lineage as Zoe's brain
`@earendil-works/pi-coding-agent`) is a viable **substrate** for Zoe's autonomous
PR-processing harness.

The decided architecture (do not re-derive):

- Reproduce the **pattern** of `workos/case`
  (`scout → implementer → verifier → reviewer → closer → retrospective`).
  We reproduce the *pattern from scratch* — we do **not** copy `case`'s code
  (it is unlicensed / all-rights-reserved).
- This spike implements only a **minimal slice** of that pattern:
  **`scout → implement → verify → openPR`** — enough to prove the loop closes
  end-to-end on one real Zoe GitHub issue, fully locally, with no cloud LLM.
- The LLM is the **local llama.cpp** OpenAI-compatible server already running on
  the Jetson (Gemma-4-E4B on `:11434`), wired via Flue's provider registration
  pointed at `http://127.0.0.1:11434/v1`.

If this slice closes the loop and produces a reviewable PR with evidence, we have
de-risked Flue as the harness substrate. See [`RUNBOOK.md`](./RUNBOOK.md) for the
exact run steps and [acceptance criteria](#acceptance-criteria-the-samantha-tests).

## Why lab-only / why you must NOT run a full loop on the Jetson

The Jetson Orin NX is the **live production voice box**: Gemma on llama.cpp
(`:11434`), Kokoro TTS (`:10201`), and `zoe-data` (`:8000`) are all serving real
traffic on it. A full Flue agent loop (27-package monorepo install + a durable
workflow hammering the GPU) would contend with the live model for VRAM and CPU.

**Therefore: run this on a separate dev box, not on the Jetson.** The Jetson's
`:11434` endpoint *can* be reached from the dev box over LAN for the bounded
connectivity test, but the agent loop itself should run elsewhere (ideally the
dev box runs its own local llama.cpp, or points at a non-production endpoint).

## What's in here

| File | Purpose |
|------|---------|
| `README.md` | This file — what the spike proves + acceptance bar |
| `RUNBOOK.md` | Exact dev-box steps: prereqs, install, env, run |
| `package.json` | Pinned deps (`@flue/*`), Node ≥22, run scripts |
| `.env.example` | Knobs: LLM URL/model, GitHub token, repo, target issue |
| `src/provider.ts` | Registers the local llama.cpp OpenAI-compatible provider |
| `src/agents.ts` | Subagent definitions: scout / implementer / verifier |
| `src/workflow.ts` | Durable workflow: `scout → implement → verify → openPR` |
| `src/github.ts` | Thin GitHub helpers (branch + PR open) |
| `src/index.ts` | Entry point — wires it all together |
| `FINDINGS.md` | Stub for the human to fill after the dev-box run |

## Acceptance criteria (the "Samantha tests")

The spike **passes** if, on a dev box with no cloud access, a single run:

1. **Scouts** one real Zoe GitHub issue (default: `#715`) — reads the issue and
   relevant repo files, produces a written plan.
2. **Implements** a minimal change on a fresh branch (the change can be small /
   scoped — the bar is "the loop produced a real diff", not "ship-ready code").
3. **Verifies** the change (runs the repo's lint/test or a scoped check) and
   captures the output as evidence.
4. **Opens a reviewable PR** against `jason-easyazz/zoe-ai-assistant` containing
   the diff **and** the verify evidence in the PR body.
5. Does all of the above **fully locally** — the only LLM calls go to the
   `http://127.0.0.1:11434/v1` (or LAN-equivalent) llama.cpp endpoint. **No cloud
   LLM, no Anthropic/OpenAI API key required.**

If the loop stalls, the run must fail *loudly* at the phase boundary (durable
workflow checkpoint) so we can see *which* phase Flue couldn't carry.

## Important caveats for the reviewer

- The exact Flue API surface (`registerProvider` signature, `defineAgent` /
  durable-workflow helpers) is pinned to the package versions in `package.json`
  but was **not** runtime-verified on the Jetson (we deliberately did not
  `npm install` the monorepo on the production box). The `src/` code is written
  against the documented/expected shape and is annotated with `// FLUE-API:`
  comments wherever the human must confirm the call against the installed
  package. Treat the first dev-box `npm install` + typecheck as part of the spike.
- This is a **spike**: clarity over completeness. Phases 5–6 of the `case`
  pattern (`reviewer → closer → retrospective`) are intentionally omitted.
