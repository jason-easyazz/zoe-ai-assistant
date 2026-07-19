---
type: index
title: Zoe Knowledge Bundle
description: Curated, agent- and human-readable reference knowledge for Zoe, in Open Knowledge Format (OKF). Governed by docs/AGENTS.md.
---

# Zoe Knowledge (OKF bundle)

Open Knowledge Format bundle — markdown + YAML frontmatter, cross-linked, readable by humans without tooling and by agents without bespoke SDKs.

This is **knowledge / records** (descriptive facts), not a DOX contract. See the root `AGENTS.md` rule "Knowledge vs. Records (OKF)": binding rules live in `AGENTS.md`; this bundle curates what is *true* about Zoe and may be maintained by the autonomous knowledge loop.

## Concepts

- [Zoe tool stack](zoe-tool-stack.md) — the installed agent tooling (graphify, opensrc, Multica, Pi, Hermes, OpenClaw, MemPalace, SkillSpector) and how the pieces relate.
- [Runtime topology](runtime-topology.md) — the live runtime: host, services, ports, where each is served from and logs to, the touch panel, and the no-pipeline deploy. Orientation before touching the running system.
- [Voice pipeline](voice-pipeline.md) — the STT → brain → TTS path, the replay-sample regression corpus, and the caveat that the warm harness understates real live latency.
- [Merge & deploy discipline](merge-and-deploy.md) — merged ≠ live, the protected-`main` gates, and the Greptile/greploop gotchas (large-PR skip, thread resolution, REST-not-GraphQL).
- [Production incident runbook](incident-runbook.md) — verified failure signatures + fixes: the zoe-data accept-queue hang (health 000, service "active") and root-owned lab-container files blocking every deploy; prevention rules for lab containers. Also: the voice stack swapped out — replies chopped + /health timing out while everything reports healthy (2026-07-19).
- [Relationship-memory flag-enable runbook](relationship-memory-flag-enable.md) — how the three merged-but-dark relationship features (temporal edges, graph traversal, person-merge) go live: migrate 0015 first, flip flags incrementally behind the voice replay gate, verify on a demo user, and roll back with the flags (not a schema downgrade). Also covers the independent recall-dossier flags (compact per-person line in the recall packet).
- [Contacts / people-memory operational reference](contacts-people-memory.md) — the contacts-from-known-people loop end to end, the live flag state, the **write-path FK bug class** that bit three times (writes keyed on `user_id` from non-chat paths silently FK-fail without a `users` row; two were swallowed exceptions), and the E2E harness traps (token-gated override, accept needs a real session, 30s async capture). Read before touching a people/suggestion write.
- [Fable-day handoff 2026-07-06](fable-handoff-2026-07-06.md) — what the last Fable 5 day landed (mcporter ok:false root cause, token-gated identity override, Wave-4 packets), the operator steps it left, and cold-start prompts for every unfinished thread.
- [Fable-day handoff 2026-07-07](fable-handoff-2026-07-07.md) — the Samantha evolution plan session: W0–W18 + execution packets + full-codebase review all landed; the continue-without-Fable loop (§7 NEXT ACTION → one packet → merge → update §6), operator-only steps, and rediscovered gotchas.
- [Memory pressure profile (2026-07-06)](memory-pressure-profile.md) — measured RAM/swap ownership on the Orin NX (llama-server 4.1 GB swap, ccd-cli fleet 3.6 GB, zoe-data 1 GB), what loads memory inside zoe-data, the chromadb-0.6.3 finding that the audit client is NOT a second embedder copy, and the sized candidate reductions incl. the LiveKit on-demand-reap pattern. **STATUS 2026-07-19: the two biggest swap owners are FIXED** (cgroup guards; llama-server + kokoro now hold 0 swap) — read the header before trusting these numbers.
- [Test-isolation debugging playbook](test-isolation-playbook.md) — how to hunt a passes-alone-fails-in-full-run failure to its exact poisoner: the mechanical ignore-set bisect, the side-file env/sys.modules trap, the one bug class behind all five 2026-07-06 leaks (global state mutated without identity-restore), and the fix patterns in preference order.
- [Two-stage router rollout](two-stage-router-rollout.md) — staged rollout runbook for the SetFit-shortlist + FunctionGemma-sidecar router behind `ZOE_ROUTER_HEAD` (off → shadow → shadow2 → active): the `router_rollout.sh` driver, verification checklist per stage, flag-based rollback, and where the shadow2 numbers land.
- [Router self-training loop](router-selftrain-loop.md) — how the two-stage router retrains itself on mined real-traffic mistakes and promotes a new model ONLY if provably better: the **ratchet** (no accuracy regression, zero chat-FP, p50 under budget, voice replay gate actually *ran* and passed — no override flag, ever), the frozen-corpus leak guard, `ZOE_ROUTER_SELFTRAIN` (default off), auto-rollback to last-known-good, and where the journal/scoreboard live. Includes the warm-start-checkpoint prerequisite.
- [Flue cutover tool cut list](flue-cutover-tool-cut-list.md) — decision of record (signed off 2026-07-03) for the Flue brain cutover: the 18-item cut list closing the "~56 tools" parity question, the revised 12 → 20 target via Waves 1–3, and the dispositions for each legacy tool. Re-pointed from `docs/PLANS.md` and `zoe-flue-integration.md` §10.

## Bundles

- [Multica autopilot loop contracts](autopilots/index.md) — Loop-Engineering contracts (Job / Inputs / Allowed / Forbidden / Output / Evaluation) for the three live Multica autopilots.
