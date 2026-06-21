---
type: Reference
title: Evolution Nightly Notice — Loop Contract
description: Loop-Engineering contract for the Evolution Nightly Notice autopilot (Multica id b2025eea, Self-Improvement Agent, nightly).
tags: [autopilots, multica, evolution, self-improvement]
timestamp: 2026-06-18T00:00:00Z
---

# Evolution Nightly Notice — Loop Contract

Nightly Multica autopilot. Id `b2025eea`, assignee **Self-Improvement Agent**. See the [bundle index](index.md).

## Job
Each night, analyse `evolution_proposals` for newly surfaced capability gaps and ensure each real gap is tracked by exactly one Multica issue — creating new issues for new gaps and updating existing issues as gaps evolve.

## Inputs
- New and recently changed `evolution_proposals` records since the last run.
- Existing open capability-gap Multica issues (for de-duplication and updates).
- The most recent [Weekly Digest](evolution-weekly-digest.md) for priority context.

## Allowed
- Read and analyse `evolution_proposals`.
- Create a capability-gap Multica issue for a genuinely new gap, with a clear description and evidence (linked proposals).
- Update an existing capability-gap issue (status note, new supporting proposals) when the gap recurs or evolves.

## Forbidden
- NEVER create, modify, or delete `evolution_proposals` rows or any database record; analysis is read-only.
- NEVER open a duplicate issue for a gap already tracked — match against existing issues first and update instead.
- NEVER modify application code, config, skills, intents, secrets, or env files; it files capability-gap issues, it does not implement them.
- NEVER auto-assign, start, or trigger any engineering/implementation work, and never close issues it did not open.
- NEVER touch the live checkout or push to `main`.
- NEVER batch-create issues for noise; a proposal must clear the gap threshold before it earns an issue, and a quiet night creates nothing.
- NEVER escalate or change priority of issues it did not create.

## Output
- For each new qualifying gap: one new capability-gap Multica issue with evidence.
- For each recurring gap: an updated existing issue.
- On a quiet night: no new issues, an idempotent run record.

## Evaluation
- Runs to completion well within the 1800s budget.
- Exactly one tracking issue per real capability gap (no duplicates, no orphan noise).
- Every created/updated issue cites the `evolution_proposals` evidence behind it.
- No writes to data, code, or unrelated issues.
