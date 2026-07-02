# labs — lab-only experiments & spikes

## Purpose

Throwaway-grade experiments and de-risking spikes that must run **isolated from
the Zoe runtime**. Nothing here is imported, built, or executed by any production
service, Docker image, or CI job. Lab work proves (or kills) an idea before any
prod migration — per the Samantha guardrail: **lab-prove before prod**.

## Ownership

Owned by whoever is running the spike. Each spike lives in its own subdirectory
with its own README/RUNBOOK and is self-contained.

## Forbidden

- Do **not** wire any lab code into the live voice path, `zoe-data`, a systemd
  unit, a Docker image, or CI. Labs run as hand-started processes only.
- Do **not** let lab **harness/agent** work point at the local voice brain on
  `:11434` (Gemma-4-E4B) for *its own* engineering work — harnesses must use a
  separate harness model so the live GPU slot is never contended. (Exception: a
  spike whose explicit subject **is** the Gemma brain — e.g. `flue-zoe-brain/`,
  porting Zoe's brain onto Flue per `docs/architecture/zoe-flue-integration.md`
  Seam M — points at `:11434` by design; it must stay hand-started and demo-only,
  never wired into a turn.)
- Do **not** promote a spike to prod without passing its stated acceptance bar
  (the "Samantha tests") and showing no voice-latency regression.

## Work Guidance

- Keep each spike in its own `labs/<name>/` subtree; pin dependencies.
- Mark unverified third-party API calls in-source (e.g. `// FLUE-API:`) so a human
  confirms them against the installed package on first run.

## Verification

Repo structure validator must pass (`labs/**/*` is an approved manifest pattern in
`.zoe/manifest.json`). Lab code is not covered by production CI by design.

## Child DOX Index

- `flue-harness-spike/` — Flue autonomous-harness substrate spike (scout → implement
  → verify → openPR slice); README + RUNBOOK + FINDINGS are records, not contracts.
- `flue-zoe-brain/` — Flue-hosted Pi `Agent` on the local Gemma brain (Phase 2 of
  `docs/architecture/zoe-flue-integration.md`: a third implementation behind the
  `run_zoe_core` seam). Text-reply persona only; tools/memory are a later phase.
  Hand-started, demo-only, never in a turn.
- `flue-zoe-telegram/` — Flue Telegram channel: long-poll bot bridged to zoe-data's
  `/api/chat` (NOT a Flue LLM agent; `src/agents/zoe.ts` is a build-only placeholder
  and registers no model provider — never points at the voice brain on `:11434`).
  Hand-started, demo-only; README is a record, not a contract.
