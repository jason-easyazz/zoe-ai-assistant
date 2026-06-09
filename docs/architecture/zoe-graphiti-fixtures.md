# Zoe Graphiti Fixtures

## Purpose

Graphiti remains Zoe's candidate for temporal relational truth: people, tools, capabilities, failures, fixes, approvals, recurring tasks, causality, and superseded facts.

This document records the first backend-neutral fixture set. It does not accept Graphiti for production use and does not place graph retrieval in chat.

## Harness

Files:

- `services/zoe-data/graphiti_bakeoff.py`
- `services/zoe-data/graphiti_sidecar_probe.py`
- `services/zoe-data/graphiti_runtime_probe.py`
- `scripts/maintenance/graphiti_sidecar_probe.py`
- `scripts/maintenance/graphiti_runtime_probe.py`
- `services/zoe-data/tests/test_graphiti_bakeoff.py`
- `services/zoe-data/tests/test_graphiti_sidecar_probe.py`
- `services/zoe-data/tests/test_graphiti_runtime_probe.py`

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

The runtime probe adds the next read-only gate before any ingest trial:

```bash
PYTHONPATH=services/zoe-data python3 scripts/maintenance/graphiti_runtime_probe.py --json
```

Runtime probe statuses:

- `disabled`: `GRAPHITI_ENABLED` is false; no package-gated backend probe, LLM check, graph query, ingest, or writes.
- `misconfigured`: offline-only policy or visible env parsing failed.
- `missing_dependency`: required local Python package(s) are unavailable for the selected backend.
- `backend_offline`: required Python packages are available, but the selected graph backend is not reachable.
- `llm_unavailable`: the configured local OpenAI-compatible model endpoint did not answer `/v1/models`.
- `llm_model_missing`: the endpoint answered, but did not advertise the configured local Gemma model.
- `ready_for_ingest_trial`: packages, backend TCP reachability, and local model advertisement are present.

`ready_for_ingest_trial` is not Graphiti acceptance. It only means Zoe is ready for the next
offline fixture ingest/query run.

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
- Runner mode: read-only probes and temporary smoke tests only; no accepted graph data, no
  production graph query, no chat path changes.
- Default probe status: disabled with `GRAPHITI_ENABLED=false`.
- Initial enabled FalkorDB preflight: `GRAPHITI_ENABLED=true` returned `offline`, `tcp_reachable=false`,
  `reason="[Errno 111] Connection refused"`, exit code 2, and no process/container hits.
- FalkorDB sidecar preflight: temporary `falkordb/falkordb:latest` container
  `zoe-falkordb-bakeoff` reached `healthy`, `tcp_reachable=true`, and about 37 ms probe latency.
- Zoe host Python runtime package availability: `graphiti_core=false`, `falkordb=false`,
  `neo4j=false`, and `redis=false`.
- Temporary package target outside the repo with `graphiti-core==0.29.2` and `falkordb==1.6.1`
  showed that Graphiti can connect to FalkorDB with a custom local embedder, but the current local
  Gemma endpoint did not satisfy Graphiti structured extraction. The smoke test failed with a
  Pydantic `ExtractedEntities` invalid JSON validation error after retries.

This is not a Graphiti acceptance result. It is an availability baseline and a read-only preflight. The
Graphiti bake-off remains incomplete until Zoe has approved optional local dependencies and a reliable
offline structured-output path for extraction, then the FalkorDB fixtures are ingested and measured.
Neo4j should still be tested only if feasible after FalkorDB produces useful evidence.
