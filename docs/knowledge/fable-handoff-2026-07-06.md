---
type: Record
title: Fable-day handoff — 2026-07-06
description: What the last Claude Fable 5 day landed, the operator steps it left, and cold-start prompts for every unfinished item. Read this before continuing any of these threads.
tags: [handoff, flue, security, parity, tech-debt]
timestamp: 2026-07-06T14:00:00Z
---

# Fable-day handoff — 2026-07-06

One-day burst on the strongest available model, spent on live-brain surgery,
judgment calls, and design packets cheaper models can execute. This records
what landed, what only the operator can do, and self-contained prompts for
the rest. Statuses are as of 2026-07-06 ~14:00 AWST — verify merge states
before acting.

## Landed (merged)

- **#1052 — mcporter `ok:false` class closed at the root.** The real cause sat
  one level deeper than the tech-debt plan's diagnosis: a stale rotated
  `POSTGRES_URL` baked into `~/.mcporter/mcporter.json` pre-empted
  `bootstrap_runtime_env()` (pre-set env wins), so every spawned
  `mcp_server.py` failed DB auth and crashed mid-call. Host config fixed live
  (baked secret removed; backup `~/.mcporter/mcporter.json.bak-2026-07-06`);
  repo guarantees: fail-loud exit-1 on pool-init failure, `_run_mcporter` off
  the on-loop fork (#947 class), regression tests, doc reconciliation.
  **Rule to keep: never bake `POSTGRES_URL` into agent configs** (recorded in
  [zoe-tool-stack.md](zoe-tool-stack.md)).
- **#1054 — loopback impersonation closed in code.** `X-Zoe-User-Id` override
  on `/api/chat` now requires a valid `X-Internal-Token`; bare loopback is
  denied with a journal warning. Fails closed when the token is unprovisioned.
- **#1060 — chat.py split + typed-config execution packet** (Wave 4 prep,
  design-only).
- Verified already-shipped (docs were stale, reconciled in #1052): per-request
  identity in the flue sidecar (#998/#1000/#1001 — AbortSignal WeakMap +
  ` zoe-uid:` envelope; AsyncLocalStorage was proven broken through
  `?wait=result`).

## Auto-merge armed / converging at write time

- **#1058** — parity test-user provisioning script (three Greptile P1s fixed:
  real `auth_manager` import, policy-compliant password, working
  `--rotate-password` recovery).
- **#1056** — ambient-voice GO/NO-GO: agent verdict **fallback-first** (Smart
  Turn v3 + barge-in on the existing LiveKit agent, not a Pipecat migration).
- **#1057** — Multica retire-gate spec (§8.1 executable packet).
- **#1059** — voice_tts.py split execution packet.

## Operator steps (Jason only — order matters)

1. **Provision `ZOE_INTERNAL_TOKEN`** (blocked for agents by design):
   generate once (`python3 -c "import secrets;print(secrets.token_hex(32))"`),
   put the SAME value in `services/zoe-data/.env` AND
   `labs/flue-zoe-telegram/.env`, then restart `flue-zoe-telegram.service`,
   then `zoe-data.service`. Until then Telegram turns run as guest (journal
   warning says why). Follow-up PR after provisioning: token-gate
   `POST /api/system/intent-dispatch` (same impersonation class; the flue
   sidecar already sends the token once its env has it — restart it first).
2. **Voice replay gate for #1052's in-process half:** hold the next `zoe-data`
   restart until the nightly `zoe-voice-regression` timer run is green
   (2026-07-06 evidence on the PR: 3/3 function-OK direct harness run; full
   20-sample baseline run self-deferred all day on the memory guard).
3. **Clean parity quality re-run** (when the box is quiet): run
   `scripts/maintenance/provision_parity_test_user.py`, then the recipe in
   #1058's body. If prod still leads on quality (95 vs 92.5 confound-corrected),
   the diagnosis order is: recall packet width → temperature/sampling parity →
   prompt shape.

## Needs a human decision

- **Barge-in convergence:** parallel-lane PR **#1051** (barge-in + Silero VAD
  + Smart Turn) is W1 of the Samantha evolution plan (#1070,
  `docs/architecture/samantha-evolution-plan.md`) and lands the same direction
  #1056's fallback-first verdict recommends — the plans AGREE, but two
  implementations can still collide at file level. Sequence: land #1051 first,
  then treat #1056's migration plan as the follow-on roadmap. Also from that
  lane: #1053/#1062/#1068/#1069 (compose/UI series), #1055, #1065.
- **#982** (db-leak sweep 1/3): merge-conflicted with main, needs a rebase;
  parts 2/3 unstarted.

## Cold-start prompts for the rest

- *Intent-dispatch token gate:* "In zoe-ai-assistant, ZOE_INTERNAL_TOKEN is now
  provisioned in services/zoe-data/.env and the flue sidecar env (verify, and
  verify both services restarted since). Apply the same token requirement
  resolve_acting_user got in #1054 to POST /api/system/intent-dispatch in
  services/zoe-data (it accepts a body user_id under loopback trust — same
  impersonation class). Regression tests like test_telegram_account_linking's
  denial tests. Replay-gate: this is the live brain write path — run
  scripts/maintenance/voice_regression_probe.py under
  flock /tmp/zoe-voice-harness.lock before merge."
- *people_relate retirement:* "Branch origin/chore/retire-people-relate exists
  with no PR. Per docs/adr/ADR-relationship-memory.md and the tech-debt plan,
  the people_relate intent is a redundant dead path (backend
  person_extractor._write_relationship is live). Finish the branch or redo
  small: remove/alias the intent, update tests, PR + Greptile loop."
- *#982 revival:* "PR #982 (get_db() leak sweep 1/3) is merge-conflicted.
  Rebase onto fresh main in a worktree (squash to one commit if history is
  messy), re-run its tests, re-request review, arm auto-merge. Then assess
  what 2/3 and 3/3 were meant to cover (see the PR body) and open them."
- *Wave 4 execution:* "Execute docs/architecture/voice-tts-split-plan.md
  (after #1059 merges) and docs/architecture/chat-split-and-typed-config-plan.md
  (merged, #1060) step by step — one PR per step, replay gate as specified in
  each packet. Do not batch steps."
- *Ambient voice next increment:* "Read
  docs/architecture/ambient-voice-migration-plan.md (#1056). Execute its
  first fallback-first increment (Smart Turn v3 endpointing on the LiveKit
  agent) in the lab, gated per the plan. NOTE: reconcile with PR #1051 first —
  it may already implement part of this."
