# ADR: Graphiti Bake-Off Second

## Status

Accepted for evaluation.

## Context

Graphiti is the strongest candidate for temporal relationship truth: entities, typed facts, validity windows, provenance episodes, and hybrid semantic/keyword/graph retrieval. It is heavier operationally than Postgres-native memory and must be measured before it enters Zoe's hot path.

## Decision

Evaluate Graphiti after the Hindsight bake-off.

Defaults for the bake-off:

- Test FalkorDB first for lighter operational footprint.
- Test Neo4j if feasible as the mature graph baseline.
- Keep graph retrieval out of normal chat until measured.
- Use Graphiti for relational questions, evolution analysis, and audit views.
- Require evidence-backed edges and supersession support.

## Acceptance Criteria

- Accurate multi-hop answers for people/tools/capabilities/failures/fixes/approvals/recurring tasks.
- Superseded facts are preserved, not destructively overwritten.
- Every relationship edge traces to evidence.
- Query p95 is under 2s for relational lookups, or marked async-only.
- Co-located Jetson memory usage is acceptable, or the service is sidecar/remote-only.
