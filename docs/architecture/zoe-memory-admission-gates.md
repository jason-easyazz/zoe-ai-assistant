# Zoe Memory Admission Gates

Zoe memory admission is the contract between pending retain candidates and
trusted durable memory. Reflection, extraction, and sidecar recall may propose
memory, but they cannot silently promote it into truth.

The executable contract lives in
`services/zoe-data/zoe_memory_admission.py`. Multica/review records can be
converted into the same inert decision shape by
`services/zoe-data/zoe_multica_memory_admission.py`. Terminal evolution
outcome memory candidates can be evaluated by
`services/zoe-data/zoe_evolution_outcome_admission.py`.

## Rules

- Pending retain candidates may be kept for review.
- Durable writes require approval evidence.
- Durable writes require a successful admission or verification trace.
- Failed or blocked traces prevent promotion.
- Graphiti-style targets require a relationship or supersession edge.
- Self-evolution memories require approved proposal context.
- Trace `user_id` values must match the memory candidate user.

## Current Scope

This is a schema, decision contract, Multica metadata bridge, and outcome
memory admission bridge only. It does not write to MemoryService, Hindsight,
Graphiti, MemPalace, or Multica. Runtime writer wiring should happen in a
later small PR after this contract is reviewed and tested.

## Intended Flow

1. Memory extraction or Hindsight reflection creates a pending candidate.
2. Zoe records observation traces for recall, retain, admission, or
   verification.
3. Multica or a reviewer supplies explicit `memory_admission_approved: true`
   approval evidence in Zoe ticket metadata, or a `blocked_reason` if the
   candidate must not be promoted.
4. `evaluate_memory_admission()` returns whether Zoe may keep the candidate
   pending or write durable/trusted memory.
5. The runtime writer uses the decision as a gate before touching any backend.

Outcome memory candidates follow the same rule: Zoe may build a pending event
from a terminal proposal outcome, but durable promotion still requires
`memory_admission` proposal context, approval refs, and successful
admission/verification evidence.

## Multica Bridge Rules

- Missing approval metadata remains pending review.
- Only explicit `memory_admission_approved: true` creates a Multica approval
  ref and successful admission trace.
- `blocked_reason` creates a blocked admission trace and prevents durable
  promotion even if an approval flag is present.
- Ticket metadata may request target backends, but Graphiti-style targets still
  require a relationship or supersession edge.
