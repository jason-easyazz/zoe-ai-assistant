# ADR: Zoe Memory Layer Direction

## Status

Accepted for staged implementation.

## Context

Zoe already has MemPalace through `MemoryService`, Graphify for code/system understanding, Multica for governed execution, and Gemma 4 as the local model path. The Samantha goal requires more than semantic recall: Zoe needs scoped, evidence-backed, temporal relationships between people, tools, failures, fixes, approvals, recurring tasks, and capabilities.

## Decision

Use a layered memory architecture:

- Keep MemPalace for fast associative recall.
- Add a portable Samantha memory contract before any new backend integration.
- Evaluate Hindsight first for Postgres-native experience memory and reflection.
- Evaluate Graphiti second for temporal relational truth.
- Keep Graphify as code/system graph intelligence.
- Keep Multica as the required governance layer for self-evolution writes and execution.

## Consequences

- Memory writes that create relational truth or self-evolution claims require evidence.
- Auto-recall may be enabled; blind auto-retain remains disabled.
- New backends are sidecars/bake-offs until measured.
- The current chat router is not modified by this ADR.
