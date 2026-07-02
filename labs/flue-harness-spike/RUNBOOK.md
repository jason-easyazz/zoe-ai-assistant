# RUNBOOK — Flue Harness Spike

> There is **no dev box.** Run this **on the Jetson itself**, but **isolated from
> the live voice path**: it is a hand-started process, and the harness agents run
> on a **separate `HARNESS_LLM_*` model** (default a cloud/dev endpoint) so they
> never compete for the live GPU. The **voice brain on `:11434` is never
> touched.** See `README.md` for the design.

## 0. Prerequisites

- **Node ≥ 22** (Flue requires modern Node; this spike pins `>=22`).
  ```sh
  node -v   # must print v22.x or newer
  ```
  On a box with nvm: `nvm install 22 && nvm use 22`.
- **git** + a checkout of `jason-easyazz/zoe-ai-assistant` you can branch from.
- **A GitHub token** with `repo` scope on `jason-easyazz/zoe-ai-assistant`
  (so the spike can push a branch and open a PR). The `gh` CLI being logged in
  is the easiest path.
- **A harness LLM endpoint (`HARNESS_LLM_*`), separate from the voice brain.**
  This is what the harness *agents* run on, kept **off** the live `:11434` slot:
  - **Default / recommended for the build:** a **cloud or dev** OpenAI-compatible
    endpoint — set `HARNESS_LLM_BASE_URL` / `HARNESS_LLM_MODEL` /
    `HARNESS_LLM_API_KEY`. This keeps harness load off the live voice GPU.
  - **Later, for a fully-local harness:** point `HARNESS_LLM_BASE_URL` at a local
    endpoint (a second llama.cpp, or the Jetson's own `:11434` once headroom
    allows) — **no code change**, just env.
- **The voice brain endpoint (`LLM_BASE_URL` = the local `:11434`)** is reference
  only now — the harness agents **do not run on it**. It stays the live voice
  brain, untouched.

## 1. Install + build

```sh
cd labs/flue-harness-spike
npm install            # @flue/runtime + @flue/cli (the `flue` binary), valibot, hono
npm run build          # flue build --target node — discovers src/workflows/harness.ts
```

`npm run build` (and `npm run typecheck`) both pass against `@flue/runtime@1.0.0-beta.6`.
Phase 0 already reconciled this scaffold with the real Flue API (see `FINDINGS.md`):
agents/providers/workflows come from `@flue/runtime`, the pipeline is one workflow
whose bound agent delegates to scout/verifier **subagents** (Flue has no `step.run()`).

## 2. Configure

```sh
cp .env.example .env
# then edit .env:
#   # --- harness model (SEPARATE from the voice brain; default OpenRouter) ---
#   HARNESS_LLM_PROVIDER  = openrouter
#   HARNESS_LLM_BASE_URL  = https://openrouter.ai/api/v1
#   HARNESS_LLM_MODEL     = openrouter/anthropic/claude-3.5-haiku   (any openrouter/<id>)
#   OPENROUTER_API_KEY    = <the box's existing OpenRouter key, or export it in the shell>
#   # --- GitHub + target ---
#   GITHUB_TOKEN          = <your token, or rely on gh auth>
#   GITHUB_REPO           = jason-easyazz/zoe-ai-assistant
#   TARGET_ISSUE          = 715
#   ZOE_CHECKOUT          = /abs/path/to/your/zoe-ai-assistant/checkout
```

The provider is registered in `src/app.ts` as `openrouter`; model strings are
`openrouter/<openrouter-model-id>`. To go **fully local later**, point
`HARNESS_LLM_BASE_URL` / `HARNESS_LLM_MODEL` at a local endpoint — **no code change**.

`flue run` auto-loads this `.env` (shell env wins). Never commit your real `.env`;
only `.env.example` is tracked.

## 3. Run the spike

```sh
npm run spike -- --input '{"issue": 715}'
# equivalently:  npx flue run harness --target node --input '{"issue": 715}'
```

This invokes the `harness` workflow: **`scout → implement → verify → openPR`**.
The bound orchestrator agent owns a `local()` sandbox over `ZOE_CHECKOUT`, branches
off `origin/main`, delegates scouting + verdict to the **scout/verifier subagents**,
applies the change, runs `VERIFY_CMD`, and (only on a PASS verdict) opens a PR.
Per-phase `log.info` events make a stall visible at its phase boundary.

On success the run result prints `{ "prUrl": "...", "verdict": "PASS …" }`.

## 5. Record results

Fill in `FINDINGS.md` — did the loop close? Which phase was weakest? Did the
harness model produce a usable diff? Was the PR genuinely reviewable? **Also
record the #735 voice-latency probe reading while the harness ran** (Phase 0
acceptance, PR #736 §5): the harness must show **no voice-latency regression.**
This is the actual deliverable of the spike.

## Safety reminders

- It runs **on the Jetson**, but the **harness agents must stay on the separate
  `HARNESS_LLM_*` model** — never repoint them at the live voice brain on
  `:11434` during the build. The voice path stays untouched.
- The spike opens a PR but **must not merge** it. There is no merge step in the
  code; review the PR by hand.
- The spike branches from `main` in your *local checkout* (`ZOE_CHECKOUT`); it
  does not touch other people's branches or worktrees.
