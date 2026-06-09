# Zoe Graphiti Fixtures

## Purpose

Graphiti remains Zoe's candidate for temporal relational truth: people, tools, capabilities, failures, fixes, approvals, recurring tasks, causality, and superseded facts.

This document records the first backend-neutral fixture set. It does not accept Graphiti for production use and does not place graph retrieval in chat.

## Harness

Files:

- `services/zoe-data/graphiti_bakeoff.py`
- `services/zoe-data/tests/test_graphiti_bakeoff.py`

The fixtures define synthetic Zoe episodes and expected relationship questions before any FalkorDB or Neo4j service is started. Later runners should ingest the same episodes into Graphiti, query each evaluation question, and score returned answers with the same helper functions.

Covered relationship topics:

- weather failure -> cause -> fix -> test evidence;
- memory preference supersession from an older Hindsight-first episode to a newer MemPalace-baseline episode;
- Multica governance approval and trust conditions;
- Hermes as the trusted planning/Greptile lane;
- Graphify refresh as a recurring system-understanding task.

## Acceptance Use

The next Graphiti bake-off PR should:

- run FalkorDB first;
- run Neo4j second only if feasible;
- ingest `GRAPHITI_EPISODES`;
- query `GRAPHITI_EVAL_QUERIES`;
- record p50/p95 latency;
- record CPU/RAM footprint;
- prove every returned edge or answer includes evidence;
- verify superseded facts are preserved rather than destructively overwritten;
- mark the graph path async-only if p95 exceeds 2 seconds or Jetson footprint is too high.

Graphiti must remain outside the normal chat hot path until those measurements exist.
