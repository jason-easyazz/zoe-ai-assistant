---
type: Reference
title: Evolution Weekly Digest — Loop Contract
description: Loop-Engineering contract for the Evolution Weekly Digest autopilot (Multica id 49d10c67, Self-Improvement Agent, weekly Fri).
tags: [autopilots, multica, evolution, self-improvement]
timestamp: 2026-06-18T00:00:00Z
---

# Evolution Weekly Digest — Loop Contract

Weekly Multica autopilot (Fridays). Id `49d10c67`, assignee **Self-Improvement Agent**. See the [bundle index](index.md).

## Job
Produce one weekly digest summarising the state of Zoe's self-improvement signal: recent evolution proposals, recurring intent-miss patterns, and the resulting priorities for the coming week.

## Inputs
- `evolution_proposals` records from the past week.
- Intent-miss / unmatched-intent logs and patterns over the week.
- The existing open capability-gap issues (for cross-referencing priorities).
- The prior week's digest (for continuity).

## Allowed
- Read and aggregate the inputs above into a single digest.
- Create one Multica digest issue per week summarising proposals, the top intent-miss patterns, and a ranked priority list.
- Link the digest to existing capability-gap issues by reference.

## Forbidden
- NEVER create, modify, or delete `evolution_proposals` rows or any database record; it is read-only over the data.
- NEVER open per-gap implementation issues — that is the Nightly Notice's job; the Weekly Digest only summarises and links.
- NEVER create more than one digest issue per run, and never reopen or rewrite past digests.
- NEVER modify application code, config, skills, intents, secrets, or env files.
- NEVER touch the live checkout or push to `main`.
- NEVER auto-assign, prioritise into execution, or trigger any engineering work; it reports priorities, humans schedule them.
- NEVER fabricate proposals or patterns to fill the digest; an empty week yields an explicit "no new signal" digest.

## Output
- Exactly one weekly digest Multica issue containing: this week's evolution proposals, the top intent-miss patterns with counts, and a ranked priority list with links to existing gap issues.

## Evaluation
- Runs to completion well within the 1800s budget.
- One digest per week, no duplicates, continuous with the prior week.
- Counts and links in the digest reconcile with the underlying `evolution_proposals` and intent-miss logs.
- No writes to data, code, or other issues beyond the single digest.
