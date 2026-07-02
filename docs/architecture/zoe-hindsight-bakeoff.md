# Zoe Hindsight Bake-Off

> **Status (2026-07-03): bake-off concluded — harness retired by removal.** The probe/bake-off modules, maintenance drivers, and tests listed below were removed from the tree per the retire-by-removing doctrine (docs/CANONICAL.md); git history keeps the code. File paths and commands in this document are a historical record of how the evidence was produced and are no longer runnable.

## Purpose

Hindsight remains an offline-only candidate for Zoe reflective memory: lessons, recurring failures, fixes, and experience summaries. It is not a replacement for MemPalace and it is not in the chat hot path.

This document records the current bake-off runner, Zoe-host availability checks, and the first live offline retain/recall result.

## Harness

Files:

- `services/zoe-data/hindsight_bakeoff.py`
- `services/zoe-data/hindsight_embedding_probe.py`
- `services/zoe-data/hindsight_sidecar_probe.py`
- `scripts/maintenance/hindsight_bakeoff.py`
- `scripts/maintenance/hindsight_embedding_probe.py`
- `scripts/maintenance/hindsight_sidecar_probe.py`
- `services/zoe-data/tests/test_hindsight_bakeoff.py`
- `services/zoe-data/tests/test_hindsight_embedding_probe.py`
- `services/zoe-data/tests/test_hindsight_sidecar_probe.py`

The runner uses the synthetic Zoe memory events from `hindsight_bakeoff.py`, can optionally retain them into a configured Hindsight sidecar, then measures recall scores and p50/p95 latency across the evaluation queries.

The embedding probe is read-only and should pass before a live sidecar bake-off:

```bash
PYTHONPATH=services/zoe-data python3 scripts/maintenance/hindsight_embedding_probe.py --json
```

Embedding probe statuses:

- `disabled`: `HINDSIGHT_ENABLED` is false; no health call, embedding request, retain, recall, or writes.
- `misconfigured`: offline-only policy rejected the visible embedding config.
- `missing_local_model`: local embeddings are configured, but no model path or Hugging Face cache entry exists.
- `local_model_available`: local embeddings have an existing model path or cache entry.
- `missing_onnx_model`: ONNX embeddings are configured, but the local model path does not exist.
- `onnx_model_available`: ONNX embeddings have an existing model path.
- `service_offline`: TEI or local OpenAI-compatible embeddings are configured, but the local/private service health check failed.
- `service_unhealthy`: the local/private embeddings service responded without ok/healthy status.
- `service_healthy`: the local/private embeddings service reported ok/healthy.

The sidecar probe is read-only and should be run before the bake-off:

```bash
PYTHONPATH=services/zoe-data python3 scripts/maintenance/hindsight_sidecar_probe.py --json
```

Probe statuses:

- `disabled`: `HINDSIGHT_ENABLED` is false; no health call, retain, recall, or writes.
- `misconfigured`: offline-only policy rejected the visible config.
- `offline`: enabled config is valid, but the sidecar health call failed.
- `unhealthy`: enabled config is valid and the sidecar responded, but not with an ok/healthy health body.
- `healthy`: enabled config is valid and the sidecar health response reports ok/healthy.

Probe payloads separate `ok` from `acceptable`. `ok` means the sidecar is actively healthy.
`acceptable` means the operational state is allowed for the current rollout, so `disabled` is acceptable
while bake-off runtime wiring remains off by default.

Command:

```bash
PYTHONPATH=services/zoe-data python3 scripts/maintenance/hindsight_bakeoff.py --json
```

To compare recall budgets in one run, repeat or comma-separate `--recall-budget` values. When this flag is present, the output includes both aggregate `latency` and `latency_by_recall_budget` blocks.

```bash
PYTHONPATH=services/zoe-data python3 scripts/maintenance/hindsight_bakeoff.py --recall-budget low,mid --json
```

To write synthetic events, the operator must explicitly enable Hindsight and opt into writes:

```bash
HINDSIGHT_ENABLED=true PYTHONPATH=services/zoe-data python3 scripts/maintenance/hindsight_bakeoff.py --retain-synthetic --json
```

Zoe memory remains offline-only. `HindsightConfig` rejects public/cloud LLM and embedding providers unless an OpenAI-compatible or TEI endpoint points to a localhost/private base URL.

## Current Zoe-Host Check

Date: 2026-06-09

Environment:

- Host: Zoe Jetson service host.
- Expected sidecar URL: `http://127.0.0.1:8888`.
- Sidecar status: not running; `curl --max-time 2 http://127.0.0.1:8888/health` returned connection refused.
- Process/container status: no Hindsight process or container was running.
- Runner mode: default disabled config, no retain, no writes.
- Probe status: disabled with `HINDSIGHT_ENABLED=false`; no health call, retain, recall, or writes.
- Embedding preflight: with `HINDSIGHT_ENABLED=true`, `HINDSIGHT_API_LLM_PROVIDER=llamacpp`, and the default local embedding model, `scripts/maintenance/hindsight_embedding_probe.py --json` reported `missing_local_model`.
- Checked embedding paths: `/home/zoe/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5` and `/home/zoe/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5/snapshots`.
- Local image check: `ghcr.io/vectorize-io/hindsight:latest` exists on the Zoe host.
- Offline start blocker at baseline: the image defaults its LLM provider to OpenAI unless Zoe overrides it, and the default local embedding model (`BAAI/bge-small-en-v1.5`) was not present in the host Hugging Face cache.

Measured disabled-run result:

| Metric | Value |
| --- | --- |
| Cases | 4 |
| Average score | 0.0 |
| Minimum score | 0.0 |
| p50 latency | 0.0024 ms |
| p95 latency | 0.0066 ms |
| Sidecar writes | 0 |

This disabled run is not a Hindsight acceptance result. It is an availability baseline and a fixed runner.

## Live Offline Bake-Off

Date: 2026-06-09

The default embedding blocker was cleared by caching `BAAI/bge-small-en-v1.5` on the Zoe host. The read-only embedding probe then reported:

| Probe field | Value |
| --- | --- |
| Status | `local_model_available` |
| Acceptable | `true` |
| Provider | `local` |
| Model | `BAAI/bge-small-en-v1.5` |
| Checked paths | `/home/zoe/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5`, `/home/zoe/.cache/huggingface/hub/models--BAAI--bge-small-en-v1.5/snapshots` |

The live sidecar was started as an isolated bake-off container, then removed after measurement:

```bash
docker run -d --name zoe-hindsight-bakeoff --network host \
  -v /home/zoe/.cache/huggingface:/root/.cache/huggingface \
  -e HINDSIGHT_API_DATABASE_URL=pg0 \
  -e HINDSIGHT_API_LLM_PROVIDER=openai \
  -e HINDSIGHT_API_LLM_API_KEY=local \
  -e HINDSIGHT_API_LLM_BASE_URL=http://127.0.0.1:11434/v1 \
  -e HINDSIGHT_API_LLM_MODEL=gemma-4-E2B-it-Q4_K_M.gguf \
  -e HINDSIGHT_API_EMBEDDINGS_PROVIDER=local \
  -e HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=BAAI/bge-small-en-v1.5 \
  -e HINDSIGHT_API_EMBEDDINGS_LOCAL_FORCE_CPU=true \
  -e HINDSIGHT_API_LLM_MAX_CONCURRENT=1 \
  -e HINDSIGHT_API_RETAIN_LLM_MAX_CONCURRENT=1 \
  -e HINDSIGHT_API_REFLECT_LLM_MAX_CONCURRENT=1 \
  -e HINDSIGHT_API_CONSOLIDATION_LLM_MAX_CONCURRENT=1 \
  ghcr.io/vectorize-io/hindsight:latest
```

Sidecar probe result:

| Field | Value |
| --- | --- |
| Status | `healthy` |
| Health | `{"status":"healthy","database":"connected"}` |
| Database | embedded PostgreSQL via `pg0` |
| LLM | Zoe local Gemma through `http://127.0.0.1:11434/v1` |
| Embeddings | local `BAAI/bge-small-en-v1.5` |
| Auto-retain | `false` |

Synthetic retain/recall command:

```bash
HINDSIGHT_ENABLED=true \
HINDSIGHT_BASE_URL=http://127.0.0.1:8888 \
HINDSIGHT_API_LLM_PROVIDER=openai \
HINDSIGHT_API_LLM_BASE_URL=http://127.0.0.1:11434/v1 \
HINDSIGHT_API_EMBEDDINGS_PROVIDER=local \
PYTHONPATH=services/zoe-data \
python3 scripts/maintenance/hindsight_bakeoff.py --retain-synthetic --retain-timeout-seconds 240 --json
```

Measured live retain/recall result:

| Metric | Value |
| --- | --- |
| Synthetic events retained | 4 |
| Retain parent operations completed | 4/4 |
| Retain extraction errors | 0 |
| Recall cases | 4 |
| Average score | 1.0 |
| Minimum score | 1.0 |
| p50 recall latency | 564.71 ms |
| p95 recall latency | 649.00 ms |
| Recall scores | 4/4 exact expected-term matches |

Warm recall-only pass:

| Metric | Value |
| --- | --- |
| Recall cases | 4 |
| Average score | 1.0 |
| Minimum score | 1.0 |
| p50 recall latency | 567.67 ms |
| p95 recall latency | 643.25 ms |

## Live Budget Matrix Pass

Date: 2026-06-11

The live sidecar was started as a fresh isolated `zoe-hindsight-budget-bakeoff` container using the same local Gemma and cached local BGE configuration. The runner retained the four synthetic events, waited for all four parent operations to complete, then measured both low and mid recall budgets in one pass:

```bash
HINDSIGHT_ENABLED=true \
HINDSIGHT_BASE_URL=http://127.0.0.1:8888 \
HINDSIGHT_API_LLM_PROVIDER=openai \
HINDSIGHT_API_LLM_BASE_URL=http://127.0.0.1:11434/v1 \
HINDSIGHT_API_EMBEDDINGS_PROVIDER=local \
PYTHONPATH=services/zoe-data \
python3 scripts/maintenance/hindsight_bakeoff.py --retain-synthetic --retain-timeout-seconds 240 --recall-budget low,mid --json
```

Aggregate result:

| Metric | Value |
| --- | --- |
| Synthetic events retained | 4 |
| Retain parent operations completed | 4/4 |
| Recall cases | 8 |
| Average score | 1.0 |
| Minimum score | 1.0 |
| p50 recall latency | 513.95 ms |
| p95 recall latency | 657.52 ms |
| Hot-path status | `async_or_cached_only` |

Budget breakdown:

| Recall Budget | Cases | Average Score | p50 Latency | p95 Latency | Hot-Path Status |
| --- | --- | --- | --- | --- | --- |
| `low` | 4 | 1.0 | 553.22 ms | 647.14 ms | `async_or_cached_only` |
| `mid` | 4 | 1.0 | 510.21 ms | 637.34 ms | `async_or_cached_only` |

Resource observation at cleanup: the isolated sidecar sampled about 936.9 MiB memory and about 4.59% CPU, then it was removed.

Resource observations:

| Observation | Value |
| --- | --- |
| Startup worker RSS | about 752 MB |
| Active retain sample | about 970 MiB container memory, about 2% CPU at sample time |
| Idle after bake-off | about 981.5 MiB container memory, about 0.46% CPU at sample time |
| Recall engine timings | internal warm recall completions were about 0.316-0.574 s before client overhead |

Residual findings:

- Offline viability is proven for the synthetic Hindsight path: no cloud LLM or cloud embedding provider was required.
- Accuracy is strong on the current synthetic fixture: 4/4 recall cases scored 1.0.
- Hindsight does not yet clear the strict hot-path latency gate because p95 recall stayed above the 600 ms target on live, warm, low-budget, and mid-budget runs.
- Hindsight should therefore remain a sidecar/async or cached recall candidate until latency is improved, cached prompt packets are measured, or the routing budget is explicitly relaxed.
- The bake-off used an isolated embedded PostgreSQL instance inside the Hindsight container; it did not write Zoe production memory tables.
- Hindsight emitted maintenance warnings for some embedded database helper functions during startup, but the health check, retain operations, consolidation, and recall completed.

## Acceptance Use

Hindsight should not move into production recall until a measured sidecar run proves:

- recall p95 under 600 ms for normal low/mid budget use, or an explicit async-only designation;
- returned memories include evidence/source pointers;
- retain can run async without blocking chat;
- wrong, disputed, or superseded memories can be corrected;
- user/scope isolation passes;
- no cloud model provider is required;
- Jetson CPU/RAM impact is acceptable or the service is marked sidecar/remote-only.
