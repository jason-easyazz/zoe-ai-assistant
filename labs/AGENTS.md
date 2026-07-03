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
  (Deliberate exception: `flue-zoe-brain/` ships an **operator opt-in** user-unit
  *template* — `scripts/setup/systemd/flue-zoe-brain.service`, port 3578 — that is
  never auto-installed or auto-enabled; production reaches the sidecar only
  through zoe-data's default-OFF `ZOE_BRAIN_BACKEND=flue` seam. No other lab may
  ship a unit without amending this contract.)
- Do **not** let lab **harness/agent** work point at the local voice brain on
  `:11434` (Gemma-4-E4B) for *its own* engineering work — harnesses must use a
  separate harness model so the live GPU slot is never contended. (Exception: a
  spike whose explicit subject **is** the Gemma brain — e.g. `flue-zoe-brain/`,
  porting Zoe's brain onto Flue per `docs/architecture/zoe-flue-integration.md`
  Seam M — points at `:11434` by design; it stays demo-grade and is never wired
  into a live turn — the prod seam that can reach it defaults OFF.)
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
- `flue-zoe-brain/` — Flue-hosted Pi `Agent` on the local Gemma brain (a third
  implementation behind the `run_zoe_core` seam, per
  `docs/architecture/zoe-flue-integration.md`). Serves 20 tools (19 capability
  tools against zoe-data + the `activate_abilities` activator; Waves 1–3 of the
  cut-list record `docs/knowledge/flue-cutover-tool-cut-list.md` §3 — the parity
  target) with progressive
  tool disclosure at the wire (always-on core + activated groups per call;
  `src/tools/tool-groups.ts`), identity fail-closed, writes dry-run-gated.
  Emits the Seam-A text-delta + `__TOOL__`/`__THINKING__` sentinel stream
  (byte-pinned to the prod contract) via content-negotiated NDJSON on the
  agent route (`src/streaming.ts`); whole-result `?wait=result` unchanged.
  Model calls are non-streaming at the wire by default (MTP draft-token
  corruption mitigation, `src/providers/nonstreaming-completions.ts`;
  `ZOE_BRAIN_TOKEN_STREAMING=true` restores token streaming), so reply text
  reaches the sentinel stream as one final chunk.
  Reached from prod only via the default-OFF `ZOE_BRAIN_BACKEND=flue` seam; may
  be supervised via the opt-in unit template (see Forbidden above). Operator
  measurement checklists pending in `flue-zoe-brain/LANDING.md`.
- `flue-zoe-telegram/` — Flue Telegram channel: long-poll bot bridged to zoe-data's
  `/api/chat` (NOT a Flue LLM agent; `src/agents/zoe.ts` is a build-only placeholder
  and registers no model provider — never points at the voice brain on `:11434`).
  Hand-started, demo-only; README is a record, not a contract.
