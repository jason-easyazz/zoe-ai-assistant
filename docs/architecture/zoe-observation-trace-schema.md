# Zoe Observation Trace Schema

## Purpose

Zoe needs proof that memory and self-evolution are helping before those systems are wired into the hot path. The schema in `services/zoe-data/zoe_observation_trace.py` defines small evidence records for memory route decisions, recall, retain candidates, admission, contradiction, fallback, proposal, verification, outcome eval, and hardware-budget events.

This is an executable trace contract, not a persistence layer.

## Trace Types

- `memory_route`: deterministic memory-router decision or skipped route.
- `recall`: memory or graph recall attempt.
- `retain_candidate`: proposed memory or lesson before admission.
- `admission`: review/admission decision for memory or capability promotion.
- `contradiction`: correction, dispute, or supersession event.
- `fallback`: timeout, unavailable sidecar, or safe fallback path.
- `proposal`: self-evolution proposal creation or update.
- `verification`: tests, validators, health checks, or review result.
- `outcome_eval`: task completion, correction handling, continuity, friction, trust, or cleanup quality eval.
- `hardware_budget`: CPU, RAM, GPU, latency, or sidecar footprint measurement.

## Contract Rules

- Personal and shared traces require `user_id`.
- Retain candidate, admission, contradiction, proposal, verification, and outcome eval traces require evidence.
- Metadata cannot contain secret-like fields.
- Latency must be non-negative.
- Helpfulness and confidence are normalized from `0` to `1`.
- Trace summaries can compute p50/p95 latency and outcome/type counts.
- Repeated failed outcome eval traces can become Zoe `EvolutionSignal` records, with an optional idempotency key and existing-signal guard for future persistence.
- Memory-route traces store safe route metadata such as query length, selected backend, and safety booleans; they do not store raw query text.

## Current Scope

This slice is still non-persistent:

- no database migration;
- memory-router runtime calls can return optional trace packets;
- no automatic durable trace writes;
- no hot-path recall changes;
- no automatic proposal creation.

Next slices should wire traces around retain candidates, sidecar bake-offs, Multica admission, and capability promotion/retirement.
