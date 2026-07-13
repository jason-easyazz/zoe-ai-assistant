---
type: Record
title: Fable-day handoff — 2026-07-07 (Samantha evolution plan + codebase audit)
description: What the 2026-07-07 Fable session landed (the complete Samantha evolution plan W0–W18 + execution packets + full-codebase review), where every SSOT lives, and cold-start prompts so Opus 4.8 / smaller models can continue without Fable. Read this before continuing any of these threads.
tags: [handoff, samantha, plan, packets, review, audit]
timestamp: 2026-07-07T00:30:00Z
---

# Fable-day handoff — 2026-07-07

> **STATUS UPDATE (2026-07-13):** this is a point-in-time handoff. **P-W0 is DONE** —
> merged as #1160, and the spoken positive control passed on 2026-07-13 after #1282
> fixed the transcript-save bugs it exposed (no Jason-spoken turn needed; run via the
> panel-bound device token). The current NEXT ACTION lives in
> `docs/architecture/samantha-evolution-plan.md` §7 (now **W3.1**), which supersedes
> the pointers below.

This session built the **post-memory Samantha evolution plan** end-to-end and audited
the whole codebase. Everything of substance lives in the repo (nothing is only in the
chat). Statuses as of 2026-07-07 ~00:30 AWST — verify merge states before acting.

## Landed (all merged to main)

- **#1070 + #1089 + #1091 + #1098 + #1100** — `docs/architecture/samantha-evolution-plan.md`:
  workstreams **W0–W18**, prior-art grounding (§8), OS-horizon (§9), execution protocol
  (§10), compute doctrine (§11 — build behind a model seam, OpenRouter interim by data
  class, senses+soul never leave the box).
- **#1095** — `docs/architecture/samantha-evolution-packets.md`: **cheap-model cold-start
  packets** (P-W0, P-W1.3, P-W1.4, P-W2.1, P-W2.2, P-W4.1, P-W5.1 + deferred table).
  One packet = one increment = one session = one PR. P0 protocol carries the traps.
  P-W3.2 is DONE (#1084) — do not execute.
- **#1087** — W1 status sync: **barge-in + Smart Turn are LIVE** (#1051/#1081, prod
  flags ON per #1082) — do NOT redo; also retired the overtaken #1056 migration doc.
- **#1104** — `docs/reviews/full-codebase-review-2026-07-07.md`: 5-domain audit;
  **F1–F5 fix-now list** + H1–H8 hardening + hygiene backlog.
- `docs/reviews/issue-register-2026-07-07.md` — the deeper 4-domain register
  (tests/flags, frontend, docs-drift, data layer), **every item verification-marked**
  ([V] re-verified / [A] verify-before-fixing) with a rejected-findings section so
  future audits don't re-report false positives.

## How to continue without Fable (the intended loop)

1. Read `docs/architecture/samantha-evolution-plan.md` **§7 NEXT ACTION** — always
   exactly one. Currently: **execute packet P-W0** (capture positive-control).
2. Take ONE packet from `samantha-evolution-packets.md`, obey its P0 protocol and STOP
   conditions, drive the PR to merge (resolve Greptile threads via GraphQL, REST-verify
   merged), then update plan §6/§7.
3. Fix-work: `docs/reviews/full-codebase-review-2026-07-07.md` in its corrected order
   **F1 → F3 → F4 → F5** (F2 is RETRACTED — do not execute; F1 is a one-file fix for a
   silently-failing prune that has never run, not a crash). Each F-item has file:line +
   fix shape; the deeper `issue-register-2026-07-07.md` marks which second-pass items
   are `[V]` verified vs `[A]` verify-before-fixing. **Full fix packets** (researched
   approach, exact test files to add, software needed, order) are in
   `docs/architecture/remediation-packets-2026-07.md` — take one packet per session.
4. Hard tasks route to the Omnigent fleet (claude_code/codex/pi workers) or Hermes; the
   plan's §11 doctrine covers when remote models are allowed.

## Operator-only steps left

- **P-W0 needs Jason** to speak one authenticated voice turn to the panel.
- **Flue quality re-run**: #1058's script mints the authenticated parity test user —
  run it, then re-run the voice parity gate (clears the 92.5%-vs-95% confound).
- **W3.1 ccd-cli cleanup** (~3.6 GB swap): host-side kill of stale `--resume` session
  processes; zero repo change.
- Speaker-ID enrollment (P-W5.1 operator steps) and any prod flag flips.

## Gotchas rediscovered this session

- Greptile's green check ≠ resolved threads; `behind` needs `update-branch` nudges;
  REST (`pulls/N --jq .merged`) is the only trustworthy merge check.
- Another lane may land your planned work while you plan it (#1051 shipped W1.1/W1.2;
  #1084 shipped W3.2) — **re-check §6 against fresh main before starting any packet**.
- `services/zoe-ui/dist/` is hand-maintained source (NOT build output); bump
  `SW_VERSION` in `dist/sw.js` when touching precached files.
- Branches auto-delete on merge — pushing to a just-merged branch silently recreates
  it; check PR state before pushing follow-ups.
