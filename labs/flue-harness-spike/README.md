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
  end-to-end on one real Zoe GitHub issue.
- **Two providers, two models (the key design point).** Flue sets the model
  **per agent** via `registerProvider`. So the spike registers **two** providers
  and uses them for different things:
  1. the **local llama.cpp** OpenAI-compatible server already running on the
     Jetson (Gemma-4-E4B on `:11434`) — this is the **voice brain, left
     untouched**; and
  2. a **configurable harness model** (`HARNESS_LLM_*`, default a **cloud/dev
     model**) that the **harness agents** run on.
  The harness agents are bound to the harness model so they **never compete with
  the live voice brain for the GPU slot on `:11434`.** Later, point
  `HARNESS_LLM_*` at a local endpoint and the harness goes fully local with **no
  code change** — see [Model knob](#the-per-agent-model-knob).

If this slice closes the loop and produces a reviewable PR with evidence, we have
de-risked Flue as the harness substrate. See [`RUNBOOK.md`](./RUNBOOK.md) for the
exact run steps and [acceptance criteria](#acceptance-criteria-the-samantha-tests).

## Where this runs: on the Jetson itself, isolated from the live path

There is **no separate dev box.** This spike runs **on the live Jetson Orin NX**
— the same box serving Gemma on llama.cpp (`:11434`), Kokoro TTS (`:10201`),
`zoe-data` (`:8000`), and Moonshine STT. That is safe because the spike is kept
**isolated from the live voice path**:

- It runs as its **own process, started by hand**, not in any voice request path.
- The **harness agents run on a separate model** (`HARNESS_LLM_*`, default a
  cloud/dev endpoint), **not** the voice brain on `:11434`, so the live GPU slot
  is never contended by the harness.
- The **voice brain on `:11434` is left completely untouched** by this spike.

### The per-agent model knob

Flue binds a model **per agent**, so the *same* harness code runs on *any* model
just by changing env:

- **Now (default):** `HARNESS_LLM_*` points at a **cloud/dev** OpenAI-compatible
  endpoint. The harness runs there; voice stays local on `:11434`.
- **Later (one-line swap to fully local):** point `HARNESS_LLM_BASE_URL` /
  `HARNESS_LLM_MODEL` at a **local** endpoint (e.g. a second llama.cpp, or the
  Jetson's own `:11434` once headroom allows). **No `src/` change** — only env.
- **Per-phase tuning:** because the model is per-agent, you can later give
  scout/triage a cheap/fast model and implementer/reviewer a stronger one
  (`src/agents.ts` is where each agent's model is selected).

## What's in here

| File | Purpose |
|------|---------|
| `README.md` | This file — what the spike proves + acceptance bar |
| `RUNBOOK.md` | Exact on-Jetson steps: prereqs, install, env, run |
| `package.json` | Pinned deps (`@flue/*`), Node ≥22, run scripts |
| `.env.example` | Knobs: voice-brain URL + **separate `HARNESS_LLM_*` model**, GitHub token, repo, target issue |
| `src/provider.ts` | Registers **two** providers — the local llama.cpp voice brain **and** the configurable harness model |
| `src/agents.ts` | Subagent definitions (scout / implementer / verifier), each bound to the harness model |
| `src/workflow.ts` | Durable workflow: `scout → implement → verify → openPR` |
| `src/github.ts` | Thin GitHub helpers (branch + PR open) |
| `src/index.ts` | Entry point — wires it all together |
| `FINDINGS.md` | Stub for the human to fill after the on-Jetson run |

## Acceptance criteria (the "Samantha tests")

The spike **passes** if, running **on the Jetson but isolated from the live path**
(harness on the `HARNESS_LLM_*` model, voice brain untouched on `:11434`), a
single run:

1. **Scouts** one real Zoe GitHub issue (default: `#715`) — reads the issue and
   relevant repo files, produces a written plan.
2. **Implements** a minimal change on a fresh branch (the change can be small /
   scoped — the bar is "the loop produced a real diff", not "ship-ready code").
3. **Verifies** the change (runs the repo's lint/test or a scoped check) and
   captures the output as evidence.
4. **Opens a reviewable PR** against `jason-easyazz/zoe-ai-assistant` containing
   the diff **and** the verify evidence in the PR body.
5. Does all of the above **without disturbing the live voice path** — the harness
   talks to its **own `HARNESS_LLM_*` endpoint**, the **voice brain on `:11434`
   is never touched**, and (Phase 0 acceptance, per PR #736 §5) running it shows
   **no voice-latency regression** measured by the **#735 latency probe**.

If the loop stalls, the run must fail *loudly* at the phase boundary (durable
workflow checkpoint) so we can see *which* phase Flue couldn't carry.

## Important caveats for the reviewer

- The exact Flue API surface (`registerProvider` signature, `defineAgent` /
  durable-workflow helpers) is pinned to the package versions in `package.json`
  but is **not yet runtime-verified** — the scaffold was authored without an
  `npm install`. The `src/` code is written against the documented/expected shape
  and is annotated with `// FLUE-API:` comments wherever the human must confirm
  the call against the installed package. Treat the **first on-Jetson
  `npm install` + typecheck** as part of the spike.
- This is a **spike**: clarity over completeness. Phases 5–6 of the `case`
  pattern (`reviewer → closer → retrospective`) are intentionally omitted.
