# Zoe Observation Trace Collector

## Purpose

`services/zoe-data/zoe_observation_trace_collector.py` is the first governed
admission boundary for observation trace packets. It validates and summarizes
trace batches without writing to a database, memory backend, prompt context, or
self-evolution record.

## Contract

- Persistence is disabled and rejected by policy.
- Trace batches are capped by `max_batch_size`.
- Every trace must pass `ObservationTrace.validate()`.
- Optional policy filters can restrict surfaces and trace types.
- Personal/shared trace validation remains enforced by the trace schema.
- Mixed-user batches are rejected by default.
- Mixed-scope batches can be rejected when a caller needs a stricter boundary.
- Accepted traces are returned to the caller with a summary; rejected traces
  return trace ids and reasons.

## Current Use

This is still an inert foundation:

- no database migration;
- no automatic trace writes;
- no prompt-time memory injection;
- no durable memory writes;
- no self-evolution proposal creation.

## Next Step

Runtime callers can route optional trace packets through this collector before a
future PR adds a durable, scoped trace store or forwards summaries into Multica
evidence.
