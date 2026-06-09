# Zoe Graphiti Fixtures

## Purpose

Graphiti remains Zoe's candidate for temporal relational truth: people, tools, capabilities, failures, fixes, approvals, recurring tasks, causality, and superseded facts.

This document records the first backend-neutral fixture set. It does not accept Graphiti for production use and does not place graph retrieval in chat.

## Harness

Files:

- `services/zoe-data/graphiti_bakeoff.py`
- `services/zoe-data/graphiti_sidecar_probe.py`
- `scripts/maintenance/graphiti_sidecar_probe.py`
- `services/zoe-data/tests/test_graphiti_bakeoff.py`
- `services/zoe-data/tests/test_graphiti_sidecar_probe.py`

The fixtures define synthetic Zoe episodes and expected relationship questions before any FalkorDB or Neo4j service is started. Later runners should ingest the same episodes into Graphiti, query each evaluation question, and score returned answers with the same helper functions.

The sidecar probe is read-only and should be run before any Graphiti ingestion bake-off:

```bash
PYTHONPATH=services/zoe-data python3 scripts/maintenance/graphiti_sidecar_probe.py --json
```

Probe statuses:

- `disabled`: `GRAPHITI_ENABLED` is false; no backend TCP check, graph query, ingest, or writes.
- `misconfigured`: offline-only policy or visible env parsing failed.
- `offline`: enabled config is valid, but the selected backend TCP endpoint is not reachable.
- `healthy`: enabled config is valid and the selected backend TCP endpoint accepts a connection.

Probe payloads separate `ok` from `acceptable`. `ok` means the selected backend is actively reachable.
`acceptable` means the operational state is allowed for the current rollout, so `disabled` is acceptable
while Graphiti remains outside the chat hot path.

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

## Current Zoe-Host Check

Date: 2026-06-09

Environment:

- Host: Zoe Jetson service host.
- Default backend: FalkorDB at `127.0.0.1:6379`.
- Runner mode: default disabled config, no graph ingest, no graph query, no writes.
- Probe status: disabled with `GRAPHITI_ENABLED=false`.
- Enabled FalkorDB preflight: `GRAPHITI_ENABLED=true` returned `offline`, `tcp_reachable=false`,
  `reason="[Errno 111] Connection refused"`, exit code 2, and no process/container hits.

This is not a Graphiti acceptance result. It is an availability baseline and a read-only preflight. The
Graphiti bake-off remains incomplete until a local/offline FalkorDB sidecar is started and measured, then
Neo4j is tested if feasible.
