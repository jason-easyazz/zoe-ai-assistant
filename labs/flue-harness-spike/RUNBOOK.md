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
- **The voice brain endpoint (`LLM_BASE_URL` = the local `:11434`)** is read only
  for the connectivity sanity check / local-provider registration. The **harness
  agents do not run on it** — it stays the live voice brain, untouched.

## 1. Install

```sh
cd labs/flue-harness-spike
npm install        # pulls @flue/* — first install also typechecks the API shape
```

> If `npm install` or `npm run typecheck` surfaces a mismatch between this
> scaffold and the real Flue API (e.g. `registerProvider` has a different
> signature), that is expected spike work — fix the `// FLUE-API:` annotated
> call sites in `src/` and note it in `FINDINGS.md`.

## 2. Configure

```sh
cp .env.example .env
# then edit .env:
#   # --- voice brain (LEAVE on the live local llama.cpp; harness does NOT run here) ---
#   LLM_BASE_URL          = http://127.0.0.1:11434/v1   (the live voice brain)
#   LLM_MODEL             = local                        (llama.cpp ignores name; any string)
#   # --- harness model (SEPARATE; default a cloud/dev endpoint) ---
#   HARNESS_LLM_PROVIDER  = openai                       (provider style; openai-compatible)
#   HARNESS_LLM_BASE_URL  = https://<your-cloud-or-dev-endpoint>/v1
#   HARNESS_LLM_MODEL     = <model the harness agents run on>
#   HARNESS_LLM_API_KEY   = <key for the harness endpoint>
#   # --- GitHub + target ---
#   GITHUB_TOKEN          = <your token, or rely on gh auth>
#   GITHUB_REPO           = jason-easyazz/zoe-ai-assistant
#   TARGET_ISSUE          = 715
#   ZOE_CHECKOUT          = /abs/path/to/your/zoe-ai-assistant/checkout
```

To make the harness **fully local later**, point `HARNESS_LLM_BASE_URL` /
`HARNESS_LLM_MODEL` at a local endpoint — **no `src/` change**, only env.

Never commit your real `.env`. Only `.env.example` is tracked.

## 3. Bounded connectivity sanity check (optional, fast)

Before the full loop, confirm the **harness** model target is reachable:

```sh
npm run check:llm     # single small non-streaming POST to HARNESS_LLM_BASE_URL
```

Expect a short JSON completion back. If it hangs or errors, fix
`HARNESS_LLM_BASE_URL` (and `HARNESS_LLM_API_KEY`) before running the loop.

## 4. Run the spike

```sh
npm run spike
```

This runs the durable workflow: **`scout → implement → verify → openPR`** against
`TARGET_ISSUE` in `GITHUB_REPO`. Progress is checkpointed per phase, so a failure
stops *at the phase that failed* (visible in the logs) rather than silently.

On success it prints the URL of the opened PR.

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
