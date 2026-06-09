# Zoe Evolution Proposal Contract

## Purpose

Zoe's self-evolution loop needs a structured proposal record before it changes code, installs tools, starts sidecars, writes trusted memory, or promotes a capability. The contract in `services/zoe-data/zoe_evolution_proposal.py` defines that record without executing it.

This is the bridge between the harness plan and Multica:

- Notice: an evidence-backed signal is captured.
- Explain: the signal becomes a problem statement.
- Search: a candidate is selected from Pi, MCP, GitHub, skills, APIs, local services, or existing Zoe surfaces.
- Evaluate: the candidate carries Zoe's score, license, offline viability, security, footprint, and overlap evidence.
- Propose: the proposal records affected capabilities, risk, expected benefit, verification, rollback, and approval requirements.

## Contract Rules

- Every signal requires evidence.
- Every proposal requires at least one signal.
- Every proposal requires a scored candidate.
- Every proposal requires affected capabilities, verification, rollback, and evidence.
- Execute and promote proposals require explicit approvals.
- High-risk and privileged proposals require approval.
- The proposal contract never grants execution by itself.

`approval_gate.allowed_to_execute` is intentionally always false. A valid proposal can be prepared and handed to Multica or review, but privileged execution must happen through the governed runtime path.

## Why This Matters

This gives Zoe a durable, testable shape for self-improvement before the runtime loop becomes more autonomous. It prevents a user request, reflection, or failed tool run from becoming an unchecked install, trusted memory, or code change.

It also attaches candidate scoring to proposals, so Zoe can compare existing Pi/MCP/GitHub/local options before building new bespoke code.

## Current Scope

The foundation contract is now wired into Zoe's live proposal writers:

- the legacy `evolution_proposals` row shape remains unchanged for current UI,
  Multica, and review routes;
- a validated `zoe_evolution_proposal` contract snapshot is stored as JSON in
  the existing `target_patterns` field;
- nightly NOTICE proposals, MCP-created proposals, user-frustration proposals,
  and explicit user issue reports all emit the snapshot;
- MEASURE still supports legacy target-pattern behavior by reading
  `legacy_target_patterns` from contract metadata;
- `multica_issue_id` remains authoritative in the legacy column; the stored
  contract snapshot is not rewritten just to mirror that live sync field;
- Multica tickets created from evolution proposals carry compact contract
  markers in their `zoe-ticket` metadata, and admission refuses approved
  evolution-proposal tickets when those markers are absent or mismatched;
- proposal creation remains review-only and never grants execution;
- `approval_gate.allowed_to_execute` remains false.

This still avoids:

- no automatic execution;
- no automatic memory promotion;
- no chat hot-path change.

The next slices should extend these admission rules to memory and capability
promotion, then require explicit approval evidence before installs or
replacements can execute.
