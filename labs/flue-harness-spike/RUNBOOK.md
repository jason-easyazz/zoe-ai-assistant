# RUNBOOK — Flue Harness Spike

> Run this on a **separate dev box**, not on the production Jetson. See
> `README.md` for why.

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
- **A local OpenAI-compatible LLM endpoint.** Either:
  - run your own llama.cpp on the dev box (recommended — keeps load off the
    Jetson), **or**
  - point `LLM_BASE_URL` at the Jetson's LAN endpoint
    `http://<jetson-ip>:11434/v1` (read-only inference; fine for a light run,
    but do not hammer it).

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
#   LLM_BASE_URL   = http://127.0.0.1:11434/v1   (your local llama.cpp)
#   LLM_MODEL      = local                        (llama.cpp ignores name; any string)
#   GITHUB_TOKEN   = <your token, or rely on gh auth>
#   GITHUB_REPO    = jason-easyazz/zoe-ai-assistant
#   TARGET_ISSUE   = 715
#   ZOE_CHECKOUT   = /abs/path/to/your/zoe-ai-assistant/checkout
```

Never commit your real `.env`. Only `.env.example` is tracked.

## 3. Bounded connectivity sanity check (optional, fast)

Before the full loop, confirm the provider target is reachable:

```sh
npm run check:llm     # single small non-streaming POST to LLM_BASE_URL
```

Expect a short JSON completion back. If it hangs or errors, fix
`LLM_BASE_URL` before running the loop.

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
local Gemma produce a usable diff? Was the PR genuinely reviewable? This is the
actual deliverable of the spike.

## Safety reminders

- Do **not** run this on the production Jetson.
- The spike opens a PR but **must not merge** it. There is no merge step in the
  code; review the PR by hand.
- The spike branches from `main` in your *local checkout* (`ZOE_CHECKOUT`); it
  does not touch other people's branches or worktrees.
