# ADR: Hindsight Bake-Off First

## Status

Accepted for evaluation.

## Context

Hindsight aligns with Zoe's Samantha goal because it separates world facts, experiences, mental models, and reflection. It also fits Zoe's Postgres-centered architecture better than graph stacks that require another always-on database in the chat hot path.

## Decision

Evaluate Hindsight before Graphiti as a runtime candidate.

Defaults for the bake-off:

- Run as a sidecar first.
- Prefer controlled recall before any write integration.
- Keep auto-retain off by default.
- Route durable writes through retain candidates and evidence/admission gates.
- Measure recall latency, write latency, memory pollution, scope isolation, and evidence provenance.

## Acceptance Criteria

- Recall p95 under 600ms for low/mid budget.
- Retain can run async without blocking chat.
- Returned memories include evidence/source pointers.
- Disputed/superseded memories behave correctly.
- Missing or cross-user scope fails closed.
- Zoe can disable Hindsight without breaking MemPalace recall.
