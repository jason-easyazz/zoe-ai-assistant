---
type: index
title: Multica Autopilot Loop Contracts
description: Loop-Engineering contracts for Zoe's live Multica autopilots — Job, Inputs, Allowed, Forbidden, Output, and Evaluation per agent. Records, not DOX contracts.
tags: [autopilots, multica, loop-engineering, contracts]
timestamp: 2026-06-18T00:00:00Z
---

# Multica Autopilot Loop Contracts (OKF bundle)

Open Knowledge Format bundle recording the **Loop-Engineering contracts** for the three live Multica autopilots that run on the Multica board. Part of the parent [Zoe Knowledge bundle](../index.md); see also the [Zoe tool stack](../zoe-tool-stack.md) for what Multica, Hermes, and the Self-Improvement Agent are.

This is **knowledge / records** (descriptive facts about how each autopilot is scoped), not a DOX contract. Binding rules live in `AGENTS.md`. The autonomous knowledge loop may curate this bundle.

## Why these contracts exist

All three autopilots `create_issue` on the Multica board. They previously failed en masse because the agent timeout was 120s and they had no scope or **Forbidden** discipline, so they ran away. The timeout is now fixed at 1800s; these contracts give each autopilot an explicit, load-bearing scope so it never runs away again. The most important field in every contract is **Forbidden** — what the agent must NEVER do.

Each contract states six fields:
- **Job** — what the autopilot owns.
- **Inputs** — what it may inspect.
- **Allowed** — what it may change.
- **Forbidden** — what it must NEVER do (most load-bearing).
- **Output** — what exists after a successful run.
- **Evaluation** — how success is measured.

## Concepts

- [Platform Health Check](platform-health-check.md) — id `3f9bb428`, assignee Hermes, daily; verifies docker / PostgreSQL / zoe-data / Hermes / OpenClaw health.
- [Evolution Weekly Digest](evolution-weekly-digest.md) — id `49d10c67`, Self-Improvement Agent, weekly (Fri); digest of evolution proposals + intent-miss patterns + priorities.
- [Evolution Nightly Notice](evolution-nightly-notice.md) — id `b2025eea`, Self-Improvement Agent, nightly; analyses `evolution_proposals`, creates/updates capability-gap issues.
