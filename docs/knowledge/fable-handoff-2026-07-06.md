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

- *Intent-dispatch token gate:* **DONE 2026-07-07 (#1090, replay-gated 8/8).**
  Verification found the precondition only ⅔ met (`labs/flue-zoe-brain/.env`
  still lacks the token), so the strict gate shipped DARK behind
  `ZOE_INTENT_DISPATCH_REQUIRE_TOKEN` with per-caller readiness warnings.
  Enable sequence + the remaining same-class residual (`delegate-sync` body
  `user_id`) recorded in the tech-debt plan's loopback entry.
- *people_relate retirement:* **OVERTAKEN — already retired on main** (zero
  `people_relate` references in services/zoe-data as of 2026-07-07; verified
  `git grep`). The stale draft branch `origin/chore/retire-people-relate`
  can simply be deleted; do not redo the work.
- *#982 revival:* **DONE 2026-07-07 — all three parts.** 1/3 merged (`bacbc953`,
  pattern re-applied over #1015's panel_auth refactor; CI-enumeration hunk
  replaced with the ci_safe marker). 2/3 (routers/chat.py, 20 sites) and 3/3
  (intent_router.py, ~12 sites) dispatched same day; `test_get_db_leak_scan.py`
  enforces cleaned files stay clean.
- *Wave 4 execution:* "Execute docs/architecture/voice-tts-split-plan.md
  (after #1059 merges) and docs/architecture/chat-split-and-typed-config-plan.md
  (merged, #1060) step by step — one PR per step, replay gate as specified in
  each packet. Do not batch steps."
- *Ambient voice next increment:* "OVERTAKEN — #1051 already shipped barge-in +
  Smart Turn v3 endpointing (real-voice verified #1081, prod flags ON #1082), and the
  #1056 migration-plan doc was retired as redundant. The remaining ambient-voice work
  is tracked in docs/architecture/samantha-evolution-plan.md: W1.3 (sentence-streamed
  TTS in the LiveKit lane) + W1.4 (M3 latency / M4 RAM measurement session)."
