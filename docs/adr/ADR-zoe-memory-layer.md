# ADR: Zoe Memory Layer Direction

## Status

Accepted for staged implementation.

## Context

Zoe already has MemPalace through `MemoryService`, Graphify for code/system understanding, Multica for governed execution, and Gemma 4 as the local model path. The Zoe continuity goal requires more than semantic recall: Zoe needs scoped, evidence-backed, temporal relationships between people, tools, failures, fixes, approvals, recurring tasks, and capabilities.

## Decision

Use a layered memory architecture:

- Keep Working Context short-lived and disposable for active conversation state.
- Keep PostgreSQL as canonical state/truth for live app data and operational records.
- Keep `MemoryService`/MemPalace as the episodic memory gatekeeper for fast personalization and exact remembered facts.
- Add a portable Zoe memory contract before any new backend integration.
- Evaluate Hindsight first as optional reflective memory for lessons, recurring patterns, failures/fixes, and experience summaries.
- Evaluate Graphiti second only for temporal relational truth that changes over time.
- Keep Graphify as code/system graph intelligence.
- Keep Multica/review as the required governance layer for self-evolution writes and execution.
- Add observation/evaluation traces so memory reads and writes can be judged by latency, helpfulness, hallucination, contradiction, and fallback behavior.

## Consequences

- Memory writes that create relational truth or self-evolution claims require evidence.
- Auto-recall may be enabled; blind auto-retain remains disabled.
- New backends are sidecars/bake-offs until measured.
- Hindsight, Graphiti, and any new sidecar remain async, feature-flagged, timeout-bounded, and fallback-safe.
- The current chat router is not modified by this ADR.
