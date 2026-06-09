# Zoe Memory Admission Gates

Zoe memory admission is the contract between pending retain candidates and
trusted durable memory. Reflection, extraction, and sidecar recall may propose
memory, but they cannot silently promote it into truth.

The executable contract lives in
`services/zoe-data/zoe_memory_admission.py`.

## Rules

- Pending retain candidates may be kept for review.
- Durable writes require approval evidence.
- Durable writes require a successful admission or verification trace.
- Failed or blocked traces prevent promotion.
- Graphiti-style targets require a relationship or supersession edge.
- Self-evolution memories require approved proposal context.
- Trace `user_id` values must match the memory candidate user.

## Current Scope

This is a schema and decision contract only. It does not write to
MemoryService, Hindsight, Graphiti, or Multica. Runtime wiring should happen in
a later small PR after this contract is reviewed and tested.

## Intended Flow

1. Memory extraction or Hindsight reflection creates a pending candidate.
2. Zoe records observation traces for recall, retain, admission, or
   verification.
3. Multica or a reviewer supplies approval evidence.
4. `evaluate_memory_admission()` returns whether Zoe may keep the candidate
   pending or write durable/trusted memory.
5. The runtime writer uses the decision as a gate before touching any backend.
